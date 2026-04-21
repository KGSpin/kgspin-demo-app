"""Sprint 12 Task 5+11 — pipeline_config_ref resolution tests.

Pins the precedence rule (``pipeline_config_ref`` > ``strategy`` >
``None``) and admin-resolution graceful degrade. Matches the
VP Eng Phase 1 condition: demo must survive ref lookup failures
without breaking the extraction path.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace


demo_compare = importlib.import_module("demo_compare") if False else None
# demos/extraction/demo_compare.py is not a package; load via the path hack
# tests/unit/test_demo_compare_registry_reads.py already uses.
import sys
from pathlib import Path
_DEMO_PATH = Path(__file__).parent.parent.parent / "demos" / "extraction"
sys.path.insert(0, str(_DEMO_PATH))
import demo_compare as dc  # type: ignore


def test_empty_ref_returns_none() -> None:
    assert dc._resolve_pipeline_config_ref("") is None
    assert dc._resolve_pipeline_config_ref("   ") is None


def test_admin_returns_none_for_missing_ref(monkeypatch) -> None:
    class _Client:
        def get(self, rid):
            return None
    monkeypatch.setattr(dc, "_get_registry_client", lambda: _Client())
    assert dc._resolve_pipeline_config_ref("pipeline_config:missing") is None


def test_admin_returns_resource_name_on_success(monkeypatch) -> None:
    class _Client:
        def get(self, rid):
            return SimpleNamespace(
                id=rid,
                metadata={"name": "agentic_flash", "version": "1.0.0"},
            )
    monkeypatch.setattr(dc, "_get_registry_client", lambda: _Client())
    name = dc._resolve_pipeline_config_ref("pipeline_config:agentic_flash:1.0.0")
    assert name == "agentic_flash"


def test_admin_failure_graceful_degrade(monkeypatch) -> None:
    class _Client:
        def get(self, rid):
            raise RuntimeError("admin unreachable")
    monkeypatch.setattr(dc, "_get_registry_client", lambda: _Client())
    assert dc._resolve_pipeline_config_ref("pipeline_config:anything") is None


def test_precedence_ref_beats_strategy(monkeypatch) -> None:
    """When both are supplied, the admin-resolved ref wins."""
    class _Client:
        def get(self, rid):
            return SimpleNamespace(metadata={"name": "agentic_flash"})
    monkeypatch.setattr(dc, "_get_registry_client", lambda: _Client())
    # Legacy strategy is "discovery_deep" which maps to "discovery-deep";
    # resolved ref is "agentic_flash" which isn't in the legacy map →
    # the combined result is None (agentic_flash isn't a zero-token
    # path). This is the expected shape: LLM pipelines take a different
    # dispatch path in demo.
    result = dc._pipeline_id_from_compare_args("discovery_deep", "pipeline_config:agentic_flash")
    assert result is None  # agentic_flash not in legacy zero-token map


def test_precedence_strategy_used_when_no_ref() -> None:
    result = dc._pipeline_id_from_compare_args("discovery_deep", "")
    assert result == "discovery-deep"


def test_both_empty_returns_none() -> None:
    result = dc._pipeline_id_from_compare_args("", "")
    assert result is None


def test_unknown_strategy_returns_none() -> None:
    result = dc._pipeline_id_from_compare_args("unknown_strategy", "")
    assert result is None


def test_ref_resolves_to_legacy_mappable_name(monkeypatch) -> None:
    """When the ref resolves to a name that IS in the legacy map,
    pipeline_id returns the legacy value. Common case: operator
    switching from strategy=fan_out to a ref-based call."""
    class _Client:
        def get(self, rid):
            return SimpleNamespace(metadata={"name": "fan_out"})
    monkeypatch.setattr(dc, "_get_registry_client", lambda: _Client())
    result = dc._pipeline_id_from_compare_args("", "pipeline_config:fan_out:1.0.0")
    assert result == "fan-out"
