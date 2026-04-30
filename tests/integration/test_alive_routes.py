"""Alive-but-unused-by-UI route guards.

PRD-004 v5 Phase 5A fixup-20260430 commit 4 / VP-Eng M5.

The frontend wiring for ``/api/why-this-matters/{doc_id}`` (Sprint 05
HITL-r2 Q&A flow) and ``/api/compare-qa/{doc_id}`` (Sprint 91 slot-Q&A
flow) is removed in commit 6. Per VP-Prod #4 multihop posture, the
backend routes stay alive until Phase 5B so a hotfix can re-introduce
a UI button without re-implementing the backend.

These tests prove the routes are still **registered** (not 404'd)
after commit 6 deletes the frontend callers. They don't exercise
the full LLM/cache flow — just the route-registration contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_EXTRACTION = PROJECT_ROOT / "demos" / "extraction"
sys.path.insert(0, str(DEMO_EXTRACTION))


@pytest.fixture
def app_client():
    import demo_compare
    return TestClient(demo_compare.app)


def test_why_this_matters_route_is_registered(app_client):
    """GET /api/why-this-matters/{doc_id} returns ANY non-404 status —
    proves the route is wired even after commit 6's frontend deletion.

    The actual response shape depends on cache state (this test runs
    with no cached KG, so the endpoint likely returns a structured
    error in 200 or a 4xx; either is fine — anything but 404).
    """
    resp = app_client.get("/api/why-this-matters/ZZZZ?domain=financial")
    assert resp.status_code != 404, (
        f"/api/why-this-matters/{{doc_id}} returned 404 — route was "
        f"deleted by mistake. Per fixup F8 + VP-Prod #4 posture the "
        f"backend route stays alive until Phase 5B."
    )


def test_compare_qa_route_is_registered(app_client):
    """POST /api/compare-qa/{doc_id} returns ANY non-404 status —
    proves the route is wired even after commit 6's frontend deletion.

    Per fixup F8 + VP-Prod #4 posture: backend stays alive until 5B.
    """
    resp = app_client.post(
        "/api/compare-qa/ZZZZ",
        json={"question": "test", "pipeline": "kgenskills"},
    )
    assert resp.status_code != 404, (
        f"/api/compare-qa/{{doc_id}} returned 404 — route was deleted "
        f"by mistake. Per fixup F8 + VP-Prod #4 posture the backend "
        f"route stays alive until Phase 5B."
    )


def test_multihop_run_route_is_registered(app_client):
    """POST /api/multihop/run returns ANY non-404 status — proves the
    Wave-G route stays alive after the v4 multi-hop UI was deleted in
    the predecessor sprint commit 9a. Same VP-Prod #4 posture.
    """
    resp = app_client.post(
        "/api/multihop/run",
        json={"doc_id": "ZZZZ", "scenario_id": "x", "slot_pipelines": [None, None, None]},
    )
    assert resp.status_code != 404
