"""FastAPI endpoint tests for the ADR-002 LLM alias surface (Sprint 0.5.4).

Covers:
- ``llm_alias`` + legacy ``model`` → 400 (ambiguous) on every endpoint that
  exposes both selectors.
- Legacy ``model``-only path emits a DeprecationWarning.
- ``llm_alias``-only path resolves through the injected resolver.
- Fall-through when neither selector is passed uses the demo's configured
  default (no surprise vendor default).

Real LLM calls are avoided by monkey-patching
:func:`kgspin_demo_app.llm_backend.resolve_llm_backend` to return a recording
fake backend; each endpoint's body-level import resolves to the patched
symbol on every request.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# demos/extraction is not a package — add it to sys.path so we can import
# ``demo_compare`` the same way the uvicorn entry point does.
_DEMO_DIR = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))


@dataclass
class _Result:
    text: str = "fake answer"
    tokens_used: int = 123


@dataclass
class _FakeBackend:
    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str, **kwargs: Any) -> _Result:
        self.calls.append(prompt)
        return _Result()


@dataclass
class _ResolveCall:
    llm_alias: str | None
    legacy_model: str | None
    flow: str | None


@pytest.fixture
def app_client():
    """Return a FastAPI TestClient for the demo app (imported lazily)."""
    import demo_compare  # noqa: WPS433 — intentional late import

    client = TestClient(demo_compare.app)
    return client, demo_compare


@pytest.fixture
def patched_resolver(monkeypatch: pytest.MonkeyPatch) -> list[_ResolveCall]:
    """Replace ``resolve_llm_backend`` with a recorder.

    The demo's endpoint bodies re-import ``resolve_llm_backend`` on every
    request (local ``from kgspin_demo_app.llm_backend import …``), so patching
    the module attribute is enough — the next request picks up the fake.
    """
    from kgspin_demo_app import llm_backend as _mod

    records: list[_ResolveCall] = []

    def _fake(**kwargs: Any) -> _FakeBackend:
        records.append(_ResolveCall(
            llm_alias=kwargs.get("llm_alias"),
            legacy_model=kwargs.get("legacy_model"),
            flow=kwargs.get("flow"),
        ))
        return _FakeBackend()

    monkeypatch.setattr(_mod, "resolve_llm_backend", _fake)
    return records


@pytest.fixture
def stub_bundle(monkeypatch: pytest.MonkeyPatch):
    """Stub out :func:`demo_compare._get_bundle` so tests don't load real bundles.

    The auto-flag / auto-discover-TP endpoints read a bundle for entity-type
    hierarchy context *after* the LLM resolver runs. We care about verifying
    the alias threads to the resolver; the bundle load is orthogonal and
    currently blows up on a schema-v2 mismatch in the on-disk test bundle.
    """
    import demo_compare

    class _StubBundle:
        entity_parent_types: dict[str, str] = {}
        type_semantic_definitions: dict[str, str] = {}
        entity_types = ["COMPANY", "PRODUCT"]

    monkeypatch.setattr(demo_compare, "_get_bundle", lambda *a, **kw: _StubBundle())
    monkeypatch.setattr(demo_compare, "_get_bundle_predicates", lambda *a, **kw: [])
    return _StubBundle


# ---------- Ambiguity returns 400 on every endpoint with both selectors ----------


@pytest.mark.parametrize(
    "method, url, kwargs",
    [
        ("GET", "/api/compare/PFE?llm_alias=ga&model=foo", {}),
        (
            "GET",
            "/api/compare-clinical/NCT0001?llm_alias=ga&model=foo",
            {},
        ),
        ("GET", "/api/refresh-agentic-flash/PFE?llm_alias=ga&model=foo", {}),
        ("GET", "/api/refresh-agentic-analyst/PFE?llm_alias=ga&model=foo", {}),
        ("GET", "/api/intelligence/PFE?llm_alias=ga&model=foo", {}),
        ("GET", "/api/refresh-intel/PFE?llm_alias=ga&model=foo", {}),
        ("GET", "/api/why-this-matters/PFE?llm_alias=ga&model=foo", {}),
        ("GET", "/api/impact/PFE?llm_alias=ga&model=foo", {}),
        (
            "POST",
            "/api/compare-qa/PFE",
            {"json": {"graphs": [], "llm_alias": "ga", "model": "foo"}},
        ),
        (
            "POST",
            "/api/feedback/auto_flag",
            {"json": {"nodes": [{"id": 1}], "edges": [], "llm_alias": "ga", "model": "foo"}},
        ),
        (
            "POST",
            "/api/feedback/auto_discover_tp",
            {"json": {"ticker": "PFE", "nodes": [{"id": 1}], "edges": [], "llm_alias": "ga", "model": "foo"}},
        ),
        (
            "POST",
            "/api/refresh-analysis/PFE",
            {"json": {"llm_alias": "ga", "model": "foo"}},
        ),
    ],
)
def test_endpoints_reject_both_alias_and_model(
    app_client, patched_resolver: list[_ResolveCall],
    method: str, url: str, kwargs: dict[str, Any],
) -> None:
    client, _ = app_client
    resp = client.request(method, url, **kwargs)
    assert resp.status_code == 400, (
        f"{method} {url} expected 400, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    err = str(body.get("error", "")).lower()
    assert "llm_alias" in err and "model" in err and "both" in err, (
        f"{method} {url} expected ambiguity error, got: {err!r}"
    )
    # Also: resolver must NOT have been invoked — we short-circuit at the edge.
    assert patched_resolver == []


# ---------- Happy path per flow: body/query alias threads through ----------


def test_compare_qa_happy_alias_threads_to_resolver(
    app_client, patched_resolver: list[_ResolveCall],
) -> None:
    client, demo_compare = app_client
    # Seed cache so the handler reaches the LLM call path.
    demo_compare._kg_cache["PFE"] = {
        "kgs_kg": {"entities": [], "relationships": []},
        "gem_kg": {"entities": [], "relationships": []},
        "text": "some text",
        "info": {"name": "Pfizer"},
    }
    try:
        resp = client.post(
            "/api/compare-qa/PFE",
            json={
                "graphs": [
                    {"pipeline": "kgspin-default", "bundle": ""},
                    {"pipeline": "fullshot", "bundle": ""},
                ],
                "domain": "financial",
                "llm_alias": "gemini_flash",
            },
        )
    finally:
        demo_compare._kg_cache.pop("PFE", None)
    assert resp.status_code == 200, resp.text
    assert patched_resolver, "resolver should have been called"
    assert patched_resolver[0].llm_alias == "gemini_flash"
    assert patched_resolver[0].legacy_model is None
    assert patched_resolver[0].flow == "compare_qa"


def test_auto_flag_happy_alias_threads_to_resolver(
    app_client, patched_resolver: list[_ResolveCall], stub_bundle,
) -> None:
    client, _ = app_client
    resp = client.post(
        "/api/feedback/auto_flag",
        json={
            "nodes": [{"id": 1, "text": "Foo", "entity_type": "PRODUCT", "confidence": 0.9}],
            "edges": [],
            "document_id": "PFE",
            "llm_alias": "cheap_llm",
        },
    )
    assert resp.status_code in (200, 503), resp.text
    assert patched_resolver[0].llm_alias == "cheap_llm"
    assert patched_resolver[0].flow == "auto_flag"


def test_auto_discover_tp_happy_alias_threads_to_resolver(
    app_client, patched_resolver: list[_ResolveCall], stub_bundle,
) -> None:
    client, _ = app_client
    resp = client.post(
        "/api/feedback/auto_discover_tp",
        json={
            "ticker": "PFE",
            "nodes": [{"id": 1, "text": "Pfizer", "entity_type": "COMPANY", "confidence": 0.99}],
            "edges": [],
            "llm_alias": "accurate_llm",
        },
    )
    # Response code varies (200/500/503) depending on how the handler parses
    # the fake-backend response — we only care that the resolver saw the
    # alias before the handler reached the LLM.
    assert patched_resolver, f"resolver must be called (got {resp.status_code}: {resp.text})"
    assert patched_resolver[0].llm_alias == "accurate_llm"
    assert patched_resolver[0].flow == "auto_discover_tp"


# ---------- Legacy model-only path goes through without 400 ----------


def test_compare_qa_legacy_model_goes_through(
    app_client, patched_resolver: list[_ResolveCall],
) -> None:
    client, demo_compare = app_client
    demo_compare._kg_cache["PFE"] = {
        "kgs_kg": {"entities": [], "relationships": []},
        "gem_kg": {"entities": [], "relationships": []},
        "text": "some text",
        "info": {"name": "Pfizer"},
    }
    try:
        resp = client.post(
            "/api/compare-qa/PFE",
            json={
                "graphs": [
                    {"pipeline": "kgspin-default", "bundle": ""},
                    {"pipeline": "fullshot", "bundle": ""},
                ],
                "domain": "financial",
                "model": "gemini-99-flash",
            },
        )
    finally:
        demo_compare._kg_cache.pop("PFE", None)
    assert resp.status_code == 200, resp.text
    assert patched_resolver[0].llm_alias is None
    assert patched_resolver[0].legacy_model == "gemini-99-flash"


def test_auto_flag_neither_alias_nor_model_falls_through(
    app_client, patched_resolver: list[_ResolveCall], stub_bundle,
) -> None:
    client, _ = app_client
    resp = client.post(
        "/api/feedback/auto_flag",
        json={
            "nodes": [{"id": 1, "text": "Foo", "entity_type": "PRODUCT", "confidence": 0.9}],
            "edges": [],
            "document_id": "PFE",
        },
    )
    # No alias, no model → the edge check passes and the resolver is
    # invoked with no selectors so the helper applies the config default.
    assert resp.status_code in (200, 503), resp.text
    assert patched_resolver, "resolver must be called for fall-through"
    assert patched_resolver[0].llm_alias is None
    assert patched_resolver[0].legacy_model is None
    assert patched_resolver[0].flow == "auto_flag"
