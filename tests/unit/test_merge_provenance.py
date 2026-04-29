"""Wave J / PRD-056 v2 — provenance-preserving merge + hub-registry client.

Covers:
  - Union-with-provenance semantics: same normalized key → aliases union,
    confidence = max, sources union (deduped by kind/origin/article_id).
  - SourceRef present on every entity + relationship after merge.
  - Legacy KGs without ``sources`` get a synthetic default at merge time.
  - Admission-token normalization collapses ``Inc.``-style suffixes.
  - Hub-registry fetch falls back to ``[]`` on admin unreachable + emits
    a warning.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.error import URLError

import pytest


# demos/extraction is not a package — add it to sys.path so we can import
# ``demo_compare`` the same way the uvicorn entry point does.
_DEMO_DIR = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))


@pytest.fixture
def demo_compare():
    """Late import — keeps fixture-managed env vars in play."""
    import demo_compare as _dc  # noqa: WPS433
    return _dc


# --- Merge semantics --------------------------------------------------------


def _ent(text: str, etype: str = "ORGANIZATION", **extra):
    base = {"text": text, "entity_type": etype, "confidence": 0.7}
    base.update(extra)
    return base


def _rel(subj: str, predicate: str, obj: str, **extra):
    base = {
        "subject": {"text": subj, "entity_type": "ORGANIZATION"},
        "predicate": predicate,
        "object": {"text": obj, "entity_type": "ORGANIZATION"},
        "confidence": 0.6,
    }
    base.update(extra)
    return base


def test_merge_unions_aliases_max_confidence_and_sources(demo_compare):
    base = {
        "entities": [
            _ent("Johnson & Johnson", confidence=0.9, aliases=["JNJ"], sources=[
                {"kind": "filing", "origin": "sec", "article_id": "JNJ_10K", "fetched_at": ""}
            ]),
        ],
        "relationships": [],
    }
    overlay = {
        "entities": [
            _ent("Johnson & Johnson", confidence=0.5, aliases=["J&J"]),
        ],
        "relationships": [],
    }
    overlay_sref = {
        "kind": "news_article", "origin": "reuters",
        "article_id": "art-1", "fetched_at": "2026-04-22T10:00Z",
    }

    merged = demo_compare._merge_kgs_with_provenance(
        base, overlay, overlay_source_ref=overlay_sref,
    )

    assert len(merged["entities"]) == 1
    e = merged["entities"][0]
    assert e["confidence"] == 0.9  # max
    assert "JNJ" in e["aliases"] and "J&J" in e["aliases"]
    kinds = {s["kind"] for s in e["sources"]}
    assert kinds == {"filing", "news_article"}


def test_every_node_and_edge_carries_sources(demo_compare):
    base = {
        "entities": [_ent("JNJ"), _ent("Merck")],
        "relationships": [_rel("JNJ", "partnered_with", "Merck")],
    }
    overlay = {
        "entities": [_ent("Pfizer")],
        "relationships": [_rel("JNJ", "competes_with", "Pfizer")],
    }
    overlay_sref = {
        "kind": "news_article", "origin": "bloomberg",
        "article_id": "art-2", "fetched_at": "",
    }

    merged = demo_compare._merge_kgs_with_provenance(
        base, overlay, overlay_source_ref=overlay_sref,
    )

    for ent in merged["entities"]:
        assert ent.get("sources"), f"entity missing sources: {ent}"
    for rel in merged["relationships"]:
        assert rel.get("sources"), f"relationship missing sources: {rel}"


def test_legacy_kg_without_sources_gets_synthetic_default(demo_compare):
    legacy = {
        "entities": [_ent("Apple")],
        "relationships": [_rel("Apple", "competes_with", "Samsung")],
    }
    merged = demo_compare._merge_kgs_with_provenance(legacy, {"entities": [], "relationships": []})

    apple = next(e for e in merged["entities"] if "apple" in e["text"].lower())
    assert apple["sources"] == [
        {"kind": "filing", "origin": "legacy", "article_id": None, "fetched_at": None}
    ]


def test_admission_tokens_collapse_corporate_suffix(demo_compare):
    base = {"entities": [_ent("Johnson & Johnson Inc.")], "relationships": []}
    overlay = {"entities": [_ent("Johnson & Johnson")], "relationships": []}

    merged = demo_compare._merge_kgs_with_provenance(
        base, overlay, admission_tokens=["inc"],
    )
    # Both normalize to "johnson & johnson" → single merged entity.
    assert len(merged["entities"]) == 1


def test_source_ref_dedup_by_kind_origin_article(demo_compare):
    sref = {
        "kind": "news_article", "origin": "reuters",
        "article_id": "art-1", "fetched_at": "",
    }
    base = {"entities": [_ent("Acme", sources=[sref])], "relationships": []}
    overlay = {"entities": [_ent("Acme", sources=[dict(sref)])], "relationships": []}

    merged = demo_compare._merge_kgs_with_provenance(base, overlay)

    assert len(merged["entities"][0]["sources"]) == 1


# --- Hub-registry fetch -----------------------------------------------------


def test_fetch_hub_registry_returns_empty_on_admin_unreachable(
    demo_compare, monkeypatch, caplog
):
    def _explode(*args, **kwargs):
        raise URLError("admin offline")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    # Reset per-process cache for test isolation.
    demo_compare._hub_registry_cache.clear()

    with caplog.at_level(logging.WARNING):
        out = demo_compare._fetch_hub_registry_sync("financial")

    assert out == []
    assert any("hub registry unreachable" in rec.message for rec in caplog.records)


def test_fetch_hub_registry_deserializes_admin_payload(demo_compare, monkeypatch):
    import io
    import json as _json

    payload = {
        "hubs": [
            {
                "canonical_name": "Johnson & Johnson",
                "aliases": ["JNJ", "J&J"],
                "ticker": "JNJ",
                "cik": "0000200406",
                "entity_type": "ORGANIZATION",
                "source_bundles": ["financial-v0"],
                "domain": "financial",
            },
        ]
    }

    class _FakeResp:
        def __init__(self, body): self._body = body
        def read(self): return self._body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(url, timeout=5):
        return _FakeResp(_json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    demo_compare._hub_registry_cache.clear()

    out = demo_compare._fetch_hub_registry_sync("financial")
    assert len(out) == 1
    assert out[0].canonical_name == "Johnson & Johnson"
    assert out[0].aliases == ("JNJ", "J&J")
    assert out[0].ticker == "JNJ"
