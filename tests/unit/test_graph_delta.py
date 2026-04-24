"""Wave J / PRD-056 v2 — `graph_delta` SSE payload helpers (commit 3).

Covers the pure helpers that shape the per-article graph_delta events:
  - ``_compute_graph_delta``: diffs two merged-KG snapshots; dedup-by-key
    so overlays that collapse into existing entities are NOT reported
    as additions.
  - ``_graph_delta_payload``: emits the wire shape the frontend consumes
    (article_index, outlet, added_entities, bridges_created, health, ...).

SSE routing + EventSource semantics are exercised by manual smoke +
Playwright (commit 6), not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DEMO_DIR = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))


@pytest.fixture
def demo_compare():
    import demo_compare as _dc  # noqa: WPS433
    return _dc


def _ent(text, etype="ORGANIZATION", **extra):
    base = {"text": text, "entity_type": etype, "confidence": 0.7}
    base.update(extra)
    return base


def _rel(subj, predicate, obj, **extra):
    base = {
        "subject": {"text": subj, "entity_type": "ORGANIZATION"},
        "predicate": predicate,
        "object": {"text": obj, "entity_type": "ORGANIZATION"},
        "confidence": 0.6,
    }
    base.update(extra)
    return base


def test_compute_graph_delta_reports_new_entities(demo_compare):
    before = {"entities": [_ent("JNJ")], "relationships": []}
    after = {"entities": [_ent("JNJ"), _ent("Merck")], "relationships": []}
    delta = demo_compare._compute_graph_delta(before, after)
    assert [e["text"] for e in delta["added_entities"]] == ["Merck"]
    assert delta["added_relationships"] == []


def test_compute_graph_delta_reports_new_relationships(demo_compare):
    before = {
        "entities": [_ent("JNJ"), _ent("Merck")],
        "relationships": [],
    }
    after = {
        "entities": [_ent("JNJ"), _ent("Merck")],
        "relationships": [_rel("JNJ", "partnered_with", "Merck")],
    }
    delta = demo_compare._compute_graph_delta(before, after)
    assert delta["added_entities"] == []
    assert len(delta["added_relationships"]) == 1
    assert delta["added_relationships"][0]["predicate"] == "partnered_with"


def test_compute_graph_delta_ignores_overlay_that_collapses(demo_compare):
    # Before + after are identical by normalized key → no additions even
    # though the text casing/punctuation differs.
    before = {"entities": [_ent("Johnson & Johnson")], "relationships": []}
    after = {"entities": [_ent("Johnson & Johnson"), _ent("johnson & johnson ")], "relationships": []}
    delta = demo_compare._compute_graph_delta(before, after)
    assert delta["added_entities"] == []


def test_compute_graph_delta_respects_admission_tokens(demo_compare):
    before = {"entities": [_ent("Johnson & Johnson Inc.")], "relationships": []}
    after = {
        "entities": [_ent("Johnson & Johnson Inc."), _ent("Johnson & Johnson")],
        "relationships": [],
    }
    # With "inc" as an admission token, both texts normalize identically →
    # the second one is not an addition.
    delta = demo_compare._compute_graph_delta(before, after, admission_tokens=["inc"])
    assert delta["added_entities"] == []


def test_graph_delta_payload_shape(demo_compare, monkeypatch):
    # Stub the health adapter so we don't require kgspin-core in CI.
    monkeypatch.setattr(
        "kgspin_demo_app.services.topology_health.health_for_kg",
        lambda _kg: {"score": 42, "node_count": 5, "edge_count": 3},
    )
    from kgspin_core.execution.graph_aware import SourceRef
    sref = SourceRef(
        kind="news_article", origin="reuters",
        article_id="art-7", fetched_at="2026-04-22T10:00Z",
    )

    payload = demo_compare._graph_delta_payload(
        article_index=2,
        news_source_type="news_article",
        news_article={"title": "JNJ-Merck partnership", "source_name": "Reuters"},
        overlay_sref=sref,
        added_entities=[_ent("Merck")],
        added_relationships=[_rel("JNJ", "partnered_with", "Merck")],
        bridges_created=[{"predicate": "partnered_with", "subject": "Johnson & Johnson",
                          "object": "Merck", "sources": []}],
        spokes_promoted=[{"canonical_name": "Pfizer"}],
        kgs_kg={"entities": [], "relationships": []},
    )

    assert payload["article_index"] == 2
    assert payload["article_id"] == "art-7"
    assert payload["outlet"] == "Reuters"
    assert payload["title"] == "JNJ-Merck partnership"
    assert payload["fetched_at"] == "2026-04-22T10:00Z"
    assert payload["source_ref"]["kind"] == "news_article"
    assert len(payload["added_entities"]) == 1
    assert len(payload["added_relationships"]) == 1
    assert len(payload["bridges_created"]) == 1
    assert payload["spokes_promoted"] == [{"canonical_name": "Pfizer"}]
    assert payload["health"]["score"] == 42


def test_graph_delta_payload_handles_missing_source_ref(demo_compare, monkeypatch):
    monkeypatch.setattr(
        "kgspin_demo_app.services.topology_health.health_for_kg",
        lambda _kg: {"score": -1, "insufficient_reason": "empty KG"},
    )
    payload = demo_compare._graph_delta_payload(
        article_index=0,
        news_source_type="",
        news_article={},
        overlay_sref=None,
        added_entities=[],
        added_relationships=[],
        bridges_created=[],
        spokes_promoted=[],
        kgs_kg={},
    )
    assert payload["source_ref"] is None
    assert payload["article_id"] is None
    assert payload["fetched_at"] == ""
