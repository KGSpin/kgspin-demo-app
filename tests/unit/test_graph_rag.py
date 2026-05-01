"""Unit tests for ``services/graph_rag``.

PRD-004 v5 Phase 5A — deliverable C.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_rag_corpus import build_corpus_for_ticker  # noqa: E402

from kgspin_demo_app.services import dense_rag, graph_rag  # noqa: E402


@pytest.fixture
def tiny_corpus(tmp_path, monkeypatch, fake_embedder):
    """Build a small AAPL corpus with hand-crafted graph nodes/edges."""
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    html = (
        "<html><body>"
        "<p>Apple Inc. is a technology company. Tim Cook is CEO. "
        "Apple Inc. makes iPhones and Macs.</p>"
        "<p>The Company faces antitrust litigation. App Store fees disputed in EU.</p>"
        "<p>Apple Operations International is a subsidiary in Ireland. "
        "Apple Distribution International is also based in Ireland.</p>"
        "</body></html>"
    ) * 5  # ensure we get multiple chunks
    (fin_dir / "raw.html").write_text(html, encoding="utf-8")
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(tmp_path))
    import build_rag_corpus
    monkeypatch.setattr(build_rag_corpus, "KGSPIN_CORPUS_ROOT", tmp_path)

    out_root = tmp_path / "rag-corpus"
    out_root.mkdir()
    monkeypatch.setattr(build_rag_corpus, "CORPUS_OUT_DIR", out_root)

    aapl_out = out_root / "AAPL"
    aapl_out.mkdir()

    # Pre-seed a tiny graph that has nodes whose parent_doc_offsets
    # overlap with the chunked source text.
    plaintext_dir = out_root / "AAPL"
    # Find offsets in the eventual source.txt by reading the input.
    raw_text = (
        "Apple Inc. is a technology company. Tim Cook is CEO. "
        "Apple Inc. makes iPhones and Macs.\n\nThe Company faces antitrust litigation. "
        "App Store fees disputed in EU.\n\nApple Operations International is a subsidiary in Ireland. "
        "Apple Distribution International is also based in Ireland."
    )
    apple_idx = raw_text.find("Apple Inc.")
    tim_idx = raw_text.find("Tim Cook")
    aoi_idx = raw_text.find("Apple Operations International")

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
                    "character_span": [0, 30],
                },
                "metadata": {"semantic_definition": "Cupertino-based technology firm"},
            },
            {
                "id": "ent-tim",
                "text": "Tim Cook",
                "entity_type": "PERSON",
                "confidence": 0.95,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Tim Cook is CEO.",
                    "sentence_index": 1,
                    "source_document": "AAPL",
                    "character_span": [tim_idx, tim_idx + 20],
                },
                "metadata": {},
            },
            {
                "id": "ent-aoi",
                "text": "Apple Operations International",
                "entity_type": "ORGANIZATION",
                "confidence": 0.85,
                "evidence": {
                    "chunk_id": "AAPL-c00001",
                    "sentence_text": "Apple Operations International is a subsidiary in Ireland.",
                    "sentence_index": 2,
                    "source_document": "AAPL",
                    "character_span": [aoi_idx, aoi_idx + 60],
                },
                "metadata": {"semantic_definition": "Apple subsidiary"},
            },
        ],
        "relationships": [
            {
                "id": "rel-tim-aapl",
                "subject": {"id": "ent-tim", "text": "Tim Cook", "entity_type": "PERSON"},
                "predicate": "ceo_of",
                "object": {"id": "ent-aapl", "text": "Apple Inc.", "entity_type": "ORGANIZATION"},
                "confidence": 0.95,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Tim Cook is CEO.",
                    "sentence_index": 1,
                    "source_document": "AAPL",
                    "character_span": [tim_idx, tim_idx + 20],
                },
            },
            {
                "id": "rel-aoi-aapl",
                "subject": {"id": "ent-aoi", "text": "Apple Operations International", "entity_type": "ORGANIZATION"},
                "predicate": "subsidiary_of",
                "object": {"id": "ent-aapl", "text": "Apple Inc.", "entity_type": "ORGANIZATION"},
                "confidence": 0.85,
                "evidence": {
                    "chunk_id": "AAPL-c00001",
                    "sentence_text": "Apple Operations International is a subsidiary in Ireland.",
                    "sentence_index": 2,
                    "source_document": "AAPL",
                    "character_span": [aoi_idx, aoi_idx + 60],
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


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _new_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def test_a1_dropped_in_5b_plus(tiny_corpus):
    """PRD-004 v5 Phase 5B+: mode 'A1' was dropped (it duplicated the
    dense-RAG pane). aquery_context now raises ValueError with a
    migration note."""
    loop = _new_event_loop()
    try:
        with pytest.raises(ValueError, match="A1.*dropped"):
            loop.run_until_complete(
                graph_rag.aquery_context(tiny_corpus, "x", mode="A1", top_k=2)
            )
    finally:
        loop.close()


def test_chunk_first_returns_chunks_plus_subgraph(tiny_corpus):
    """A2 alias resolves to chunk_first. Chunks anchor retrieval; each
    chunk row carries its in-chunk subgraph (5B+ retrieval redesign)."""
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple subsidiary Ireland", mode="A2", top_k=3)
        )
    finally:
        loop.close()
    assert bundle.mode == "chunk_first"
    assert len(bundle.text_chunks) >= 1
    assert bundle.n_hops >= 1
    # Each chunk row carries chunk + subgraph_nodes + subgraph_edges.
    assert len(bundle.chunk_rows) == len(bundle.text_chunks)
    # At least one of the seeded entities should be picked up across chunks.
    node_ids = {n.get("id") for n in bundle.graph_nodes}
    assert any(nid in node_ids for nid in ("ent-aapl", "ent-tim", "ent-aoi"))


def test_graph_first_returns_match_rows_with_source_chunks(tiny_corpus):
    """A3 alias resolves to graph_first. Graph items anchor retrieval;
    each match row carries the source chunk(s) (5B+ retrieval redesign)."""
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Tim Cook CEO", mode="A3", top_k=3)
        )
    finally:
        loop.close()
    assert bundle.mode == "graph_first"
    # graph_first returns no flat text_chunks; per-match source_chunks live
    # on graph_match_rows instead.
    assert bundle.text_chunks == []
    assert len(bundle.graph_match_rows) >= 1
    for row in bundle.graph_match_rows:
        assert row["kind"] in ("node", "edge")
        # source_chunks may be empty if dense_rag corpus isn't loadable
        # for this synthetic ticker, but the field must exist.
        assert "source_chunks" in row
    assert len(bundle.evidence_spans) >= 1
    for start, end in bundle.evidence_spans:
        assert 0 <= start < end <= len(bundle.source_text)


def test_parallel_runs_dense_and_graph_independently(tiny_corpus):
    """New parallel mode (5B+): dense + graph searched independently,
    both result lists land side-by-side in the prompt."""
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple subsidiary", mode="parallel", top_k=3)
        )
    finally:
        loop.close()
    assert bundle.mode == "parallel"
    # Both retrieval streams populated; no nested per-row joining.
    assert len(bundle.text_chunks) >= 1
    assert len(bundle.graph_nodes) >= 1 or len(bundle.graph_edges) >= 1
    # Parallel mode doesn't populate the nested per-row structures.
    assert bundle.chunk_rows == []
    assert bundle.graph_match_rows == []


def test_aquery_context_rejects_unknown_mode(tiny_corpus):
    loop = _new_event_loop()
    try:
        with pytest.raises(ValueError, match="mode must be"):
            loop.run_until_complete(
                graph_rag.aquery_context(tiny_corpus, "x", mode="A99")
            )
    finally:
        loop.close()


def test_context_filter_semantic_reranks(tiny_corpus):
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple", mode="A2", top_k=3)
        )
    finally:
        loop.close()
    filtered = graph_rag.context_filter(bundle, "semantic", query="Tim Cook")
    # 5B+ retrieval redesign: A2 alias resolves to chunk_first.
    assert filtered.mode == "chunk_first"
    assert len(filtered.graph_nodes) == len(bundle.graph_nodes)
    # Order may change; we don't assert specifics with a fake embedder
    # (semantic ranking is deterministic but content-blind), but the
    # filter should not error and should preserve item count.


def test_context_filter_relational_restricts_to_seed_neighborhood(tiny_corpus):
    # Build a bundle whose graph_nodes is a single seed, then filter
    # relational — should retain edges that touch that seed.
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple subsidiary", mode="A2", top_k=3)
        )
    finally:
        loop.close()
    seed_only = graph_rag.ContextBundle(
        mode="A2",
        text_chunks=[],
        graph_nodes=[n for n in bundle.graph_nodes if n.get("id") == "ent-aapl"],
        graph_edges=bundle.graph_edges,
        evidence_spans=bundle.evidence_spans,
        source_text=bundle.source_text,
    )
    filtered = graph_rag.context_filter(seed_only, "relational")
    # Edges that touch ent-aapl should remain; those not touching it dropped.
    for e in filtered.graph_edges:
        assert e.get("src") == "ent-aapl" or e.get("tgt") == "ent-aapl"


def test_context_filter_rejects_unknown_filter_type():
    bundle = graph_rag.ContextBundle(mode="chunk_first")
    with pytest.raises(ValueError, match="filter_type"):
        graph_rag.context_filter(bundle, "garbage")


def test_serialize_bundle_chunk_first_has_per_chunk_subgraph(tiny_corpus):
    """5B+ retrieval redesign: chunk_first serializes per-chunk rows
    with each chunk's in-chunk subgraph inline (vs flat sections)."""
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple subsidiary", mode="chunk_first", top_k=2)
        )
    finally:
        loop.close()
    s = graph_rag.serialize_bundle_for_prompt(bundle)
    assert "CHUNK-FIRST RETRIEVAL" in s
    assert "Chunk 1" in s


def test_serialize_bundle_graph_first_has_match_rows(tiny_corpus):
    """5B+ retrieval redesign: graph_first serializes per-match rows
    with each match's source chunk inline."""
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple", mode="graph_first", top_k=2)
        )
    finally:
        loop.close()
    s = graph_rag.serialize_bundle_for_prompt(bundle)
    assert "GRAPH-FIRST RETRIEVAL" in s
    assert "Match 1" in s


def test_serialize_bundle_parallel_has_two_independent_sections(tiny_corpus):
    """5B+ new parallel mode: serializes [DENSE RETRIEVAL] and
    [GRAPH RETRIEVAL] as two independent side-by-side sections."""
    loop = _new_event_loop()
    try:
        bundle = loop.run_until_complete(
            graph_rag.aquery_context(tiny_corpus, "Apple", mode="parallel", top_k=2)
        )
    finally:
        loop.close()
    s = graph_rag.serialize_bundle_for_prompt(bundle)
    assert "[DENSE RETRIEVAL]" in s
    assert "[GRAPH RETRIEVAL]" in s


def test_serialize_empty_bundle_returns_empty_string():
    bundle = graph_rag.ContextBundle(mode="chunk_first")
    assert graph_rag.serialize_bundle_for_prompt(bundle) == ""
