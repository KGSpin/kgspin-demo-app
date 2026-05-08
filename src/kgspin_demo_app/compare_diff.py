"""Python mirror of demos/extraction/static/js/diff.js::computeTrainedDiff.

Used by the trained-pipeline smoke test (and any server-side caller
that wants the same trained-vs-heuristic comparison the /compare UI
displays). The two implementations must stay behavior-identical: the
unit tests in ``tests/unit/test_compare_diff.py`` pin the same set of
cases the Node-driven JS tests pin.
"""

from __future__ import annotations

import re
from typing import Any


_WS_RE = re.compile(r"\s+")


def _normalize_surface(s: Any) -> str:
    return _WS_RE.sub(" ", str(s or "").lower()).strip()


def _entities_by_type(entities: list[dict]) -> dict[str, dict[str, str]]:
    by_type: dict[str, dict[str, str]] = {}
    for e in entities or []:
        etype = e.get("type") or e.get("entity_type") or "UNKNOWN"
        surface = (
            e.get("surface")
            or e.get("name")
            or e.get("text")
            or e.get("surface_form")
            or ""
        )
        norm = _normalize_surface(surface)
        if not norm:
            continue
        bucket = by_type.setdefault(etype, {})
        bucket.setdefault(norm, surface)
    return by_type


def compute_trained_diff(
    slot_state_a: dict, slot_state_b: dict,
) -> dict:
    """Compute trained-vs-heuristic diff between two slot KGs.

    Each ``slot_state`` is expected to be a dict with a ``kg`` key whose
    value is a dict containing an ``entities`` list. Each entity is a
    dict with at least ``type`` and a surface field (``surface`` /
    ``name`` / ``text`` / ``surface_form``).

    Returns a dict with:
      - ``by_type``: ``{type: {a_count, b_count, delta}}``
      - ``only_in_a`` / ``only_in_b`` / ``agreed``: lists of
        ``{type, surface}`` entries
      - ``total_a`` / ``total_b``: total entity counts per slot

    Set diff is per-type: same surface in different types is NOT folded
    into the same bucket. Surfaces are normalized via casefold +
    whitespace collapse.
    """
    ents_a = ((slot_state_a or {}).get("kg") or {}).get("entities") or []
    ents_b = ((slot_state_b or {}).get("kg") or {}).get("entities") or []

    a_by_type = _entities_by_type(ents_a)
    b_by_type = _entities_by_type(ents_b)

    all_types = set(a_by_type.keys()) | set(b_by_type.keys())

    by_type: dict[str, dict[str, int]] = {}
    only_in_a: list[dict[str, str]] = []
    only_in_b: list[dict[str, str]] = []
    agreed: list[dict[str, str]] = []

    for t in all_types:
        a_map = a_by_type.get(t, {})
        b_map = b_by_type.get(t, {})
        by_type[t] = {
            "a_count": len(a_map),
            "b_count": len(b_map),
            "delta": len(b_map) - len(a_map),
        }
        for norm, surface in a_map.items():
            if norm in b_map:
                agreed.append({"type": t, "surface": surface})
            else:
                only_in_a.append({"type": t, "surface": surface})
        for norm, surface in b_map.items():
            if norm not in a_map:
                only_in_b.append({"type": t, "surface": surface})

    return {
        "by_type": by_type,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "agreed": agreed,
        "total_a": len(only_in_a) + len(agreed),
        "total_b": len(only_in_b) + len(agreed),
    }
