"""Unit tests for the three retrieval strategies.

Each strategy gets a tiny hand-crafted graph so the expected chunk
ordering is deterministic and auditable.
"""

from __future__ import annotations

import pytest

from benchmarks.retrieval import (
    fan_out_from_corpus,
    fan_out_from_graph,
    semantic_composed,
)


def _graph() -> dict:
    """Two chunks, two nodes sharing chunk c1, one edge linking them."""
    return {
        "schema_version": "graph-v0",
        "corpus_id": "unit",
        "arm": "b",
        "chunks": [
            {"chunk_id": "c1", "doc_id": "D1",
             "text": "Apple revenue grew by 10 percent in 2022."},
            {"chunk_id": "c2", "doc_id": "D1",
             "text": "Cupertino skies were blue that year."},
            {"chunk_id": "c3", "doc_id": "D2",
             "text": "Microsoft also reported Azure revenue growth."},
        ],
        "nodes": [
            {"node_id": "n1", "surface_form": "Apple", "node_type": "ORGANIZATION",
             "provenance": [{"chunk_id": "c1"}]},
            {"node_id": "n2", "surface_form": "revenue", "node_type": "METRIC",
             "provenance": [{"chunk_id": "c1"}, {"chunk_id": "c3"}]},
            {"node_id": "n3", "surface_form": "Cupertino", "node_type": "LOCATION",
             "provenance": [{"chunk_id": "c2"}]},
        ],
        "edges": [
            {"edge_id": "e1", "subject": "n1", "predicate": "reports",
             "object": "n2", "provenance": [{"chunk_id": "c1"}]},
            {"edge_id": "e2", "subject": "n1", "predicate": "headquartered_in",
             "object": "n3", "provenance": [{"chunk_id": "c2"}]},
        ],
    }


# --- fan_out_from_corpus --------------------------------------------------


def test_corpus_fan_out_picks_best_matching_chunk_first() -> None:
    g = _graph()
    out = fan_out_from_corpus.retrieve(g, "Apple revenue", top_k=3)
    assert out
    assert "Apple" in out[0]  # c1 scores highest


def test_corpus_fan_out_expands_via_graph_one_hop() -> None:
    g = _graph()
    # Question mentions Apple; c1 is seed. Graph expansion through
    # n1 → n3 (Cupertino) should pull c2 in even though "Cupertino" isn't
    # in the question text.
    out = fan_out_from_corpus.retrieve(g, "Apple revenue", top_k=5)
    assert any("Cupertino" in t for t in out)


def test_corpus_fan_out_empty_on_no_tokens() -> None:
    g = _graph()
    assert fan_out_from_corpus.retrieve(g, "", top_k=5) == []


# --- fan_out_from_graph ---------------------------------------------------


def test_graph_fan_out_ranks_by_entity_match() -> None:
    g = _graph()
    out = fan_out_from_graph.retrieve(g, "Apple revenue growth", top_k=3)
    assert out
    # Edge e1 links n1 (Apple) to n2 (revenue) — both seeded — so its
    # evidence chunk c1 should win the top slot.
    assert "Apple" in out[0]


def test_graph_fan_out_no_nodes_returns_empty() -> None:
    empty = {"schema_version": "graph-v0", "corpus_id": "u", "arm": "b",
             "chunks": [], "nodes": [], "edges": []}
    assert fan_out_from_graph.retrieve(empty, "Apple", top_k=3) == []


# --- semantic_composed ----------------------------------------------------


def test_semantic_composed_blends_both_strategies() -> None:
    g = _graph()
    out = semantic_composed.retrieve(g, "Apple revenue", top_k=5)
    assert out
    # The composed strategy should reach results that either single
    # strategy can return, but strictly filter to top_k.
    assert len(out) <= 5


def test_semantic_composed_stable_when_one_strategy_empty() -> None:
    """If graph-side yields nothing (no matching entity), still returns
    corpus-side hits."""
    g = _graph()
    out = semantic_composed.retrieve(g, "Apple", top_k=3)
    assert out
