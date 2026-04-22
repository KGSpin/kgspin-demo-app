"""Topological Health adapter (PRD-055 #1).

Wraps ``kgspin_core.graph_topology.health.compute_health`` so the demo
can score a kgspin-shaped dict KG and serialize the result for the
frontend. When the core module is not yet importable (cross-repo
handshake in progress) we return a sentinel so the UI degrades to a
"-" badge instead of crashing.

The cross-repo contract is pinned in
``/tmp/cto/wave-g-20260422/common-preamble.md`` and verified by
``tests/unit/test_topological_health_contract.py``.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any


_SENTINEL_REASON_NO_MODULE = "kgspin-core graph_topology not yet available"


def _sentinel(reason: str) -> dict:
    return {
        "score": -1,
        "connectivity": 0.0,
        "bridge_density": 0.0,
        "mean_hop_length": None,
        "degree_gini": 0.0,
        "node_count": 0,
        "edge_count": 0,
        "insufficient_reason": reason,
    }


def _normalize_serialized(payload: dict) -> dict:
    """Replace non-JSON-serializable floats (inf, nan) with None."""
    import math
    out = dict(payload)
    val = out.get("mean_hop_length")
    if isinstance(val, float) and (math.isinf(val) or math.isnan(val)):
        out["mean_hop_length"] = None
    return out


def health_for_kg(kg_dict: dict | None) -> dict:
    """Compute a topological health badge for a kgspin-shaped dict KG.

    Returns a JSON-friendly dict matching the ``TopologicalHealth``
    dataclass shape (with ``mean_hop_length`` coerced to None if
    infinite, so the JSON encoder doesn't choke). On any failure
    (missing core module, bad shape) returns the sentinel with
    ``score: -1`` and ``insufficient_reason`` populated.
    """
    if not kg_dict or not isinstance(kg_dict, dict):
        return _sentinel("empty KG")

    try:
        from kgspin_core.graph_topology.health import compute_health
    except ImportError:
        return _sentinel(_SENTINEL_REASON_NO_MODULE)

    try:
        kg = _dict_to_kg(kg_dict)
        th = compute_health(kg)
    except Exception as e:
        return _sentinel(f"compute_health failed: {type(e).__name__}: {e}")

    return _normalize_serialized(asdict(th))


def _dict_to_kg(kg_dict: dict) -> Any:
    """Adapt the demo's dict KG to whatever shape ``compute_health`` accepts.

    The contract pins the function signature as
    ``compute_health(kg: KnowledgeGraph) -> TopologicalHealth``. The demo
    threads dict KGs around; we wrap them in a tiny duck-typed object that
    exposes ``entities`` and ``relationships`` lists. If kgspin-core's
    typed ``KnowledgeGraph`` becomes importable later, we can swap to it.
    """
    entities = list(kg_dict.get("entities") or [])
    relationships = list(kg_dict.get("relationships") or [])
    return _DuckKG(entities=entities, relationships=relationships)


class _DuckKG:
    """Duck-typed KG matching what compute_health is expected to read.

    Exposes ``entities`` and ``relationships`` as lists of dicts (the
    demo's native shape). compute_health iterates these for nodes and
    edges; per the preamble it does no other I/O.
    """

    __slots__ = ("entities", "relationships")

    def __init__(self, entities: list[dict], relationships: list[dict]):
        self.entities = entities
        self.relationships = relationships
