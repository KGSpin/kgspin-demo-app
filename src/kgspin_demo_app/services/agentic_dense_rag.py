"""Agentic dense RAG (Search-o1-style) — PRD-004 v5 Phase 5A deliverable D.

The "strong baseline" for Scenario B's left pane: an agent that
decomposes the multi-hop question into atomic sub-queries, retrieves
chunks for each via dense_rag, answers each, and stitches a final
answer from the accumulated history.

NOT graph-aware. The paper (RAGSearch / GraphSearch) calls this
"naive RAG with decomposition"; the demo's value-prop is that the
graph-mirror pipeline (deliverable E) beats this one.

LLM client is dependency-injected (``LLMClient`` Protocol) so the
unit tests pass a ``MockLLMClient`` with canned responses. Production
binds to ``gemini_flash`` via the demo's ``llm_backend`` factory
(wired in deliverable H endpoint code).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

from kgspin_demo_app.services import dense_rag
from kgspin_demo_app.services.dense_rag import Chunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM client protocol
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """The minimal LLM surface this service depends on.

    ``complete(prompt) -> str`` — deterministic-shape async call.
    Production implementation (deliverable H) wraps ``gemini_flash``;
    tests pass a ``MockLLMClient`` that returns canned strings.
    """

    async def complete(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Prompts (terse — sized for ~200-token decomposition output)
# ---------------------------------------------------------------------------


_DECOMPOSITION_PROMPT = """You are a question-decomposition assistant.

Given a complex question, break it into 2-5 atomic sub-questions, each
addressing exactly one entity or relationship. Return strictly valid
JSON, with this exact shape:

{{"sub_questions": ["<sub-question 1>", "<sub-question 2>", ...]}}

Rules:
- Each sub-question must be self-contained (no pronouns referring to
  prior sub-questions).
- Order sub-questions so each one's answer feeds the next when needed.
- Maximum 5 sub-questions.

Question: {question}

Output (JSON only):"""


_SUB_ANSWER_PROMPT = """You are a question-answering assistant. Use only the
retrieved context below; if the context is insufficient, say so explicitly.

Sub-question: {sub_question}

Retrieved context:
{context}

Answer (1-3 sentences, grounded in the context):"""


_FINAL_ANSWER_PROMPT = """You are a question-answering assistant. Use the
sub-question/answer history below to construct the final answer to the
main question. Cite sub-question numbers (e.g. "from #1, ..."), and do
NOT invent facts beyond the history.

Main question: {question}

Decomposition + retrieval history:
{history}

Final answer:"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgenticResult:
    final_answer: str
    decomposition_trace: list[str]
    retrieval_history: list[dict] = field(default_factory=list)
    sub_query_answers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_decomposition(raw: str, max_steps: int) -> list[str]:
    r"""Extract sub_questions from the LLM's decomposition output.

    Tolerant — accepts JSON blocks even when wrapped in triple-backticks,
    falls back to one-question-per-line splitting if JSON parsing
    fails. Hard-caps at ``max_steps`` per the bounded-iteration test.
    """
    text = (raw or "").strip()
    if not text:
        return []
    # Strip code fences.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: pluck the JSON object from the text via regex.
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
        else:
            parsed = None
    if isinstance(parsed, dict):
        sub_qs = parsed.get("sub_questions")
        if isinstance(sub_qs, list):
            cleaned = [str(q).strip() for q in sub_qs if str(q).strip()]
            return cleaned[:max_steps]
    # Last-resort: split on newlines, take first ``max_steps`` non-empty
    # lines that look like questions.
    candidates = [
        re.sub(r"^[\s\d\.\-\*]+", "", line).strip()
        for line in text.splitlines()
    ]
    candidates = [c for c in candidates if c and ("?" in c or len(c) > 10)]
    return candidates[:max_steps]


def _format_history_for_prompt(history: list[dict]) -> str:
    lines: list[str] = []
    for i, step in enumerate(history, start=1):
        lines.append(f"Sub-question {i}: {step['sub_question']}")
        lines.append(
            "Retrieved context:\n" + step.get("context_text", "")
        )
        lines.append("Sub-answer: " + step.get("sub_answer", ""))
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def answer(
    ticker: str,
    question: str,
    *,
    llm: LLMClient,
    max_steps: int = 5,
    top_k: int = 5,
    progress_cb: Optional[Callable[[str, dict], None]] = None,
) -> AgenticResult:
    """Search-o1-style agentic dense RAG.

    Steps:
        1. ``llm.complete(decompose-prompt)`` → sub-questions (≤ max_steps).
        2. For each sub-question:
           a. ``dense_rag.search(ticker, sub_q, top_k)`` → chunks.
           b. ``llm.complete(sub-answer-prompt)`` → sub-answer.
           c. Append to history.
        3. ``llm.complete(final-prompt)`` over the full history → final answer.
    """
    if progress_cb:
        progress_cb("decomposition_start", {"question": question})

    decomposition_raw = await llm.complete(
        _DECOMPOSITION_PROMPT.format(question=question)
    )
    sub_questions = _parse_decomposition(decomposition_raw, max_steps)
    if not sub_questions:
        # Fallback: treat the original question as the only sub-question.
        sub_questions = [question]
    if progress_cb:
        progress_cb("decomposition_done", {"sub_questions": sub_questions})

    retrieval_history: list[dict] = []
    sub_answers: list[str] = []

    for i, sub_q in enumerate(sub_questions):
        if progress_cb:
            progress_cb("sub_query_start", {"index": i, "sub_question": sub_q})

        # Retrieval.
        chunks = dense_rag.search(ticker, sub_q, top_k=top_k)
        context_text = dense_rag.serialize_chunks(chunks)
        if progress_cb:
            progress_cb(
                "sub_query_retrieved",
                {"index": i, "n_chunks": len(chunks)},
            )

        # Answer.
        sub_answer = await llm.complete(
            _SUB_ANSWER_PROMPT.format(
                sub_question=sub_q, context=context_text or "(no context retrieved)",
            )
        )
        sub_answer = (sub_answer or "").strip()
        sub_answers.append(sub_answer)

        retrieval_history.append({
            "index": i,
            "sub_question": sub_q,
            "context_text": context_text,
            "n_chunks": len(chunks),
            "sub_answer": sub_answer,
            "chunk_ids": [c.chunk_id for c in chunks],
        })
        if progress_cb:
            progress_cb(
                "sub_query_done",
                {"index": i, "sub_answer": sub_answer},
            )

    if progress_cb:
        progress_cb("final_answer_start", {})

    history_str = _format_history_for_prompt(retrieval_history)
    final_answer = await llm.complete(
        _FINAL_ANSWER_PROMPT.format(question=question, history=history_str)
    )
    final_answer = (final_answer or "").strip()

    if progress_cb:
        progress_cb("final_answer_done", {"final_answer": final_answer})

    return AgenticResult(
        final_answer=final_answer,
        decomposition_trace=sub_questions,
        retrieval_history=retrieval_history,
        sub_query_answers=sub_answers,
    )


__all__ = [
    "AgenticResult",
    "LLMClient",
    "answer",
]
