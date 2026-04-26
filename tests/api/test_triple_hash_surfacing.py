"""Phase 2 INSTALLATION (CTO 2026-04-26) — triple-hash surfacing tests.

Asserts the wire shape of the ``extraction_metadata`` block on every
extraction-returning surface (api/server.py response models, mcp_server.py
helper, demos/extraction/routes/runs.py legacy fallback) plus the
match-or-409 contract for the replay endpoint.

These tests deliberately avoid spinning up a full kgspin-core extraction
(which depends on heavy ML deps) — they exercise the wire-shape glue
that the demo-app owns. The end-to-end determinism property is owned
by kgspin-core's own test suite (the orchestrator's triple-hash
machinery is verified there).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# demos/extraction is not a package — add it to sys.path so the cached-runs
# UI route module can import ``cache.run_log`` the same way the uvicorn
# entry point does. Mirrors tests/unit/test_demo_compare_llm_endpoints.py.
_DEMO_DIR = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))


# ---------------------------------------------------------------------------
# api/server.py — ExtractionMetadata model + lift helper
# ---------------------------------------------------------------------------


def test_extraction_metadata_field_order_pinned():
    """VP-Eng C4: JSON serialization must keep field order stable."""
    from kgspin_demo_app.api.server import ExtractionMetadata

    m = ExtractionMetadata(
        pipeline_version_hash="aaa",
        bundle_version_hash="bbb",
        installation_version_hash="ccc",
    )
    keys = list(m.model_dump().keys())
    assert keys == [
        "schema_version",
        "pipeline_version_hash",
        "bundle_version_hash",
        "installation_version_hash",
    ]


def test_extraction_metadata_schema_version_default_pinned():
    """schema_version defaults to V1 (today). Bumps are explicit."""
    from kgspin_demo_app.api.server import ExtractionMetadata
    from kgspin_interface.version import INSTALLATION_CONFIG_SCHEMA_V1

    m = ExtractionMetadata()
    assert m.schema_version == INSTALLATION_CONFIG_SCHEMA_V1


def test_build_extraction_metadata_normalizes_empty_strings_to_none():
    """kgspin-core's migration-window default is empty strings; we
    normalize to ``None`` so the customer-facing surface has one
    "unset" representation."""
    from kgspin_demo_app.api.server import _build_extraction_metadata

    provenance = SimpleNamespace(
        pipeline_version_hash="",
        bundle_version_hash="",
        installation_version_hash="",
    )
    meta = _build_extraction_metadata(provenance)
    assert meta.pipeline_version_hash is None
    assert meta.bundle_version_hash is None
    assert meta.installation_version_hash is None


def test_build_extraction_metadata_passes_through_real_hashes():
    """Verbatim passthrough — no filtering, no rewriting."""
    from kgspin_demo_app.api.server import _build_extraction_metadata

    provenance = SimpleNamespace(
        pipeline_version_hash="00d9b544577a7abc",
        bundle_version_hash="5e96a76e7715f5b8",
        installation_version_hash="abc123def4567890",
    )
    meta = _build_extraction_metadata(provenance)
    assert meta.pipeline_version_hash == "00d9b544577a7abc"
    assert meta.bundle_version_hash == "5e96a76e7715f5b8"
    assert meta.installation_version_hash == "abc123def4567890"


def test_relationship_response_carries_extraction_metadata_field():
    """Pre-Phase-2 callers see the new field as additive — the only
    backwards-incompat is that ``extraction_metadata`` is now required
    in the model. Flat ``bundle_version`` field is preserved one
    release as a deprecation shim."""
    from kgspin_demo_app.api.server import (
        ExtractionMetadata,
        RelationshipResponse,
    )

    resp = RelationshipResponse(
        entities=[],
        relationships=[],
        bundle_version="v1.0",
        processing_time_ms=12.3,
        extraction_metadata=ExtractionMetadata(),
    )
    assert "extraction_metadata" in resp.model_dump()
    # Deprecation shim retained.
    assert "bundle_version" in resp.model_dump()


def test_entity_response_extraction_metadata_optional_and_defaults_none():
    """GLiNER entity-only path doesn't run the orchestrator that mints
    the triple, so this surface returns ``extraction_metadata: null``
    (still present on the wire — schema-stable for downstream tooling)."""
    from kgspin_demo_app.api.server import EntityResponse

    resp = EntityResponse(entities=[], count=0, processing_time_ms=1.0)
    payload = resp.model_dump()
    assert "extraction_metadata" in payload
    assert payload["extraction_metadata"] is None


# ---------------------------------------------------------------------------
# mcp_server.py — _extraction_metadata_dict mirrors the API model
# ---------------------------------------------------------------------------


def test_mcp_extraction_metadata_dict_field_order_matches_api():
    """MCP and API must produce structurally identical metadata blocks."""
    from kgspin_demo_app.mcp_server import _extraction_metadata_dict

    provenance = SimpleNamespace(
        pipeline_version_hash="aaa",
        bundle_version_hash="bbb",
        installation_version_hash="ccc",
    )
    meta = _extraction_metadata_dict(provenance)
    assert list(meta.keys()) == [
        "schema_version",
        "pipeline_version_hash",
        "bundle_version_hash",
        "installation_version_hash",
    ]
    assert meta["pipeline_version_hash"] == "aaa"


def test_mcp_extraction_metadata_dict_normalizes_missing_attrs_to_none():
    """getattr default chain — provenance objects without the new
    fields (legacy mocks, older subclasses) surface as ``None``."""
    from kgspin_demo_app.mcp_server import _extraction_metadata_dict

    provenance = SimpleNamespace()
    meta = _extraction_metadata_dict(provenance)
    assert meta["pipeline_version_hash"] is None
    assert meta["bundle_version_hash"] is None
    assert meta["installation_version_hash"] is None


# ---------------------------------------------------------------------------
# Cached-runs UI — legacy fallback to <pre-Phase-2>
# ---------------------------------------------------------------------------


def test_cached_runs_pre_phase_2_fallback_for_legacy_runs():
    """A cached run captured before Phase 2 has no triple in
    ``kg.provenance``. Render ``<pre-Phase-2>`` rather than empty
    strings (which would look like a bug to a customer)."""
    from demos.extraction.routes.runs import _extraction_metadata_from_kg

    legacy_kg = {
        "provenance": {
            "extraction_bundle_version": "v1.0",
            "timestamp": "2025-09-01T00:00:00Z",
        }
    }
    meta = _extraction_metadata_from_kg(legacy_kg)
    assert meta["pipeline_version_hash"] == "<pre-Phase-2>"
    assert meta["bundle_version_hash"] == "<pre-Phase-2>"
    assert meta["installation_version_hash"] == "<pre-Phase-2>"


def test_cached_runs_passes_real_triple_through():
    """Post-Phase-2 cached run carries the triple in provenance —
    pass through verbatim."""
    from demos.extraction.routes.runs import _extraction_metadata_from_kg

    live_kg = {
        "provenance": {
            "pipeline_version_hash": "pip-hash-1",
            "bundle_version_hash": "bnd-hash-1",
            "installation_version_hash": "ins-hash-1",
        }
    }
    meta = _extraction_metadata_from_kg(live_kg)
    assert meta["pipeline_version_hash"] == "pip-hash-1"
    assert meta["bundle_version_hash"] == "bnd-hash-1"
    assert meta["installation_version_hash"] == "ins-hash-1"


def test_cached_runs_empty_string_in_provenance_falls_back_to_pre_phase_2():
    """Migration-window empty strings surface as <pre-Phase-2> so the
    UI shows one consistent "unrecorded" signal."""
    from demos.extraction.routes.runs import _extraction_metadata_from_kg

    drift_kg = {
        "provenance": {
            "pipeline_version_hash": "",
            "bundle_version_hash": "",
            "installation_version_hash": "",
        }
    }
    meta = _extraction_metadata_from_kg(drift_kg)
    assert meta["pipeline_version_hash"] == "<pre-Phase-2>"
    assert meta["bundle_version_hash"] == "<pre-Phase-2>"
    assert meta["installation_version_hash"] == "<pre-Phase-2>"


# ---------------------------------------------------------------------------
# Replay endpoint — match-or-409 contract
# ---------------------------------------------------------------------------


def _fake_extraction_result(pipeline="pip", bundle="bnd", install="ins"):
    """Build a minimal ExtractionResult-shaped namespace that the
    replay endpoint can lift the triple from."""
    provenance = SimpleNamespace(
        pipeline_version_hash=pipeline,
        bundle_version_hash=bundle,
        installation_version_hash=install,
    )
    return SimpleNamespace(
        entities=[],
        relationships=[],
        provenance=provenance,
    )


@pytest.fixture
def replay_app(monkeypatch):
    """Build a FastAPI app with the extractor + bundle stubbed so the
    replay endpoint is exercised without spinning up the real ML
    pipeline."""
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("fastapi.testclient")

    from kgspin_demo_app.api import server as api_server

    fake_bundle = SimpleNamespace(version="v-fake")

    def _fake_get_bundle(name=None):
        return fake_bundle

    monkeypatch.setattr(api_server, "get_bundle", _fake_get_bundle)

    class _FakeExtractor:
        def __init__(self, bundle, **_kwargs):
            self.bundle = bundle

        def extract(self, text, source_document):
            return _fake_extraction_result()

    monkeypatch.setattr(api_server, "KnowledgeGraphExtractor", _FakeExtractor)

    app = api_server.create_app()
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_replay_endpoint_returns_200_on_triple_match(replay_app):
    """Customer pins the triple they captured from a prior extract;
    deployment is still on that triple → 200 with the same shape as
    /extract/relationships plus the echoed triple."""
    resp = replay_app.post(
        "/extract/replay/relationships",
        json={
            "text": "doc body",
            "source_document": "test-doc",
            "pipeline_version_hash": "pip",
            "bundle_version_hash": "bnd",
            "installation_version_hash": "ins",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["extraction_metadata"]["pipeline_version_hash"] == "pip"
    assert body["extraction_metadata"]["bundle_version_hash"] == "bnd"
    assert body["extraction_metadata"]["installation_version_hash"] == "ins"


def test_replay_endpoint_409_on_pipeline_mismatch_with_installed_echo(replay_app):
    """Customer pins yesterday's pipeline; deployment redeployed →
    409 with both ``requested`` and ``installed`` triples so the
    customer can see what version this deployment is on."""
    resp = replay_app.post(
        "/extract/replay/relationships",
        json={
            "text": "doc body",
            "source_document": "test-doc",
            "pipeline_version_hash": "pip-old",  # mismatch
            "bundle_version_hash": "bnd",
            "installation_version_hash": "ins",
        },
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "triple_hash_mismatch"
    assert detail["requested"]["pipeline_version_hash"] == "pip-old"
    assert detail["installed"]["pipeline_version_hash"] == "pip"


def test_replay_endpoint_409_on_bundle_mismatch(replay_app):
    resp = replay_app.post(
        "/extract/replay/relationships",
        json={
            "text": "doc body",
            "source_document": "test-doc",
            "pipeline_version_hash": "pip",
            "bundle_version_hash": "bnd-old",  # mismatch
            "installation_version_hash": "ins",
        },
    )
    assert resp.status_code == 409


def test_replay_endpoint_409_on_installation_mismatch(replay_app):
    resp = replay_app.post(
        "/extract/replay/relationships",
        json={
            "text": "doc body",
            "source_document": "test-doc",
            "pipeline_version_hash": "pip",
            "bundle_version_hash": "bnd",
            "installation_version_hash": "ins-old",  # mismatch
        },
    )
    assert resp.status_code == 409
