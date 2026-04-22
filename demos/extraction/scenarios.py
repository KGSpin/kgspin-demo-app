"""Multi-hop scenario pack loader (PRD-004 v4 #9).

Loads `multihop_scenarios.yaml` into frozen `Scenario` dataclasses.
The YAML is read once at module import.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_SCENARIOS_PATH = Path(__file__).parent / "multihop_scenarios.yaml"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    domain: str
    question: str
    expected_hops: int
    talking_track: str


def _load_from_disk() -> list[Scenario]:
    raw = yaml.safe_load(_SCENARIOS_PATH.read_text())
    items = raw.get("scenarios") or []
    out: list[Scenario] = []
    for entry in items:
        out.append(Scenario(
            scenario_id=str(entry["scenario_id"]),
            domain=str(entry["domain"]),
            question=str(entry["question"]),
            expected_hops=int(entry["expected_hops"]),
            talking_track=str(entry["talking_track"]).rstrip(),
        ))
    return out


_CACHE: list[Scenario] | None = None


def load_scenarios() -> list[Scenario]:
    """Return all scenarios. Cached after first read."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_from_disk()
    return list(_CACHE)


def get_scenario(scenario_id: str) -> Scenario:
    """Return one scenario by id. Raises KeyError if absent."""
    for s in load_scenarios():
        if s.scenario_id == scenario_id:
            return s
    raise KeyError(scenario_id)


def scenario_to_dict(s: Scenario) -> dict:
    """Serialize one scenario for JSON responses."""
    return {
        "id": s.scenario_id,
        "domain": s.domain,
        "question": s.question,
        "expected_hops": s.expected_hops,
        "talking_track": s.talking_track,
    }
