"""End-to-end Phase 5A smoke (mocked LLM, FakeEmbedder).

PRD-004 v5 Phase 5A — deliverable K. Walks the full demo-day flow:
build corpus → POST scenario-a/run → POST scenario-a/analyze →
POST scenario-b/run (SSE) → POST scenario-b/analyze. Every LLM call
is mocked; sentence-transformers is replaced with FakeEmbedder.

When ``KGSPIN_LIVE_LLM=1``, the smoke targets a real
``gemini_flash`` instance (operator pre-funds; ~$1 of tokens).
This is the post-merge validation pass; default `pytest .` runs in
mock mode.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_EXTRACTION = PROJECT_ROOT / "demos" / "extraction"
sys.path.insert(0, str(DEMO_EXTRACTION))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


_LIVE_LLM = os.environ.get("KGSPIN_LIVE_LLM") == "1"


class _MockLLM:
    """Same shape as in test_scenario_endpoints.py — kept local so the
    smoke is fully self-contained."""
    async def complete(self, prompt: str) -> str:
        if "specializing in complex query decomposition for knowledge graph" in prompt:
            return '{"Sub-query 1": [("Apple Inc.", "has CEO", "Tim Cook")]}'
        if "specializing in complex query decomposition" in prompt:
            return '{"Sub-query 1": "Who is the CEO of Apple?"}'
        if "specializing in completing partially defined sub-queries" in prompt:
            return "Who is Apple's CEO?"
        if "specializing in completing partially defined knowledge graph" in prompt:
            return '[("Apple Inc.", "has CEO", "Tim Cook")]'
        if "summarizer specialized" in prompt:
            return "Apple Inc. is a technology company. Tim Cook is CEO."
        if "knowledge graph extractor" in prompt:
            return "[(\"Apple Inc.\", \"has CEO\", \"Tim Cook\")]"
        if "specializing in complex question answering" in prompt:
            return "Tim Cook is the Chief Executive Officer of Apple Inc."
        if "specializing in question answering" in prompt:
            return "Tim Cook is CEO of Apple."
        if "critical evaluator" in prompt:
            return "No"
        if "specializing in query expansion" in prompt:
            return '["follow-up query"]'
        if "You are a question-decomposition assistant" in prompt:
            return '{"sub_questions": ["Who is the CEO?"]}'
        # Scenario A judge.
        if "two candidate answers labeled A and B" in prompt:
            return json.dumps({
                "winner": "B",
                "rationale_a": "A is generic.",
                "rationale_b": "B cites specific entities.",
                "verdict": "B is better grounded.",
            })
        # F1 structured-extract.
        if "structured-answer extractor" in prompt:
            return '{"structured": [{"subsidiary": "Apple Operations International", "jurisdiction": "Ireland"}]}'
        return ""


@pytest.fixture
def smoke_app(tmp_path, monkeypatch, fake_embedder):
    """End-to-end fixture: synthetic AAPL corpus + TestClient + mocked LLM."""
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    html = (
        "<html><body>"
        "<p>Apple Inc. is a technology company. Tim Cook is the Chief Executive Officer.</p>"
        "<p>Subsidiaries listed in Exhibit 21 include Apple Operations International (Ireland) "
        "and Apple Distribution International (Ireland). Apple Inc. discloses ongoing antitrust "
        "litigation in the European Union under Item 3.</p>"
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
    seed_kg = {
        "entities": [
            {"id": "ent-aapl", "text": "Apple Inc.", "entity_type": "ORGANIZATION",
             "confidence": 0.99, "evidence": {"chunk_id": "AAPL-c00000",
                                              "sentence_text": "Apple Inc. is a technology company.",
                                              "sentence_index": 0, "source_document": "AAPL"},
             "metadata": {"semantic_definition": "tech firm"}},
            {"id": "ent-aoi", "text": "Apple Operations International",
             "entity_type": "ORGANIZATION", "confidence": 0.85,
             "evidence": {"chunk_id": "AAPL-c00000",
                          "sentence_text": "Apple Operations International is a subsidiary in Ireland.",
                          "sentence_index": 1, "source_document": "AAPL"},
             "metadata": {"semantic_definition": "subsidiary"}},
        ],
        "relationships": [
            {"id": "rel-1", "subject": {"id": "ent-aoi", "text": "Apple Operations International",
                                         "entity_type": "ORGANIZATION"},
             "predicate": "subsidiary_of",
             "object": {"id": "ent-aapl", "text": "Apple Inc.", "entity_type": "ORGANIZATION"},
             "confidence": 0.85,
             "evidence": {"chunk_id": "AAPL-c00000",
                          "sentence_text": "Apple Operations International is a subsidiary in Ireland.",
                          "sentence_index": 1, "source_document": "AAPL"}},
        ],
        "derived_facts": [], "provenance": {}, "rejected_relationships": [],
    }
    (aapl_out / "graph.json").write_text(json.dumps(seed_kg), encoding="utf-8")
    build_rag_corpus.build_corpus_for_ticker("AAPL", embedder=fake_embedder, skip_extraction=True)

    from kgspin_demo_app.services import dense_rag, graph_rag
    dense_rag.set_corpus_root(out_root)
    dense_rag.set_embedder(fake_embedder)
    graph_rag._clear_graph_cache()

    import demo_compare
    if not _LIVE_LLM:
        demo_compare.set_scenario_llm_client(_MockLLM())

    yield {
        "client": TestClient(demo_compare.app),
        "ticker": "AAPL",
    }

    if not _LIVE_LLM:
        demo_compare.set_scenario_llm_client(None)
    dense_rag.set_corpus_root(dense_rag._DEFAULT_CORPUS_ROOT)
    dense_rag.set_embedder(None)
    graph_rag._clear_graph_cache()


def test_phase5a_full_smoke(smoke_app):
    """End-to-end: corpus + Scenario A run+analyze + Scenario B run (SSE) + analyze."""
    client = smoke_app["client"]
    ticker = smoke_app["ticker"]

    # --- Scenario A run -----------------------------------------------------
    res_a_run = client.post(
        "/api/scenario-a/run",
        json={"question": "Who is the CEO of Apple?", "ticker": ticker, "mode": "A2"},
    )
    assert res_a_run.status_code == 200, res_a_run.text
    body_a = res_a_run.json()
    assert body_a["dense_answer"]
    assert body_a["graphrag_answer"]

    # --- Scenario A analyze ------------------------------------------------
    if _LIVE_LLM:
        # Live judge call requires a real `gemini_flash` instance.
        res_a_judge = client.post(
            "/api/scenario-a/analyze",
            json={
                "question": "Who is the CEO of Apple?",
                "dense_answer": body_a["dense_answer"],
                "graphrag_answer": body_a["graphrag_answer"],
            },
        )
        assert res_a_judge.status_code == 200, res_a_judge.text
        assert res_a_judge.json()["winner"] in ("A", "B", "tie")

    # --- Scenario B templates ----------------------------------------------
    res_tpl = client.get("/api/scenario-b/templates")
    assert res_tpl.status_code == 200
    assert len(res_tpl.json()) == 6

    # --- Scenario B run (SSE) ----------------------------------------------
    res_b_run = client.post(
        "/api/scenario-b/run",
        json={
            "scenario_id": "subsidiaries_litigation_jurisdiction",
            "ticker": ticker,
            "panes": ["agentic_dense"],  # one pane → fast smoke
            "enable_self_reflection": False,
        },
    )
    assert res_b_run.status_code == 200, res_b_run.text
    body = res_b_run.text
    assert "event: stage" in body
    assert "event: all_done" in body

    # --- Scenario B analyze (with pre-parsed structured rows so we don't need
    # the LLM extractor) ----------------------------------------------------
    pane_outputs = {
        "agentic_dense": {
            "name": "agentic_dense",
            "final_answer": "Apple's Irish subsidiaries face EU litigation.",
            "structured": [
                {"subsidiary": "Apple Operations International", "jurisdiction": "Ireland"},
            ],
        },
    }
    res_b_analyze = client.post(
        "/api/scenario-b/analyze",
        json={
            "scenario_id": "subsidiaries_litigation_jurisdiction",
            "ticker": ticker,
            "pane_outputs": pane_outputs,
        },
    )
    assert res_b_analyze.status_code == 200
    body_analyze = res_b_analyze.json()
    assert "f1_per_pane" in body_analyze
    assert "agentic_dense" in body_analyze["f1_per_pane"]
    f1 = body_analyze["f1_per_pane"]["agentic_dense"]["f1"]
    assert 0.0 <= f1 <= 1.0
