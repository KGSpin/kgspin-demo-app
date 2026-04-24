"""Wave J / PRD-056 v2 — `build_vis_data` bridge + source styling (commit 4).

Verifies that the vis.js payload carries the Wave J metadata the frontend
rendering + filter modules depend on:
  - Bridge edges get ``kind="bridge"``, width > 1, distinct color.
  - Regular spokes stay ``kind="spoke"``, width 1.
  - Nodes aggregate ``source_kinds`` / ``source_origins`` / ``source_style``
    from the per-entity SourceRef list.
  - News-only nodes get dashed borders (shapeProperties.borderDashes).
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


def _ent(text, *, sources, confidence=0.9):
    return {
        "text": text,
        "entity_type": "ORGANIZATION",
        "confidence": confidence,
        "sources": list(sources),
    }


def _filing_src():
    return {"kind": "filing", "origin": "sec", "article_id": "JNJ_10K", "fetched_at": ""}


def _news_src(origin="reuters", article_id="art-1"):
    return {"kind": "news_article", "origin": origin, "article_id": article_id, "fetched_at": ""}


def _rel(subj, predicate, obj, *, kind="spoke", sources):
    return {
        "subject": {"text": subj, "entity_type": "ORGANIZATION"},
        "predicate": predicate,
        "object": {"text": obj, "entity_type": "ORGANIZATION"},
        "confidence": 0.8,
        "kind": kind,
        "sources": list(sources),
    }


def test_bridge_edge_gets_distinct_styling(demo_compare):
    kg = {
        "entities": [
            _ent("Johnson & Johnson", sources=[_filing_src()]),
            _ent("Merck", sources=[_news_src()]),
        ],
        "relationships": [
            _rel("Johnson & Johnson", "partnered_with", "Merck",
                 kind="bridge", sources=[_news_src()]),
        ],
    }
    vis = demo_compare.build_vis_data(kg)
    assert len(vis["edges"]) == 1
    edge = vis["edges"][0]
    assert edge["metadata"]["kind"] == "bridge"
    assert edge["metadata"]["is_bridge"] is True
    assert edge["width"] == 4
    assert edge["color"]["color"] == "#E67E5B"


def test_regular_spoke_edge_has_width_1(demo_compare):
    kg = {
        "entities": [
            _ent("Johnson & Johnson", sources=[_filing_src()]),
            _ent("Acme Co", sources=[_filing_src()]),
        ],
        "relationships": [
            _rel("Johnson & Johnson", "competes_with", "Acme Co",
                 kind="spoke", sources=[_filing_src()]),
        ],
    }
    vis = demo_compare.build_vis_data(kg)
    edge = vis["edges"][0]
    assert edge["metadata"]["kind"] == "spoke"
    assert edge["metadata"]["is_bridge"] is False
    assert edge["width"] == 1


def test_news_only_node_gets_dashed_border(demo_compare):
    kg = {
        "entities": [
            _ent("Johnson & Johnson", sources=[_filing_src()]),
            _ent("Acme Co", sources=[_news_src(origin="reuters")]),
        ],
        "relationships": [
            _rel("Johnson & Johnson", "competes_with", "Acme Co",
                 sources=[_news_src()]),
        ],
    }
    vis = demo_compare.build_vis_data(kg)
    acme = next(n for n in vis["nodes"] if "Acme" in n["metadata"]["text"])
    assert acme["metadata"]["source_style"] == "news_only"
    assert acme.get("shapeProperties", {}).get("borderDashes") == [4, 4]
    assert "reuters" in acme["label"]  # outlet badge in label


def test_hybrid_node_has_news_counter(demo_compare):
    kg = {
        "entities": [
            _ent("Johnson & Johnson",
                 sources=[_filing_src(), _news_src(article_id="art-1"),
                          _news_src(article_id="art-2")]),
            _ent("Acme Co", sources=[_filing_src()]),
        ],
        "relationships": [
            _rel("Johnson & Johnson", "competes_with", "Acme Co",
                 sources=[_filing_src()]),
        ],
    }
    vis = demo_compare.build_vis_data(kg)
    jnj = next(n for n in vis["nodes"] if "Johnson" in n["metadata"]["text"])
    assert jnj["metadata"]["source_style"] == "hybrid"
    assert "+2 news" in jnj["label"]
    assert jnj.get("shapeProperties", {}).get("borderDashes") is None


def test_node_source_origins_aggregated(demo_compare):
    kg = {
        "entities": [
            _ent("Johnson & Johnson",
                 sources=[_filing_src(),
                          _news_src(origin="reuters", article_id="a"),
                          _news_src(origin="bloomberg", article_id="b")]),
            _ent("Acme Co", sources=[_filing_src()]),
        ],
        "relationships": [
            _rel("Johnson & Johnson", "competes_with", "Acme Co",
                 sources=[_filing_src()]),
        ],
    }
    vis = demo_compare.build_vis_data(kg)
    jnj = next(n for n in vis["nodes"] if "Johnson" in n["metadata"]["text"])
    assert set(jnj["metadata"]["source_origins"]) == {"sec", "reuters", "bloomberg"}
    assert set(jnj["metadata"]["source_kinds"]) == {"filing", "news_article"}
