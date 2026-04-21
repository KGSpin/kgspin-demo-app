"""Sprint 12 Task 3+11 — admin_registry_reader cache + circuit-breaker tests.

Pins the VP Eng Phase 1 condition on Task 3: "the per-request cache
MUST implement strict-timeout + circuit-breaker behavior... Tests pin
both happy path and degraded path."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from kgspin_interface.registry_client import ResourceKind
from kgspin_interface.resources import CustomPointer, Resource, ResourceStatus, Provenance

from kgspin_demo_app.services import admin_registry_reader as reader


# --- Test fake: counts calls, can be configured to fail + return data ------


class CountingFakeClient:
    """Minimal ResourceRegistryClient stand-in for breaker + cache tests.

    Not a full FakeRegistryClient replacement — just the ``list``
    surface plus hooks for tests to inject failures / responses.
    """

    def __init__(self) -> None:
        self.list_calls: list[tuple[ResourceKind, dict[str, Any]]] = []
        self._response: list[Resource] = []
        self._raise: Exception | None = None

    def set_response(self, resources: list[Resource]) -> None:
        self._response = resources
        self._raise = None

    def set_failure(self, exc: Exception) -> None:
        self._raise = exc

    def list(self, kind: ResourceKind, **kwargs: Any) -> list[Resource]:
        self.list_calls.append((kind, dict(kwargs)))
        if self._raise is not None:
            raise self._raise
        return list(self._response)


def _pipeline_resource(name: str, **extras: Any) -> Resource:
    from datetime import datetime, timezone
    meta = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} test description",
        "fusion_policy": "union",
        "backends_used": ("deterministic",),
    }
    meta.update(extras)
    return Resource(
        id=f"pipeline_config:{name}:1.0.0",
        kind=ResourceKind.PIPELINE_CONFIG,
        pointer=CustomPointer(scheme="archetype-yaml", value=f"{name}.yaml"),
        metadata=meta,
        provenance=Provenance(
            registered_at=datetime.now(timezone.utc),
            registered_by="test",
            hash=None,
        ),
        status=ResourceStatus.ACTIVE,
    )


@pytest.fixture(autouse=True)
def _reset_reader_state() -> None:
    """Each test starts with a clean cache + breaker."""
    reader.reset_caches_for_testing()


# --- Happy-path reads ------------------------------------------------------


def test_admin_returns_pipelines_translates_to_ui_slot_shape() -> None:
    client = CountingFakeClient()
    client.set_response([
        _pipeline_resource(
            "agentic_flash",
            diagnostics={"demo_ui": {
                "label": "Agentic Flash",
                "tagline": "LLM single-prompt",
                "capability": "Agentic",
                "pipeline_id": "discovery-deep",
                "backend": "gemini",
                "help_anchor": "agentic-flash",
            }},
        ),
    ])
    slots = reader.list_pipeline_configs(client)
    assert len(slots) == 1
    assert slots[0]["id"] == "agentic_flash"
    assert slots[0]["label"] == "Agentic Flash"
    assert slots[0]["capability"] == "Agentic"
    assert slots[0]["backend"] == "gemini"


def test_cache_hit_within_ttl_skips_admin(monkeypatch) -> None:
    client = CountingFakeClient()
    client.set_response([_pipeline_resource("a")])

    fixed_time = [1000.0]
    reader.list_pipeline_configs(client, now=lambda: fixed_time[0])
    assert len(client.list_calls) == 1

    # Second call 0.5s later — still within 2s TTL.
    fixed_time[0] += 0.5
    reader.list_pipeline_configs(client, now=lambda: fixed_time[0])
    assert len(client.list_calls) == 1  # no new admin call


def test_cache_miss_after_ttl_refreshes() -> None:
    client = CountingFakeClient()
    client.set_response([_pipeline_resource("a")])

    fixed_time = [1000.0]
    reader.list_pipeline_configs(client, now=lambda: fixed_time[0])

    # 3s later — past 2s TTL, new admin call expected.
    fixed_time[0] += 3.0
    reader.list_pipeline_configs(client, now=lambda: fixed_time[0])
    assert len(client.list_calls) == 2


# --- Circuit-breaker behavior (VP Eng condition) ---------------------------


def test_single_failure_does_not_trip_breaker() -> None:
    client = CountingFakeClient()
    client.set_failure(RuntimeError("admin unreachable"))

    reader.list_pipeline_configs(client, now=lambda: 1000.0)
    assert reader._pipelines_breaker.failure_count == 1
    assert not reader._pipelines_breaker.is_tripped(1000.0)


def test_N_failures_trips_breaker() -> None:
    client = CountingFakeClient()
    client.set_failure(RuntimeError("admin unreachable"))
    fixed_time = [1000.0]

    for _ in range(reader.FAILURE_THRESHOLD):
        fixed_time[0] += 3.0  # past TTL each time so admin IS called
        reader.list_pipeline_configs(client, now=lambda: fixed_time[0])

    assert reader._pipelines_breaker.is_tripped(fixed_time[0])


def test_tripped_breaker_serves_last_known_good_without_calling_admin() -> None:
    client = CountingFakeClient()

    # Prime with a good response.
    client.set_response([_pipeline_resource("a")])
    reader.list_pipeline_configs(client, now=lambda: 1000.0)
    baseline_calls = len(client.list_calls)

    # Flip to failing + drive through the threshold.
    client.set_failure(RuntimeError("admin unreachable"))
    fixed_time = [1003.0]
    for _ in range(reader.FAILURE_THRESHOLD):
        fixed_time[0] += 3.0
        reader.list_pipeline_configs(client, now=lambda: fixed_time[0])

    assert reader._pipelines_breaker.is_tripped(fixed_time[0])
    failing_calls = len(client.list_calls) - baseline_calls

    # Further reads during cooldown must NOT hit admin.
    fixed_time[0] += 3.0
    slots = reader.list_pipeline_configs(client, now=lambda: fixed_time[0])
    assert len(client.list_calls) - baseline_calls == failing_calls  # unchanged
    assert slots[0]["id"] == "a"  # last-known-good served


def test_breaker_resets_after_cooldown() -> None:
    client = CountingFakeClient()
    client.set_failure(RuntimeError("admin unreachable"))
    fixed_time = [1000.0]
    for _ in range(reader.FAILURE_THRESHOLD):
        fixed_time[0] += 3.0
        reader.list_pipeline_configs(client, now=lambda: fixed_time[0])
    assert reader._pipelines_breaker.is_tripped(fixed_time[0])

    # Fast-forward past cooldown.
    fixed_time[0] += reader.BREAKER_COOLDOWN_SECONDS + 1.0
    client.set_response([_pipeline_resource("recovered")])

    slots = reader.list_pipeline_configs(client, now=lambda: fixed_time[0])
    assert slots[0]["id"] == "recovered"
    assert reader._pipelines_breaker.failure_count == 0


# --- Seed fallback path ----------------------------------------------------


def test_empty_admin_plus_seed_fallback_returns_fallback(tmp_path: Path) -> None:
    seed = tmp_path / "ui_slots.yaml"
    seed.write_text(yaml.dump({
        "slots": [
            {"id": "discovery_rapid", "label": "Rapid", "capability": "Discovery",
             "pipeline_id": "discovery-rapid", "backend": "deterministic"},
        ]
    }))
    client = CountingFakeClient()
    client.set_response([])  # empty admin

    slots = reader.list_pipeline_configs(client, seed_fallback_path=seed)
    assert len(slots) == 1
    assert slots[0]["id"] == "discovery_rapid"


def test_empty_admin_no_seed_returns_empty_list() -> None:
    """Missing fallback → UI renders the 'No pipelines available' copy."""
    client = CountingFakeClient()
    client.set_response([])

    slots = reader.list_pipeline_configs(client, seed_fallback_path=None)
    assert slots == []


def test_admin_failure_no_cache_no_seed_returns_empty_list() -> None:
    client = CountingFakeClient()
    client.set_failure(RuntimeError("admin unreachable"))

    slots = reader.list_pipeline_configs(client, seed_fallback_path=None)
    assert slots == []


# --- Bundle dropdown read path --------------------------------------------


def _bundle_resource(name: str, domain: str, version: str = "1.0.0") -> Resource:
    from datetime import datetime, timezone
    return Resource(
        id=f"bundle_compiled:{domain}:{name}:{version}",
        kind=ResourceKind.BUNDLE_COMPILED,
        pointer=CustomPointer(scheme="archetype-yaml", value=f"{name}.json"),
        metadata={
            "name": name,
            "version": version,
            "domain": domain,
            "description": f"{domain} bundle {name}",
        },
        provenance=Provenance(
            registered_at=datetime.now(timezone.utc),
            registered_by="test",
            hash=None,
        ),
        status=ResourceStatus.ACTIVE,
    )


def test_bundle_reads_filter_by_domain() -> None:
    client = CountingFakeClient()
    client.set_response([
        _bundle_resource("fin-mvp-v1", "financial"),
        _bundle_resource("fin-mvp-v2", "financial"),
        _bundle_resource("clinical-mvp-v1", "clinical"),
    ])
    fin = reader.list_bundle_configs(client, domain="financial")
    cli = reader.list_bundle_configs(client, domain="clinical")
    assert {b["name"] for b in fin} == {"fin-mvp-v1", "fin-mvp-v2"}
    assert {b["name"] for b in cli} == {"clinical-mvp-v1"}


def test_bundle_reads_no_filter_returns_all() -> None:
    client = CountingFakeClient()
    client.set_response([
        _bundle_resource("a", "financial"),
        _bundle_resource("b", "clinical"),
    ])
    all_b = reader.list_bundle_configs(client)
    assert len(all_b) == 2
