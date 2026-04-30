"""Unit tests for ``services/agentic_dense_rag``.

PRD-004 v5 Phase 5A — deliverable D. Mocks LLM responses; uses the
synthetic AAPL corpus from the dense_rag fixture so retrieval calls
return real (deterministic) chunks.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_rag_corpus import build_corpus_for_ticker  # noqa: E402

from kgspin_demo_app.services import agentic_dense_rag, dense_rag  # noqa: E402


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLMClient:
    """LLMClient with canned responses keyed by prompt prefix."""

    def __init__(
        self,
        decomposition: list[str],
        sub_answers: Optional[list[str]] = None,
        final_answer: str = "Final answer: synthesized.",
    ):
        self._decomp_payload = json.dumps({"sub_questions": decomposition})
        self._sub_answers = list(sub_answers or [
            f"Sub-answer to: {q}" for q in decomposition
        ])
        self._final = final_answer
        self.calls: list[str] = []  # ordered prompts received
        self._sub_idx = 0

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "You are a question-decomposition assistant" in prompt:
            return self._decomp_payload
        if "You are a question-answering assistant" in prompt and "Sub-question:" in prompt:
            sa_idx = self._sub_idx
            self._sub_idx += 1
            if sa_idx < len(self._sub_answers):
                return self._sub_answers[sa_idx]
            return f"Default sub-answer #{sa_idx + 1}"
        if "Main question:" in prompt and "Decomposition + retrieval history:" in prompt:
            return self._final
        return ""


# ---------------------------------------------------------------------------
# Corpus fixture (reused from test_dense_rag pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_aapl(tmp_path, monkeypatch, fake_embedder):
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    html = (
        "<html><body>"
        "<p>Apple Inc. is a technology company based in Cupertino, California. "
        "Tim Cook is the Chief Executive Officer.</p>"
        "<p>The Company faces antitrust litigation regarding the App Store in the European Union. "
        "Apple Operations International, an Irish subsidiary, holds intellectual property assets.</p>"
        "<p>Revenue for fiscal 2025 was $400 billion. The Company plans further share repurchases.</p>"
        "</body></html>"
    ) * 5
    (fin_dir / "raw.html").write_text(html, encoding="utf-8")
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(tmp_path))
    import build_rag_corpus
    monkeypatch.setattr(build_rag_corpus, "KGSPIN_CORPUS_ROOT", tmp_path)

    out_root = tmp_path / "rag-corpus"
    out_root.mkdir()
    monkeypatch.setattr(build_rag_corpus, "CORPUS_OUT_DIR", out_root)

    aapl_out = out_root / "AAPL"
    aapl_out.mkdir()
    (aapl_out / "graph.json").write_text(
        json.dumps({"entities": [], "relationships": []}),
        encoding="utf-8",
    )

    build_corpus_for_ticker("AAPL", embedder=fake_embedder, skip_extraction=True)

    dense_rag.set_corpus_root(out_root)
    dense_rag.set_embedder(fake_embedder)

    yield "AAPL"

    dense_rag.set_corpus_root(dense_rag._DEFAULT_CORPUS_ROOT)
    dense_rag.set_embedder(None)


def _new_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_answer_runs_full_pipeline(synthetic_aapl):
    llm = MockLLMClient(
        decomposition=["Who is the CEO of Apple?", "Where is Apple's subsidiary based?"],
        sub_answers=["Tim Cook is the CEO.", "Apple Operations International is in Ireland."],
        final_answer="Tim Cook leads Apple, which has an Irish subsidiary.",
    )

    loop = _new_event_loop()
    try:
        result = loop.run_until_complete(
            agentic_dense_rag.answer(synthetic_aapl, "Who runs Apple and where are its subsidiaries?", llm=llm)
        )
    finally:
        loop.close()

    assert result.final_answer.startswith("Tim Cook leads Apple")
    assert result.decomposition_trace == [
        "Who is the CEO of Apple?",
        "Where is Apple's subsidiary based?",
    ]
    assert len(result.retrieval_history) == 2
    assert all("chunk_ids" in step for step in result.retrieval_history)
    # 1 decomposition + 2 sub-answers + 1 final = 4 LLM calls.
    assert len(llm.calls) == 4


def test_answer_caps_iterations_at_max_steps(synthetic_aapl):
    """Decomposition emits 7 sub-questions; only first ``max_steps`` execute."""
    llm = MockLLMClient(
        decomposition=[f"Sub-Q {i}?" for i in range(7)],
    )
    loop = _new_event_loop()
    try:
        result = loop.run_until_complete(
            agentic_dense_rag.answer(
                synthetic_aapl, "Big question?", llm=llm, max_steps=5,
            )
        )
    finally:
        loop.close()
    assert len(result.decomposition_trace) == 5
    assert len(result.retrieval_history) == 5
    # 1 decomp + 5 sub-answers + 1 final = 7.
    assert len(llm.calls) == 7


def test_answer_with_empty_decomposition_falls_back_to_main_question(synthetic_aapl):
    """Empty decomposition → original question becomes the only sub-question."""
    llm = MockLLMClient(decomposition=[])
    loop = _new_event_loop()
    try:
        result = loop.run_until_complete(
            agentic_dense_rag.answer(synthetic_aapl, "What is Apple?", llm=llm)
        )
    finally:
        loop.close()
    assert result.decomposition_trace == ["What is Apple?"]
    assert len(result.retrieval_history) == 1


def test_progress_cb_fires_at_each_stage(synthetic_aapl):
    events: list[tuple[str, dict]] = []
    llm = MockLLMClient(decomposition=["sub-q 1?", "sub-q 2?"])
    loop = _new_event_loop()
    try:
        loop.run_until_complete(
            agentic_dense_rag.answer(
                synthetic_aapl, "Question?", llm=llm,
                progress_cb=lambda stage, payload: events.append((stage, payload)),
            )
        )
    finally:
        loop.close()
    stages = [s for s, _ in events]
    assert "decomposition_start" in stages
    assert "decomposition_done" in stages
    assert stages.count("sub_query_start") == 2
    assert stages.count("sub_query_done") == 2
    assert "final_answer_start" in stages
    assert "final_answer_done" in stages


def test_parse_decomposition_handles_code_fence_wrapping():
    raw = '```json\n{"sub_questions": ["a?", "b?"]}\n```'
    out = agentic_dense_rag._parse_decomposition(raw, max_steps=5)
    assert out == ["a?", "b?"]


def test_parse_decomposition_handles_extra_text():
    raw = 'Here you go:\n{"sub_questions": ["x?", "y?"]}\nlet me know!'
    out = agentic_dense_rag._parse_decomposition(raw, max_steps=5)
    assert out == ["x?", "y?"]


def test_parse_decomposition_falls_back_to_line_split():
    raw = "1. Who is X?\n2. Where is Y?"
    out = agentic_dense_rag._parse_decomposition(raw, max_steps=5)
    assert "Who is X?" in out
    assert "Where is Y?" in out


def test_parse_decomposition_empty_returns_empty():
    assert agentic_dense_rag._parse_decomposition("", max_steps=5) == []
    assert agentic_dense_rag._parse_decomposition("   ", max_steps=5) == []


def test_format_history_for_prompt_lists_each_step():
    history = [
        {"sub_question": "q1?", "context_text": "ctx1", "sub_answer": "a1"},
        {"sub_question": "q2?", "context_text": "ctx2", "sub_answer": "a2"},
    ]
    s = agentic_dense_rag._format_history_for_prompt(history)
    assert "Sub-question 1: q1?" in s
    assert "Sub-question 2: q2?" in s
    assert "Sub-answer: a1" in s
    assert "Sub-answer: a2" in s
