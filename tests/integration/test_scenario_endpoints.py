"""Integration tests for the PRD-004 v5 Phase 5A endpoints (deliverable H).

Token spend: $0 — every LLM call is dependency-injected with a mock
client. The corpus build is a pre-seeded synthetic AAPL fixture (no
sentence-transformers model load; no fan_out extraction).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_EXTRACTION = PROJECT_ROOT / "demos" / "extraction"
sys.path.insert(0, str(DEMO_EXTRACTION))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


@pytest.fixture
def synthetic_aapl_corpus(tmp_path, monkeypatch, fake_embedder):
    """Build a synthetic AAPL corpus for the endpoint tests."""
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    html = (
        "<html><body>"
        "<p>Apple Inc. is a technology company. Tim Cook is CEO. "
        "Apple Operations International is a subsidiary in Ireland.</p>"
        "<p>The Company reports antitrust litigation in the European Union under Item 3.</p>"
        "</body></html>"
    ) * 4
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

    build_rag_corpus.build_corpus_for_ticker(
        "AAPL", embedder=fake_embedder, skip_extraction=True,
    )

    from kgspin_demo_app.services import dense_rag, graph_rag
    dense_rag.set_corpus_root(out_root)
    dense_rag.set_embedder(fake_embedder)
    graph_rag._clear_graph_cache()

    yield "AAPL"

    dense_rag.set_corpus_root(dense_rag._DEFAULT_CORPUS_ROOT)
    dense_rag.set_embedder(None)
    graph_rag._clear_graph_cache()


@pytest.fixture
def app_client(synthetic_aapl_corpus):
    import demo_compare
    return TestClient(demo_compare.app)


# ---------------------------------------------------------------------------
# Mock LLMs
# ---------------------------------------------------------------------------


class _MockLLM:
    """Async LLMClient that returns canned strings keyed by prompt-marker."""

    def __init__(self):
        self.calls: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        # Decomposition.
        if "specializing in complex query decomposition for knowledge graph" in prompt:
            return '{"Sub-query 1": [("Apple Inc.", "has CEO", "Entity#1")]}'
        if "specializing in complex query decomposition" in prompt:
            return '{"Sub-query 1": "Who is Apple CEO?"}'
        if "specializing in completing partially defined sub-queries" in prompt:
            return "Who is the CEO of Apple Inc.?"
        if "specializing in completing partially defined knowledge graph" in prompt:
            return '[("Apple Inc.", "has CEO", "Tim Cook")]'
        if "summarizer specialized in extracting" in prompt:
            return "Apple Inc. is a technology company. Tim Cook is CEO."
        if "knowledge graph extractor" in prompt:
            return "[(\"Apple Inc.\", \"has CEO\", \"Tim Cook\")]"
        if "specializing in complex question answering" in prompt:
            return "Tim Cook is the CEO of Apple Inc."
        if "specializing in question answering" in prompt:
            return "Tim Cook is CEO."
        if "critical evaluator" in prompt:
            return "No"
        if "specializing in query expansion" in prompt:
            return '["follow-up query"]'
        # Decomposition prompts for agentic_dense_rag (different shape).
        if "You are a question-decomposition assistant" in prompt:
            return '{"sub_questions": ["Who is the CEO?"]}'
        return ""


# ---------------------------------------------------------------------------
# Tests — Scenario A
# ---------------------------------------------------------------------------


def test_scenario_a_run_returns_both_panes(app_client):
    import demo_compare
    demo_compare.set_scenario_llm_client(_MockLLM())
    try:
        resp = app_client.post(
            "/api/scenario-a/run",
            json={
                "question": "Who is the CEO of Apple?",
                "ticker": "AAPL",
                "mode": "A2",
            },
        )
    finally:
        demo_compare.set_scenario_llm_client(None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "A2"
    assert body["question"] == "Who is the CEO of Apple?"
    assert body["dense_answer"]
    assert body["graphrag_answer"]
    assert "[TEXT CHUNKS]" in body["retrieved_context_left"]
    assert "[TEXT CHUNKS]" in body["retrieved_context_right"] or "[GRAPH NODES]" in body["retrieved_context_right"]


def test_scenario_a_run_rejects_invalid_mode(app_client):
    resp = app_client.post(
        "/api/scenario-a/run",
        json={"question": "x?", "ticker": "AAPL", "mode": "Z9"},
    )
    assert resp.status_code == 400


def test_scenario_a_run_rejects_missing_fields(app_client):
    resp = app_client.post("/api/scenario-a/run", json={"mode": "A1"})
    assert resp.status_code == 400


def test_scenario_a_run_returns_503_when_corpus_missing(app_client):
    resp = app_client.post(
        "/api/scenario-a/run",
        json={"question": "x?", "ticker": "ZZZZ", "mode": "A1"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "corpus_not_built"


# ---------------------------------------------------------------------------
# Tests — Scenario B templates + analyze (run is SSE, tested separately)
# ---------------------------------------------------------------------------


def test_scenario_b_templates_returns_six(app_client):
    resp = app_client.get("/api/scenario-b/templates")
    assert resp.status_code == 200
    out = resp.json()
    assert len(out) == 6
    ids = {t["scenario_id"] for t in out}
    assert "subsidiaries_litigation_jurisdiction" in ids
    assert "stelara_adverse_events_cohort_v5" in ids
    # Forward-compat: each template has key_fields (F1 input).
    assert all("key_fields" in t and t["key_fields"] for t in out)


def test_scenario_b_analyze_with_pre_parsed_structured(app_client):
    """Test bypasses LLM extraction by passing structured rows directly."""
    # AAPL × subsidiaries_litigation_jurisdiction has 2 gold rows
    # ((Apple Operations International, Ireland), (Apple Distribution International, Ireland)).
    pane_outputs = {
        "agentic_dense": {
            "name": "agentic_dense",
            "final_answer": "Apple's Irish subsidiaries face EU litigation.",
            "structured": [  # one match, one miss
                {"subsidiary": "Apple Operations International", "jurisdiction": "Ireland"},
                {"subsidiary": "Apple Bogus Subsidiary", "jurisdiction": "Mars"},
            ],
        },
        "paper_mirror": {
            "name": "paper_mirror",
            "final_answer": "Both Irish subsidiaries face EU antitrust proceedings.",
            "structured": [  # both match
                {"subsidiary": "Apple Operations International", "jurisdiction": "Ireland"},
                {"subsidiary": "Apple Distribution International", "jurisdiction": "Ireland"},
            ],
        },
    }
    resp = app_client.post(
        "/api/scenario-b/analyze",
        json={
            "scenario_id": "subsidiaries_litigation_jurisdiction",
            "ticker": "AAPL",
            "pane_outputs": pane_outputs,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "f1_per_pane" in body
    assert "agentic_dense" in body["f1_per_pane"]
    assert "paper_mirror" in body["f1_per_pane"]
    # paper_mirror should score higher than agentic_dense (more matches).
    assert body["f1_per_pane"]["paper_mirror"]["f1"] > body["f1_per_pane"]["agentic_dense"]["f1"]
    # paper_mirror with both gold rows = perfect F1.
    assert body["f1_per_pane"]["paper_mirror"]["f1"] == 1.0


def test_scenario_b_analyze_returns_recovery_narrative_on_low_f1(app_client):
    """When any pane scores F1 < 0.3, the gold's narrative_recovery surfaces."""
    pane_outputs = {
        "agentic_dense": {
            "final_answer": "wrong",
            "structured": [{"subsidiary": "Wrong Co", "jurisdiction": "Nowhere"}],
        },
    }
    resp = app_client.post(
        "/api/scenario-b/analyze",
        json={
            "scenario_id": "subsidiaries_litigation_jurisdiction",
            "ticker": "AAPL",
            "pane_outputs": pane_outputs,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "recovery_narrative" in body
    assert "section-crossing" in body["recovery_narrative"]


def test_scenario_b_analyze_no_gold_returns_qualitative_only(app_client):
    """When gold isn't available for the ticker, analyze falls back gracefully."""
    pane_outputs = {
        "agentic_dense": {"final_answer": "anything", "structured": []},
    }
    resp = app_client.post(
        "/api/scenario-b/analyze",
        json={
            "scenario_id": "subsidiaries_litigation_jurisdiction",
            "ticker": "MSFT",  # no gold for this combo
            "pane_outputs": pane_outputs,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["f1_per_pane"] == {}
    assert "No gold available" in body["recovery_narrative"]


# ---------------------------------------------------------------------------
# Tests — Scenario A analyze (judge mock)
# ---------------------------------------------------------------------------


def test_scenario_a_analyze_returns_verdict(app_client, monkeypatch):
    """Mock the rank_two judge so $0 dev sprint stays $0."""
    import judge

    class _FakeBackend:
        def complete(self, prompt, **kwargs):
            class R: pass
            r = R()
            r.text = json.dumps({
                "winner": "B",
                "rationale_a": "A is hedgy.",
                "rationale_b": "B cites the graph.",
                "verdict": "B is more grounded.",
            })
            return r

    monkeypatch.setattr(
        judge, "resolve_llm_backend",
        lambda **kw: _FakeBackend(),
    )

    resp = app_client.post(
        "/api/scenario-a/analyze",
        json={
            "question": "Q?",
            "dense_answer": "A: dense answer text.",
            "graphrag_answer": "B: graphrag answer text.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"] == "B"
    assert body["rationale_a"]
    assert body["rationale_b"]


def test_scenario_a_analyze_rejects_missing_fields(app_client):
    resp = app_client.post("/api/scenario-a/analyze", json={"question": "Q?"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests — Scenario B SSE
# ---------------------------------------------------------------------------


def test_scenario_b_run_streams_sse_with_all_done(app_client):
    """SSE stream emits stage events and terminates with all_done."""
    import demo_compare
    demo_compare.set_scenario_llm_client(_MockLLM())
    try:
        resp = app_client.post(
            "/api/scenario-b/run",
            json={
                "scenario_id": "subsidiaries_litigation_jurisdiction",
                "ticker": "AAPL",
                "panes": ["agentic_dense"],  # one pane to keep test fast
                "enable_self_reflection": False,
            },
        )
    finally:
        demo_compare.set_scenario_llm_client(None)
    assert resp.status_code == 200
    body = resp.text
    assert "event: stage" in body
    assert "event: all_done" in body
    # Final all_done payload contains the resolved question.
    assert "subsidiaries listed in Exhibit 21" in body


def test_scenario_b_run_tool_agent_emits_stage_error(app_client):
    """Forward-compat: tool_agent in panes returns a stage_error event in 5A."""
    import demo_compare
    demo_compare.set_scenario_llm_client(_MockLLM())
    try:
        resp = app_client.post(
            "/api/scenario-b/run",
            json={
                "scenario_id": "subsidiaries_litigation_jurisdiction",
                "ticker": "AAPL",
                "panes": ["tool_agent"],
            },
        )
    finally:
        demo_compare.set_scenario_llm_client(None)
    assert resp.status_code == 200
    body = resp.text
    assert "event: stage_error" in body
    assert "tool_agent" in body
    assert "Phase 5B" in body
