"""Scenario template loader + placeholder resolver.

PRD-004 v5 Phase 5A — deliverable F. Loads the v5 scenario templates
from ``demos/extraction/multihop_scenarios_v5.yaml`` and resolves
``{company}`` / ``{ticker}`` / ``{year}`` / ``{drug}`` / ``{sponsor}``
/ ``{trial_id}`` placeholders against per-ticker metadata.

Anti-pattern (do NOT do): hardcoding ticker literals in the YAML.
The 2026-04-27 multihop genericization removed exactly this; v5
formalizes ``{company}`` style placeholders.

Public API:

    from kgspin_demo_app.services.scenario_resolver import (
        load_v5_templates, get_template, resolve, ScenarioResolutionError,
    )

    templates = load_v5_templates()           # 6 scenarios
    template = get_template("subsidiaries_litigation_jurisdiction")
    resolved = resolve(template, ticker="AAPL")
    # resolved.question is the placeholder-filled question text
    # resolved.bindings is the bindings dict that was applied
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# YAML lives next to demo_compare's other scenarios YAML. Tests can
# override via ``set_yaml_path``.
_DEFAULT_YAML_PATH = (
    Path(__file__).resolve().parents[3]
    / "demos" / "extraction" / "multihop_scenarios_v5.yaml"
)
_yaml_path = _DEFAULT_YAML_PATH

_template_cache: Optional[list["ScenarioTemplate"]] = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ScenarioResolutionError(Exception):
    """Raised when ``resolve`` can't fill every required placeholder."""

    def __init__(self, scenario_id: str, missing: list[str], ticker: str):
        self.scenario_id = scenario_id
        self.missing = missing
        self.ticker = ticker
        super().__init__(
            f"Cannot resolve scenario {scenario_id!r} for ticker {ticker!r}: "
            f"missing placeholders {missing!r}"
        )


class ScenarioNotFound(KeyError):
    """Raised when ``get_template(scenario_id)`` doesn't match any template."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioTemplate:
    scenario_id: str
    domain: str
    question_template: str
    expected_hops: int
    placeholders: tuple[str, ...]
    talking_track: str
    expected_difficulty: str
    key_fields: tuple[str, ...]
    # PRD-004 v5 Phase 5A fixup-20260430 F4a — `status` flag.
    # `"ready"` (default) = real scenario; `"scaffold"` = placeholder
    # entry that the picker shows with `(TBD)` and disables Run on.
    # Defaults to `"ready"` so existing YAML rows + tests stay green
    # without changes.
    status: str = "ready"


@dataclass(frozen=True)
class ResolvedScenario:
    scenario_id: str
    question: str
    bindings: dict[str, str]
    template: ScenarioTemplate


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def set_yaml_path(path: Path) -> None:
    """Override the YAML path (test hook)."""
    global _yaml_path, _template_cache
    _yaml_path = Path(path)
    _template_cache = None


def _load_yaml() -> list[ScenarioTemplate]:
    if not _yaml_path.exists():
        raise FileNotFoundError(
            f"Scenario YAML not found at {_yaml_path}. "
            f"Phase 5A ships demos/extraction/multihop_scenarios_v5.yaml."
        )
    raw = yaml.safe_load(_yaml_path.read_text(encoding="utf-8")) or {}
    items = raw.get("scenarios") or []
    out: list[ScenarioTemplate] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        scenario_id = it.get("scenario_id")
        if not scenario_id:
            logger.warning("Skipping scenario with missing scenario_id")
            continue
        out.append(ScenarioTemplate(
            scenario_id=str(scenario_id),
            domain=str(it.get("domain", "fin")),
            question_template=str(it.get("question_template", "")).strip(),
            expected_hops=int(it.get("expected_hops", 1)),
            placeholders=tuple(it.get("placeholders") or ()),
            talking_track=str(it.get("talking_track", "")).strip(),
            expected_difficulty=str(it.get("expected_difficulty", "medium")),
            key_fields=tuple(it.get("key_fields") or ()),
            status=str(it.get("status") or "ready"),
        ))
    return out


def load_v5_templates() -> list[ScenarioTemplate]:
    """Load (and cache) all Phase-5A templates."""
    global _template_cache
    if _template_cache is None:
        _template_cache = _load_yaml()
    return list(_template_cache)


def get_template(scenario_id: str) -> ScenarioTemplate:
    """Return the template with matching ``scenario_id``."""
    for t in load_v5_templates():
        if t.scenario_id == scenario_id:
            return t
    raise ScenarioNotFound(scenario_id)


# ---------------------------------------------------------------------------
# Bindings resolution
# ---------------------------------------------------------------------------


# Built-in metadata for the 7 financial demo tickers + the clinical hedge.
# Sourced from KNOWN_TICKERS (demos/extraction/pipeline_common.py) plus the
# CTG-derived sponsor/drug/trial mapping for JNJ-Stelara. Phase 5B will
# pull these from admin's corpus_document records.
TICKER_METADATA: dict[str, dict[str, str]] = {
    "AAPL": {"company": "Apple Inc.", "ticker": "AAPL", "year": "2025"},
    "AMD":  {"company": "Advanced Micro Devices, Inc.", "ticker": "AMD", "year": "2025"},
    "GOOGL": {"company": "Alphabet Inc.", "ticker": "GOOGL", "year": "2025"},
    "JNJ":  {"company": "Johnson & Johnson", "ticker": "JNJ", "year": "2025"},
    "MSFT": {"company": "Microsoft Corporation", "ticker": "MSFT", "year": "2025"},
    "NVDA": {"company": "NVIDIA Corporation", "ticker": "NVDA", "year": "2025"},
    "UNH":  {"company": "UnitedHealth Group", "ticker": "UNH", "year": "2025"},
    "JNJ-Stelara": {
        "company": "Johnson & Johnson",
        "ticker": "JNJ-Stelara",
        "drug": "Stelara",
        "sponsor": "Centocor, Inc.",
        "trial_id": "NCT00174785",
        "year": "2025",
    },
}


def get_ticker_metadata(ticker: str) -> dict[str, str]:
    """Return the bindings dict for a ticker. Raises KeyError if not known."""
    if ticker not in TICKER_METADATA:
        raise KeyError(
            f"No metadata registered for ticker {ticker!r}; known tickers: "
            f"{sorted(TICKER_METADATA.keys())}"
        )
    return dict(TICKER_METADATA[ticker])


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _placeholders_in_template(template_text: str) -> list[str]:
    """Return all distinct ``{placeholder}`` names in template text."""
    return list(dict.fromkeys(_PLACEHOLDER_RE.findall(template_text)))


def resolve(
    template: ScenarioTemplate,
    ticker: str,
    *,
    extra_bindings: Optional[dict[str, str]] = None,
) -> ResolvedScenario:
    """Resolve template placeholders against ticker metadata.

    Returns a ``ResolvedScenario`` with ``question`` (placeholder-filled),
    ``bindings`` (the substitutions actually applied), and ``template``
    (the source template).

    Raises :class:`ScenarioResolutionError` when one or more required
    placeholders cannot be filled.
    """
    bindings: dict[str, str] = {}
    try:
        bindings.update(get_ticker_metadata(ticker))
    except KeyError:
        # No registered metadata → only extra_bindings + ticker echo apply.
        bindings["ticker"] = ticker
    if extra_bindings:
        bindings.update(extra_bindings)

    needed = _placeholders_in_template(template.question_template)
    missing = [name for name in needed if name not in bindings]
    if missing:
        raise ScenarioResolutionError(
            scenario_id=template.scenario_id, missing=missing, ticker=ticker,
        )

    # Substitute every {name} occurrence (multi-occurrence safe).
    def _sub(match: re.Match) -> str:
        return bindings[match.group(1)]

    resolved_question = _PLACEHOLDER_RE.sub(_sub, template.question_template).strip()

    # Collapse soft-wrapped YAML literal-block newlines into single spaces
    # so the resolved question is a clean one-liner. The YAML uses ``|``
    # blocks for readability in source, but the demo and gold both want a
    # single-line question string.
    resolved_question = re.sub(r"\s*\n\s*", " ", resolved_question)
    resolved_question = re.sub(r"\s+", " ", resolved_question).strip()

    return ResolvedScenario(
        scenario_id=template.scenario_id,
        question=resolved_question,
        bindings={k: bindings[k] for k in needed},
        template=template,
    )


__all__ = [
    "ResolvedScenario",
    "ScenarioNotFound",
    "ScenarioResolutionError",
    "ScenarioTemplate",
    "TICKER_METADATA",
    "get_template",
    "get_ticker_metadata",
    "load_v5_templates",
    "resolve",
    "set_yaml_path",
]
