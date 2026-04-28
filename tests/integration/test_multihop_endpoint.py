"""Integration test for ``/api/multihop/run`` (PRD-004 v4 #10).

Uses FastAPI's TestClient against the in-process app. The Gemini backend
is replaced with a queue-driven fake so we get deterministic answer text
and judge JSON without burning real LLM calls.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Iterator

import pytest


@dataclass
class _FakeBackendResult:
    text: str
    tokens_used: int = 100
    model: str = "gemini-2.5-flash"
    finish_reason: str = "stop"


class _FakeBackend:
    """Pops from a thread-safe queue of pre-canned responses."""

    def __init__(self, responses: list[str], *, sleep_s: float = 0.0):
        from threading import Lock

        self._responses: list[str] = list(responses)
        self._lock = Lock()
        self._sleep_s = sleep_s
        self.calls: list[dict] = []

    def complete(self, prompt: str, **kwargs) -> _FakeBackendResult:
        with self._lock:
            self.calls.append({"prompt": prompt, "kwargs": kwargs})
            if not self._responses:
                raise RuntimeError("FakeBackend exhausted")
            text = self._responses.pop(0)
        if self._sleep_s:
            time.sleep(self._sleep_s)
        return _FakeBackendResult(text=text)


@pytest.fixture
def app_with_seeded_kg() -> Iterator[tuple]:
    """Boot the FastAPI app, seed _kg_cache with three pipelines worth
    of KGs, and yield (TestClient, fake_backend)."""
    from fastapi.testclient import TestClient

    from demos.extraction import demo_compare as dc

    kg_kgspin = {
        "entities": [
            {"text": f"E{i}", "entity_type": "ORG", "confidence": 1.0}
            for i in range(30)
        ],
        "relationships": [
            {
                "subject": {"text": f"E{i}"},
                "object": {"text": f"E{(i + 1) % 30}"},
                "predicate": "links_to",
                "confidence": 1.0,
            }
            for i in range(50)
        ],
    }
    kg_llm = {
        "entities": [
            {"text": "Johnson & Johnson", "entity_type": "ORG", "confidence": 1.0},
            {"text": "Abiomed", "entity_type": "ORG", "confidence": 1.0},
        ],
        "relationships": [
            {
                "subject": {"text": "Johnson & Johnson"},
                "object": {"text": "Abiomed"},
                "predicate": "acquired",
                "confidence": 1.0,
            }
        ],
    }

    with dc._cache_lock:
        dc._kg_cache["TEST"] = {
            "kgs_kg": kg_kgspin,
            "gem_kg": kg_llm,
            "mod_kg": kg_llm,
            "text": "seeded",
        }

    answer_text = (
        "Johnson & Johnson acquired Abiomed in 2022. The acquisition "
        "expanded its medical-device portfolio."
    )
    judge_json = json.dumps({
        "ranking": ["A", "C", "B"],
        "rationales": {
            "A": "Most specific.",
            "B": "Hedges with 'likely'.",
            "C": "Solid coverage.",
        },
    })
    fake_backend = _FakeBackend([answer_text, answer_text, answer_text, judge_json])

    import demos.extraction.judge as judge_mod
    import kgspin_demo_app.llm_backend as llm_backend_mod

    original_resolve = llm_backend_mod.resolve_llm_backend
    original_judge_resolve = judge_mod.resolve_llm_backend

    def _stub_resolve(*args, **kwargs):
        return fake_backend

    llm_backend_mod.resolve_llm_backend = _stub_resolve
    judge_mod.resolve_llm_backend = _stub_resolve

    try:
        client = TestClient(dc.app)
        yield client, fake_backend
    finally:
        llm_backend_mod.resolve_llm_backend = original_resolve
        judge_mod.resolve_llm_backend = original_judge_resolve
        with dc._cache_lock:
            dc._kg_cache.pop("TEST", None)


def test_multihop_run_happy_path(app_with_seeded_kg):
    client, fake_backend = app_with_seeded_kg
    resp = client.post(
        "/api/multihop/run",
        json={
            "doc_id": "TEST",
            "scenario_id": "acquisitions_litigation_3hop",
            "slot_pipelines": ["fan_out", "agentic_flash", "agentic_analyst"],
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["scenario"]["id"] == "acquisitions_litigation_3hop"
    assert len(payload["answers"]) == 3
    pipelines = [a["pipeline"] for a in payload["answers"]]
    assert pipelines == ["fan_out", "agentic_flash", "agentic_analyst"]
    for a in payload["answers"]:
        assert a["answer_text"]
        assert a["latency_ms"] >= 0
        assert isinstance(a["topology_health"], dict)
        assert "score" in a["topology_health"]
    assert "ranking" in payload["judge"]
    assert sorted(payload["judge"]["ranking"]) == ["A", "B", "C"]


def test_multihop_run_judge_prompt_does_not_leak_pipeline_names(app_with_seeded_kg):
    client, fake_backend = app_with_seeded_kg
    resp = client.post(
        "/api/multihop/run",
        json={
            "doc_id": "TEST",
            "scenario_id": "acquisitions_litigation_3hop",
            "slot_pipelines": ["fan_out", "agentic_flash", "agentic_analyst"],
        },
    )
    assert resp.status_code == 200
    judge_call = fake_backend.calls[-1]
    prompt = judge_call["prompt"]
    for label in ("fan_out", "agentic_flash", "agentic_analyst"):
        assert label not in prompt, f"judge prompt leaked pipeline name: {label}"


def test_multihop_run_unknown_scenario(app_with_seeded_kg):
    client, _ = app_with_seeded_kg
    resp = client.post(
        "/api/multihop/run",
        json={
            "doc_id": "TEST",
            "scenario_id": "no_such_scenario",
            "slot_pipelines": ["fan_out", "agentic_flash", "agentic_analyst"],
        },
    )
    assert resp.status_code == 404
    assert "unknown scenario" in resp.json()["error"]


def test_multihop_run_wrong_pipeline_count(app_with_seeded_kg):
    client, _ = app_with_seeded_kg
    resp = client.post(
        "/api/multihop/run",
        json={
            "doc_id": "TEST",
            "scenario_id": "acquisitions_litigation_3hop",
            "slot_pipelines": ["fan_out", "agentic_flash"],
        },
    )
    assert resp.status_code == 400
    assert "exactly 3" in resp.json()["error"]


def test_multihop_run_unknown_pipeline(app_with_seeded_kg):
    client, _ = app_with_seeded_kg
    resp = client.post(
        "/api/multihop/run",
        json={
            "doc_id": "TEST",
            "scenario_id": "acquisitions_litigation_3hop",
            "slot_pipelines": ["fan_out", "agentic_flash", "made_up_pipeline"],
        },
    )
    assert resp.status_code == 400
    assert "unknown pipeline" in resp.json()["error"]


def test_multihop_run_cache_miss_returns_partial(app_with_seeded_kg):
    """If a slot has no cached KG, the answer for that slot surfaces an
    error string and the judge is skipped (fewer than 3 valid answers)."""
    from demos.extraction import demo_compare as dc

    with dc._cache_lock:
        # Drop one slot's cached KG.
        if "TEST" in dc._kg_cache:
            dc._kg_cache["TEST"].pop("mod_kg", None)

    client, _ = app_with_seeded_kg
    resp = client.post(
        "/api/multihop/run",
        json={
            "doc_id": "TEST",
            "scenario_id": "acquisitions_litigation_3hop",
            "slot_pipelines": ["fan_out", "agentic_flash", "agentic_analyst"],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    answers = {a["pipeline"]: a for a in payload["answers"]}
    assert answers["agentic_analyst"]["answer_text"] is None
    assert "no cached KG" in answers["agentic_analyst"]["error"]
    # Judge skipped.
    assert "error" in payload["judge"] and "valid answers" in payload["judge"]["error"]


def test_multihop_run_parallel_dispatch_under_per_call_latency(app_with_seeded_kg):
    """Three answer calls run in parallel via asyncio.to_thread; total
    wall-time should be substantially less than 3 * per-call latency.
    Use a per-call sleep of 0.4s and assert wall-time < 0.8s
    (well under the serial 1.2s lower bound)."""
    from demos.extraction import demo_compare as dc

    answer_text = "Johnson & Johnson acquired Abiomed in 2022."
    judge_json = json.dumps({
        "ranking": ["A", "B", "C"],
        "rationales": {"A": "x", "B": "y", "C": "z"},
    })

    slow_backend = _FakeBackend(
        [answer_text, answer_text, answer_text, judge_json], sleep_s=0.4
    )

    import demos.extraction.judge as judge_mod
    import kgspin_demo_app.llm_backend as llm_backend_mod

    original = llm_backend_mod.resolve_llm_backend
    original_judge = judge_mod.resolve_llm_backend
    llm_backend_mod.resolve_llm_backend = lambda *a, **k: slow_backend
    judge_mod.resolve_llm_backend = lambda *a, **k: slow_backend
    try:
        client, _ = app_with_seeded_kg
        t0 = time.perf_counter()
        resp = client.post(
            "/api/multihop/run",
            json={
                "doc_id": "TEST",
                "scenario_id": "acquisitions_litigation_3hop",
                "slot_pipelines": ["fan_out", "agentic_flash", "agentic_analyst"],
            },
        )
        elapsed = time.perf_counter() - t0
    finally:
        llm_backend_mod.resolve_llm_backend = original
        judge_mod.resolve_llm_backend = original_judge

    assert resp.status_code == 200
    # Serial would be ~1.6s (3 * 0.4 + 0.4 judge); parallel should be
    # ~0.8s (max of three + 0.4 judge). Assert under 1.3s with margin.
    assert elapsed < 1.3, f"multihop took {elapsed:.2f}s; expected parallel dispatch"


def test_topology_health_endpoint(app_with_seeded_kg):
    client, _ = app_with_seeded_kg
    resp = client.get("/api/topology-health/TEST/fan_out")
    assert resp.status_code == 200
    payload = resp.json()
    assert "score" in payload
    # No cached KG case
    resp2 = client.get("/api/topology-health/UNKNOWN/fan_out")
    assert resp2.status_code == 200
    assert resp2.json()["score"] == -1
