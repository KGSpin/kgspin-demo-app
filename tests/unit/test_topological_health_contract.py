"""Cross-repo contract self-test for kgspin-core's TopologicalHealth.

Pinned in /tmp/cto/wave-g-20260422/common-preamble.md. Any drift in the
dataclass shape or the compute_health signature breaks the demo's
topology badge integration. This test is the single artifact that proves
the cross-repo handshake works.

Skips gracefully when kgspin-core has not yet shipped the module so CI
stays green during the parallel sprint; the sentinel shim in
``services.topology_health`` keeps the UI functional in that window.
"""
from __future__ import annotations

import importlib.util
from dataclasses import fields, is_dataclass

import pytest

def _core_available() -> bool:
    try:
        return importlib.util.find_spec("kgspin_core.graph_topology.health") is not None
    except ModuleNotFoundError:
        return False


_CORE_AVAILABLE = _core_available()
_SKIP_REASON = (
    "kgspin_core.graph_topology.health not yet importable; cross-repo "
    "Wave G handshake pending. Sentinel shim in services.topology_health "
    "keeps the demo functional in the meantime."
)


@pytest.mark.skipif(not _CORE_AVAILABLE, reason=_SKIP_REASON)
def test_topological_health_dataclass_shape():
    from kgspin_core.graph_topology.health import TopologicalHealth

    assert is_dataclass(TopologicalHealth)
    field_types = {f.name: f.type for f in fields(TopologicalHealth)}
    expected = {
        "score",
        "connectivity",
        "bridge_density",
        "mean_hop_length",
        "degree_gini",
        "node_count",
        "edge_count",
        "insufficient_reason",
    }
    assert set(field_types) == expected, (
        f"TopologicalHealth fields drifted from CTO-pinned contract; "
        f"expected {expected}, got {set(field_types)}"
    )


@pytest.mark.skipif(not _CORE_AVAILABLE, reason=_SKIP_REASON)
def test_compute_health_signature_takes_kg_kwarg_min_nodes():
    import inspect

    from kgspin_core.graph_topology.health import compute_health

    sig = inspect.signature(compute_health)
    assert "min_nodes" in sig.parameters, (
        "compute_health(kg, *, min_nodes=20) — min_nodes kwarg missing"
    )


@pytest.mark.skipif(not _CORE_AVAILABLE, reason=_SKIP_REASON)
def test_compute_health_empty_kg_returns_score_negative_one():
    """Empty / undersized KG must return score=-1 with insufficient_reason set."""
    from kgspin_demo_app.services.topology_health import _dict_to_kg
    from kgspin_core.graph_topology.health import compute_health

    empty_kg = _dict_to_kg({"entities": [], "relationships": []})
    th = compute_health(empty_kg)
    assert th.score == -1
    assert th.insufficient_reason is not None and th.insufficient_reason


@pytest.mark.skipif(not _CORE_AVAILABLE, reason=_SKIP_REASON)
def test_compute_health_dense_kg_returns_score_in_range():
    """A small but dense KG should score in [0, 100]."""
    from kgspin_demo_app.services.topology_health import _dict_to_kg
    from kgspin_core.graph_topology.health import compute_health

    entities = [
        {"text": f"E{i}", "entity_type": "ORG", "confidence": 1.0}
        for i in range(30)
    ]
    relationships = [
        {
            "subject": {"text": f"E{i}"},
            "object": {"text": f"E{(i + 1) % 30}"},
            "predicate": "links_to",
            "confidence": 1.0,
        }
        for i in range(60)
    ]
    kg = _dict_to_kg({"entities": entities, "relationships": relationships})
    th = compute_health(kg, min_nodes=20)
    assert 0 <= th.score <= 100


def test_sentinel_returns_when_core_module_absent():
    """When the core module is missing, health_for_kg must return a sentinel
    (score=-1) rather than raising — this is what keeps the demo UI alive
    during the cross-repo handshake window.
    """
    from kgspin_demo_app.services.topology_health import health_for_kg

    if _CORE_AVAILABLE:
        pytest.skip("core module is available; sentinel-fallback path not exercised")
    result = health_for_kg({"entities": [{"text": "x", "entity_type": "ORG"}], "relationships": []})
    assert result["score"] == -1
    assert "not yet available" in (result.get("insufficient_reason") or "")


def test_health_for_kg_handles_none_input():
    """None / empty inputs always return a sentinel, regardless of core
    module availability."""
    from kgspin_demo_app.services.topology_health import health_for_kg

    assert health_for_kg(None)["score"] == -1
    assert health_for_kg({})["score"] == -1
