"""Unit tests for the paper-mirror GraphSearch pipeline.

PRD-004 v5 Phase 5A — deliverable E. Mocks LLM responses;
exercises every documented stage including verification +
expansion paths.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_rag_corpus import build_corpus_for_ticker  # noqa: E402

from kgspin_demo_app.services import (  # noqa: E402
    dense_rag, graph_rag, graphsearch_pipeline,
)
from kgspin_demo_app.services._graphsearch_components import (  # noqa: E402
    parse_expanded_queries, parse_subquery_kg, parse_subquery_text, normalize,
    format_history_context,
)


# ---------------------------------------------------------------------------
# Mock LLM (records every prompt; routes by prompt-marker substring)
# ---------------------------------------------------------------------------


class RecordingLLM:
    """Returns canned responses keyed on prompt-marker substrings.

    Default responses chosen so the verification-positive ("yes")
    path fires unless overridden.
    """

    def __init__(self, *, verification_yes: bool = True):
        self.calls: list[str] = []
        self._sub_idx = 0
        self._verif_idx = 0
        self.verification_yes = verification_yes

    async def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "specializing in complex query decomposition for knowledge graph" in prompt:
            return json.dumps({
                "Sub-query 1": [["Apple Inc.", "has CEO", "Entity#1"]],
                "Sub-query 2": [["Apple Inc.", "has subsidiary", "Entity#2"]],
            }).replace("[[", "[(").replace("]]", ")]")
            # Note: the paper expects ``[("a", "b", "c"), ...]`` triples; we emit
            # square-bracket-Python-style which the parse_subquery_kg regex
            # ``"Sub-query \d+":\s*(\[[^\]]+\])`` matches as long as the bracketed
            # block is present.
        if "specializing in complex query decomposition" in prompt:
            return (
                '{\n'
                '    "Sub-query 1": "Who is the CEO of Apple?",\n'
                '    "Sub-query 2": "Where is Apple\'s #1 based?"\n'
                '}\n'
            )
        if "specializing in completing partially defined sub-queries" in prompt:
            return "Where is Apple's CEO based?"
        if "specializing in completing partially defined knowledge graph sub-queries" in prompt:
            return '[("Apple Inc.", "has subsidiary", "Apple Operations International")]'
        if "summarizer specialized in extracting relevant evidence" in prompt:
            return "Text-summary placeholder."
        if "knowledge graph extractor specialized in identifying" in prompt:
            return "[(\"Apple Inc.\", \"has CEO\", \"Tim Cook\")]"
        if "specializing in complex question answering" in prompt:
            # answer_generation_deep — used for text/kg/final drafts.
            return "Deep answer placeholder."
        if "specializing in question answering" in prompt:
            # answer_generation — for sub-queries.
            return "Sub-query answer placeholder."
        if "critical evaluator specializing in verifying" in prompt:
            self._verif_idx += 1
            return "Yes" if self.verification_yes else "No"
        if "specializing in query expansion" in prompt:
            return '["Followup expansion query 1", "Followup expansion query 2"]'
        return ""


# ---------------------------------------------------------------------------
# Corpus fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_aapl(tmp_path, monkeypatch, fake_embedder):
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    html = (
        "<html><body>"
        "<p>Apple Inc. is a technology company. Tim Cook is the CEO.</p>"
        "<p>Apple Operations International is an Irish subsidiary.</p>"
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
            {
                "id": "ent-aapl",
                "text": "Apple Inc.",
                "entity_type": "ORGANIZATION",
                "confidence": 0.99,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Apple Inc. is a technology company.",
                    "sentence_index": 0,
                    "source_document": "AAPL",
                },
                "metadata": {"semantic_definition": "tech firm"},
            },
            {
                "id": "ent-tim",
                "text": "Tim Cook",
                "entity_type": "PERSON",
                "confidence": 0.95,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Tim Cook is the CEO.",
                    "sentence_index": 1,
                    "source_document": "AAPL",
                },
                "metadata": {},
            },
        ],
        "relationships": [
            {
                "id": "rel-1",
                "subject": {"id": "ent-tim", "text": "Tim Cook", "entity_type": "PERSON"},
                "predicate": "ceo_of",
                "object": {"id": "ent-aapl", "text": "Apple Inc.", "entity_type": "ORGANIZATION"},
                "confidence": 0.95,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Tim Cook is the CEO.",
                    "sentence_index": 1,
                    "source_document": "AAPL",
                },
            },
        ],
        "derived_facts": [], "provenance": {}, "rejected_relationships": [],
    }
    (aapl_out / "graph.json").write_text(json.dumps(seed_kg), encoding="utf-8")

    build_corpus_for_ticker("AAPL", embedder=fake_embedder, skip_extraction=True)

    dense_rag.set_corpus_root(out_root)
    dense_rag.set_embedder(fake_embedder)
    graph_rag._clear_graph_cache()

    yield "AAPL"

    dense_rag.set_corpus_root(dense_rag._DEFAULT_CORPUS_ROOT)
    dense_rag.set_embedder(None)
    graph_rag._clear_graph_cache()


def _new_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


def test_parse_subquery_text_extracts_quoted_strings():
    raw = '{"Sub-query 1": "first question?", "Sub-query 2": "second?"}'
    assert parse_subquery_text(raw) == ["first question?", "second?"]


def test_parse_subquery_kg_extracts_bracket_blocks():
    raw = '{"Sub-query 1": [("a", "b", "c")], "Sub-query 2": [("d", "e", "f")]}'
    out = parse_subquery_kg(raw)
    assert len(out) == 2
    assert out[0].startswith("[")


def test_parse_expanded_queries_handles_python_list():
    raw = '["query 1", "query 2"]'
    assert parse_expanded_queries(raw) == ["query 1", "query 2"]


def test_parse_expanded_queries_extracts_from_extra_text():
    raw = 'Here are the queries:\n["q1", "q2"]\nGood luck.'
    assert parse_expanded_queries(raw) == ["q1", "q2"]


def test_normalize_returns_lowercase_token_list():
    assert normalize("The Yes Indeed.") == ["yes", "indeed"]


def test_format_history_context_serializes_tuples():
    hist = [("q1?", "summary1", "answer1"), ("q2?", "summary2", "answer2")]
    out = format_history_context(hist)
    assert "Sub-query 1: q1?" in out
    assert "Sub-query 2: q2?" in out
    assert "Sub-query answer: answer1" in out


# ---------------------------------------------------------------------------
# End-to-end mocked pipeline tests
# ---------------------------------------------------------------------------


def test_run_full_pipeline_with_self_reflection_on(synthetic_aapl):
    llm = RecordingLLM(verification_yes=True)
    loop = _new_event_loop()
    try:
        result = loop.run_until_complete(
            graphsearch_pipeline.run(
                synthetic_aapl, "Who runs Apple and where is its subsidiary?",
                llm=llm, enable_self_reflection=True,
            )
        )
    finally:
        loop.close()

    assert isinstance(result, graphsearch_pipeline.GraphSearchResult)
    assert result.final_answer == "Deep answer placeholder."
    # Both channels populated.
    assert len(result.text_channel_history) >= 1
    assert len(result.kg_channel_history) >= 1
    # Verification fired (verification_yes=True).
    assert result.text_evidence_verification.lower().startswith("yes")
    # Expansion fired because verification=Yes.
    assert result.expansion_used is True
    # Multiple retrievals (seed + each sub-query + each expansion).
    assert result.retrieval_count >= 3


def test_self_reflection_off_skips_verification_and_expansion(synthetic_aapl):
    llm = RecordingLLM(verification_yes=True)
    loop = _new_event_loop()
    try:
        result = loop.run_until_complete(
            graphsearch_pipeline.run(
                synthetic_aapl, "Question?",
                llm=llm, enable_self_reflection=False,
            )
        )
    finally:
        loop.close()
    # No verification result captured.
    assert result.text_evidence_verification == ""
    assert result.kg_evidence_verification == ""
    assert result.expansion_used is False
    # No verification timing recorded.
    assert "text_verification_ms" not in result.stage_timings_ms
    assert "kg_verification_ms" not in result.stage_timings_ms


def test_verification_no_skips_expansion(synthetic_aapl):
    llm = RecordingLLM(verification_yes=False)
    loop = _new_event_loop()
    try:
        result = loop.run_until_complete(
            graphsearch_pipeline.run(
                synthetic_aapl, "Question?",
                llm=llm, enable_self_reflection=True,
            )
        )
    finally:
        loop.close()
    # Verification ran but said "No".
    assert result.text_evidence_verification == "No"
    # Therefore no expansion.
    assert "text_expansion_ms" not in result.stage_timings_ms
    assert result.expansion_used is False


def test_progress_cb_fires_at_documented_stages(synthetic_aapl):
    events: list[tuple[str, dict]] = []
    llm = RecordingLLM(verification_yes=True)
    loop = _new_event_loop()
    try:
        loop.run_until_complete(
            graphsearch_pipeline.run(
                synthetic_aapl, "Question?",
                llm=llm,
                progress_cb=lambda s, p: events.append((s, p)),
            )
        )
    finally:
        loop.close()
    stages = [s for s, _ in events]
    expected_stages = {
        "seed_retrieval_done", "dual_summary_done", "decomposition_done",
        "text_draft_done", "text_verification_done", "text_expansion_done",
        "kg_draft_done", "kg_verification_done", "kg_expansion_done",
        "merge_done",
    }
    missing = expected_stages - set(stages)
    assert not missing, f"missing stages: {missing}"


def test_pipeline_serialized_bundles_carry_section_markers(synthetic_aapl):
    """Every prompt receives a bundle string with [TEXT CHUNKS] / [GRAPH NODES]
    / [GRAPH EDGES] markers (or a subset when one is empty)."""
    llm = RecordingLLM(verification_yes=False)
    loop = _new_event_loop()
    try:
        loop.run_until_complete(
            graphsearch_pipeline.run(
                synthetic_aapl, "Question?", llm=llm,
                enable_self_reflection=False,
            )
        )
    finally:
        loop.close()
    # text_summary / kg_summary prompts should contain the section markers
    # from their context_data substitution.
    summary_prompts = [
        c for c in llm.calls
        if "summarizer specialized in extracting relevant evidence" in c
    ]
    assert summary_prompts, "no text_summary prompts emitted"
    assert any("[TEXT CHUNKS]" in p for p in summary_prompts)
