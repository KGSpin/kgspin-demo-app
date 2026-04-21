"""Unit tests for ``benchmarks.arms.b.extract`` — the Arm B extractor.

Targets pure paths only: chunker, canonicalizer, graph assembly. The
LLM round-trip is covered by driving ``build_graph`` with ``mock_llm=True``.
"""

from __future__ import annotations

import pytest

from benchmarks.arms.b.extract import (
    Chunk,
    LLMTripleExtractor,
    _jaccard,
    _normalize,
    build_graph,
    canonicalize,
    chunk_document,
)


# --- chunking --------------------------------------------------------------


def test_chunk_document_keeps_paragraph_boundaries() -> None:
    text = "\n\n".join(["Paragraph A body."] * 5)
    chunks = chunk_document("DOC1", text)
    assert all(c.doc_id == "DOC1" for c in chunks)
    assert all(c.text for c in chunks)
    # Chunks should reference unique ids.
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_chunk_document_small_input_yields_single_chunk() -> None:
    chunks = chunk_document("DOC", "short body")
    assert len(chunks) == 1
    assert chunks[0].text == "short body"


# --- canonicalization -----------------------------------------------------


def test_normalize_lowercases_and_trims() -> None:
    assert _normalize("  Apple  Inc.  ") == "apple inc."


def test_jaccard_basic() -> None:
    assert _jaccard("apple inc", "apple corp") == pytest.approx(1 / 3)
    assert _jaccard("", "anything") == 0.0


def test_canonicalize_merges_exact_match() -> None:
    from benchmarks.arms.b.extract import RawTriple

    triples = [
        RawTriple(
            subject="Apple Inc.",
            subject_type="ORGANIZATION",
            predicate="makes",
            object="iPhone",
            object_type="PRODUCT",
            evidence_text="Apple Inc. makes iPhone.",
            chunk_id="c1",
        ),
        RawTriple(
            subject="apple inc.",  # same normalized form
            subject_type="ORGANIZATION",
            predicate="headquartered_in",
            object="Cupertino",
            object_type="LOCATION",
            evidence_text="Apple Inc. is headquartered in Cupertino.",
            chunk_id="c2",
        ),
    ]
    nodes, edges = canonicalize(triples)
    org_nodes = [n for n in nodes if n.node_type == "ORGANIZATION"]
    assert len(org_nodes) == 1
    # Should have visited both chunks.
    assert {p["chunk_id"] for p in org_nodes[0].provenance} == {"c1", "c2"}
    assert len(edges) == 2


def test_canonicalize_jaccard_merges_close_surface_forms() -> None:
    from benchmarks.arms.b.extract import RawTriple

    triples = [
        RawTriple(
            subject="Apple Inc",
            subject_type="ORGANIZATION",
            predicate="has_metric",
            object="Revenue",
            object_type="METRIC",
            evidence_text="",
            chunk_id="c1",
        ),
        RawTriple(
            subject="Apple Inc.",  # near-miss on token set
            subject_type="ORGANIZATION",
            predicate="has_metric",
            object="Revenue",
            object_type="METRIC",
            evidence_text="",
            chunk_id="c2",
        ),
    ]
    nodes, _ = canonicalize(triples)
    assert len([n for n in nodes if n.node_type == "ORGANIZATION"]) == 1


def test_canonicalize_keeps_types_separate() -> None:
    """Same surface form under different types → two distinct nodes."""
    from benchmarks.arms.b.extract import RawTriple

    triples = [
        RawTriple(
            subject="Apple",  # entity of type ORG
            subject_type="ORGANIZATION",
            predicate="x",
            object="y",
            object_type="ORGANIZATION",
            evidence_text="",
            chunk_id="c1",
        ),
        RawTriple(
            subject="Apple",  # entity of type PRODUCT
            subject_type="PRODUCT",
            predicate="x",
            object="y",
            object_type="ORGANIZATION",
            evidence_text="",
            chunk_id="c2",
        ),
    ]
    nodes, _ = canonicalize(triples)
    apple_nodes = [n for n in nodes if n.surface_form.lower() == "apple"]
    assert {n.node_type for n in apple_nodes} == {"ORGANIZATION", "PRODUCT"}


# --- build_graph (mock LLM) -----------------------------------------------


def test_build_graph_mock_mode_emits_graph_v0() -> None:
    corpus = {"DOC_A": "alpha body.\n\nbeta body.", "DOC_B": "gamma."}
    graph = build_graph(corpus=corpus, corpus_id="test-corpus", mock_llm=True)
    assert graph["schema_version"] == "graph-v0"
    assert graph["corpus_id"] == "test-corpus"
    assert graph["arm"] == "b"
    assert graph["producer"]["llm_alias"] == "mock"
    # Mock emits 2 triples per chunk; 3 chunks → 6 triples → some nodes.
    assert len(graph["chunks"]) >= 2
    assert len(graph["nodes"]) >= 2
    assert len(graph["edges"]) >= 2


def test_build_graph_threads_llm_selector() -> None:
    graph = build_graph(
        corpus={"D": "text."},
        corpus_id="c",
        mock_llm=True,
        llm_alias="gemini_flash",
    )
    assert graph["producer"]["llm_alias"] == "gemini_flash"


def test_llm_triple_extractor_is_idempotent_in_mock_mode() -> None:
    extractor = LLMTripleExtractor(mock_llm=True)
    chunk = Chunk(chunk_id="c1", doc_id="D", text="body.", span_start=0, span_end=5)
    first = [(t.subject, t.predicate, t.object) for t in extractor.extract(chunk)]
    second = [(t.subject, t.predicate, t.object) for t in extractor.extract(chunk)]
    assert first == second
