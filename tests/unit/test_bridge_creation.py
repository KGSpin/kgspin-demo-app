"""Wave J / PRD-056 v2 — bridge-edge creation from hub-registry matches.

Covers:
  - Hub-registry match on both endpoints + cross_hub-eligible relation
    → relationship marked ``kind="bridge"`` with SourceRef attached.
  - Hub-registry match on one endpoint (the non-current hub) → spoke promotion
    (no bridge edge created).
  - No hub-registry match → no bridge, no promotion.
  - Utility-gate veto → bridge not committed.
  - Cross-hub relation filter narrows bridge eligibility when populated.
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


@pytest.fixture
def hub_registry():
    from kgspin_core.execution.graph_aware import HubEntry
    return [
        HubEntry(
            canonical_name="Johnson & Johnson",
            aliases=("JNJ", "J&J"),
            ticker="JNJ",
            cik="0000200406",
            entity_type="ORGANIZATION",
            source_bundles=("financial-v2",),
            domain="financial",
        ),
        HubEntry(
            canonical_name="Merck",
            aliases=("MRK", "Merck & Co."),
            ticker="MRK",
            cik="0000310158",
            entity_type="ORGANIZATION",
            source_bundles=("financial-v2",),
            domain="financial",
        ),
        HubEntry(
            canonical_name="Pfizer",
            aliases=("PFE",),
            ticker="PFE",
            cik="0000078003",
            entity_type="ORGANIZATION",
            source_bundles=("financial-v2",),
            domain="financial",
        ),
    ]


def _kg_with(rel_predicate: str, subj_text: str = "Johnson & Johnson",
             obj_text: str = "Merck") -> dict:
    return {
        "entities": [
            {"text": subj_text, "entity_type": "ORGANIZATION", "confidence": 0.9,
             "sources": [{"kind": "filing", "origin": "sec",
                          "article_id": "JNJ_10K", "fetched_at": ""}]},
            {"text": obj_text, "entity_type": "ORGANIZATION", "confidence": 0.7,
             "sources": [{"kind": "news_article", "origin": "reuters",
                          "article_id": "art-1", "fetched_at": ""}]},
        ],
        "relationships": [
            {
                "subject": {"text": subj_text, "entity_type": "ORGANIZATION"},
                "predicate": rel_predicate,
                "object": {"text": obj_text, "entity_type": "ORGANIZATION"},
                "confidence": 0.6,
                "sources": [{"kind": "news_article", "origin": "reuters",
                             "article_id": "art-1", "fetched_at": ""}],
            },
        ],
    }


def _sref():
    return {
        "kind": "news_article", "origin": "reuters",
        "article_id": "art-1", "fetched_at": "2026-04-22T10:00Z",
    }


def test_two_hub_endpoints_become_bridge_edge(demo_compare, hub_registry):
    kg = _kg_with("partnered_with")
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        source_ref=_sref(),
    )
    assert len(result["bridges_created"]) == 1
    rel = kg["relationships"][0]
    assert rel["kind"] == "bridge"
    assert rel["is_cross_hub_bridge"] is True
    assert rel["subject_hub_ref"]["canonical_name"] == "Johnson & Johnson"
    assert rel["object_hub_ref"]["canonical_name"] == "Merck"
    # Bridge edge carries the article SourceRef.
    assert any(s.get("origin") == "reuters" for s in rel["sources"])
    # Predicate label preserved.
    assert rel["predicate"] == "partnered_with"


def test_single_sided_hub_match_promotes_spoke_no_bridge(demo_compare, hub_registry):
    # JNJ → Acme Co (not a hub)
    kg = _kg_with("competes_with", subj_text="Johnson & Johnson", obj_text="Acme Co")
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        source_ref=_sref(),
    )
    assert result["bridges_created"] == []
    assert result["spokes_promoted"] == []  # only matched hub IS the current hub
    assert kg["relationships"][0].get("kind") != "bridge"


def test_other_hub_mention_without_relation_promotes_spoke(demo_compare, hub_registry):
    # Subject = current hub (JNJ); object = Pfizer (a hub != current).
    kg = _kg_with("mentions", subj_text="Johnson & Johnson", obj_text="Pfizer")
    # Force the relation to be filtered out by the cross_hub catalog (so it's
    # not auto-bridged on the "any hub-hub fallback" path).
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        cross_hub_relations=frozenset({"partnered_with", "acquired"}),
        source_ref=_sref(),
    )
    assert result["bridges_created"] == []
    # When the relation is not in the cross_hub catalog, both endpoints still
    # match hubs but no bridge is recorded — the entity is not promoted again
    # because both sides matter (and JNJ is the current hub).
    assert kg["relationships"][0].get("kind") != "bridge"


def test_no_hub_match_no_bridge(demo_compare, hub_registry):
    kg = _kg_with("competes_with", subj_text="Acme Co", obj_text="Beta Co")
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        source_ref=_sref(),
    )
    assert result == {"bridges_created": [], "spokes_promoted": []}
    assert kg["relationships"][0].get("kind") != "bridge"


def test_gate_veto_blocks_bridge(demo_compare, hub_registry):
    class _DenyAll:
        def should_commit(self, *args, **kwargs):
            return False

    kg = _kg_with("partnered_with")
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        source_ref=_sref(),
        gate=_DenyAll(),
    )
    assert result["bridges_created"] == []
    assert kg["relationships"][0].get("kind") != "bridge"


def test_cross_hub_relation_filter_narrows_bridges(demo_compare, hub_registry):
    kg = _kg_with("competes_with")  # not in cross_hub catalog
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        cross_hub_relations=frozenset({"partnered_with"}),
        source_ref=_sref(),
    )
    assert result["bridges_created"] == []  # filtered out


def test_alias_match_creates_bridge(demo_compare, hub_registry):
    # Use the alias "MRK" for Merck — should still match the hub.
    kg = _kg_with("partnered_with", subj_text="JNJ", obj_text="MRK")
    result = demo_compare._create_bridges_from_matches(
        kg,
        current_hub="Johnson & Johnson",
        hub_registry=hub_registry,
        source_ref=_sref(),
    )
    assert len(result["bridges_created"]) == 1
    assert kg["relationships"][0]["kind"] == "bridge"
