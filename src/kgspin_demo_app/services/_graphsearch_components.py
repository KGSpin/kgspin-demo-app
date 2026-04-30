"""Component wrappers for the paper-mirror GraphSearch pipeline.

PRD-004 v5 Phase 5A — deliverable E. Mirrors:
    RAGSearch/GraphSearch/deepsearch/components.py
    RAGSearch/GraphSearch/utils.py (helper functions only)

Each component is a thin async wrapper around ``LLMClient.complete`` +
the matching prompt from ``_graphsearch_prompts.PROMPTS``. Helpers
(``format_history_context``, ``extract_words_str``, ``normalize``,
``parse_expanded_queries``) are ported one-to-one — the pipeline
relies on their exact behavior.

LLM client is dependency-injected so tests pass a ``MockLLMClient``;
production binds to ``gemini_flash`` via the demo's ``llm_backend``.
"""
from __future__ import annotations

import ast
import re
import string
from typing import Protocol

from kgspin_demo_app.services._graphsearch_prompts import PROMPTS


class LLMClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Helpers (ported from RAGSearch/GraphSearch/utils.py)
# ---------------------------------------------------------------------------


def format_history_context(history: list[tuple[str, str, str]]) -> str:
    """Sub-query / retrieved-context-summary / sub-query-answer history."""
    out_lines: list[str] = []
    for i, (q, ctx_sum, a) in enumerate(history, start=1):
        out_lines.append(
            f"Sub-query {i}: {q}\nRetrieved context:\n{ctx_sum}\nSub-query answer: {a}\n"
        )
    return "\n".join(out_lines).strip()


def extract_words_str(text: str) -> str:
    """Strip non-letter chars; used to clean KG sub-query text before retrieval."""
    return " ".join(re.findall(r"[A-Za-z]+", text or ""))


def normalize(text: str) -> list[str]:
    """Paper's normalize(): lower-case, drop articles + punct, split on whitespace.

    Returns a list of tokens; the paper checks ``"yes" in normalize(...)``
    to detect verification-positive responses.
    """

    def remove_articles(t: str) -> str:
        return re.sub(r"\b(a|an|the)\b", " ", t)

    def white_space_fix(t: str) -> str:
        return " ".join(t.split())

    def remove_punc(t: str) -> str:
        return "".join(ch for ch in t if ch not in set(string.punctuation))

    return white_space_fix(
        remove_articles(remove_punc((text or "").lower()))
    ).split()


def parse_expanded_queries(query_expansion_result: str) -> list[str]:
    """Parse the LLM's ``[...]`` Python-list output. Tolerant: returns a
    single-element list of the raw text when neither parse path succeeds."""
    text = (query_expansion_result or "").strip()
    if not text:
        return []
    # Direct literal_eval.
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    except Exception:
        pass
    # Regex-extract a [...] block.
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            parsed = ast.literal_eval(m.group(0))
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return parsed
        except Exception:
            pass
    return [text]


def parse_subquery_text(decomposition_output: str) -> list[str]:
    r"""Extract text-channel sub-queries from ``query_decomposition_deep`` output.

    Mirrors the paper's ``re.findall(r'"Sub-query \d+":\s*"([^"]+)"', ...)``.
    """
    pattern = r'"Sub-query \d+":\s*"([^"]+)"'
    return re.findall(pattern, decomposition_output or "")


def parse_subquery_kg(decomposition_output: str) -> list[str]:
    r"""Extract KG-channel sub-queries (the bracketed-triples list) from
    ``query_decomposition_deep_kg`` output.

    Mirrors the paper's ``re.findall(r'"Sub-query \d+":\s*(\[[^\]]+\])', ...)``.
    """
    pattern = r'"Sub-query \d+":\s*(\[[^\]]+\])'
    return re.findall(pattern, decomposition_output or "")


# ---------------------------------------------------------------------------
# LLM-call components (one async function per prompt)
# ---------------------------------------------------------------------------


async def _safe_complete(llm: LLMClient, prompt: str) -> str:
    """Wrap llm.complete with the paper's ``except: return ""`` semantics."""
    try:
        out = await llm.complete(prompt)
        return (out or "").strip()
    except Exception:
        return ""


async def question_decomposition_deep(llm: LLMClient, query: str) -> str:
    return await _safe_complete(llm, PROMPTS["query_decomposition_deep"].format(query=query))


async def question_decomposition_deep_kg(llm: LLMClient, query: str) -> str:
    return await _safe_complete(llm, PROMPTS["query_decomposition_deep_kg"].format(query=query))


async def query_completer(llm: LLMClient, sub_query: str, context_data: str) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["query_completer"].format(
            sub_query=sub_query, context_data=context_data,
        ),
    )


async def kg_query_completer(llm: LLMClient, sub_query: str, context_data: str) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["kg_query_completer"].format(
            sub_query=sub_query, context_data=context_data,
        ),
    )


async def text_summary(llm: LLMClient, query: str, context_data: str) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["retrieval_text_summarization"].format(
            query=query, context_data=context_data,
        ),
    )


async def kg_summary(llm: LLMClient, query: str, context_data: str) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["knowledge_graph_summarization"].format(
            query=query, context_data=context_data,
        ),
    )


async def answer_generation(llm: LLMClient, query: str, context_data: str) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["answer_generation"].format(
            query=query, context_data=context_data,
        ),
    )


async def answer_generation_deep(llm: LLMClient, query: str, context_data: str) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["answer_generation_deep"].format(
            query=query, context_data=context_data,
        ),
    )


async def evidence_verification(
    llm: LLMClient, query: str, context_data: str, model_response: str,
) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["evidence_verification"].format(
            query=query, context_data=context_data, model_response=model_response,
        ),
    )


async def query_expansion(
    llm: LLMClient,
    query: str,
    context_data: str,
    model_response: str,
    evidence_verification_result: str,
) -> str:
    return await _safe_complete(
        llm,
        PROMPTS["query_expansion"].format(
            query=query,
            context_data=context_data,
            model_response=model_response,
            evidence_verification=evidence_verification_result,
        ),
    )


__all__ = [
    "LLMClient",
    "format_history_context",
    "extract_words_str",
    "normalize",
    "parse_expanded_queries",
    "parse_subquery_text",
    "parse_subquery_kg",
    "question_decomposition_deep",
    "question_decomposition_deep_kg",
    "query_completer",
    "kg_query_completer",
    "text_summary",
    "kg_summary",
    "answer_generation",
    "answer_generation_deep",
    "evidence_verification",
    "query_expansion",
]
