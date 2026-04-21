"""W3-D — canonical pipeline strategy whitelist + ref builder tests.

Pins the canonical 5-pipeline shape post Wave 3. Demo accepts
``strategy=<canonical>`` or ``pipeline_config_ref=<canonical>`` on its
endpoints — both take the underscore form (matches the pipeline YAML's
``extractor`` discriminator). Anything outside the canonical 5 raises
:class:`InvalidPipelineStrategyError`, which the endpoint handlers
render as a 400. No admin lookup happens demo-side: core's
``load_pipeline_config_via_registry`` handles resolution at dispatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DEMO_PATH = Path(__file__).parent.parent.parent / "demos" / "extraction"
sys.path.insert(0, str(_DEMO_PATH))
import demo_compare as dc  # type: ignore


CANONICAL = [
    "fan_out",
    "discovery_rapid",
    "discovery_deep",
    "agentic_flash",
    "agentic_analyst",
]


@pytest.mark.parametrize("strategy", CANONICAL)
def test_canonical_pipeline_name_maps_underscore_to_hyphen(strategy: str) -> None:
    expected = strategy.replace("_", "-")
    assert dc._canonical_pipeline_name(strategy) == expected


def test_canonical_pipeline_name_rejects_unknown() -> None:
    with pytest.raises(dc.InvalidPipelineStrategyError):
        dc._canonical_pipeline_name("emergent")  # Wave 2 legacy
    with pytest.raises(dc.InvalidPipelineStrategyError):
        dc._canonical_pipeline_name("llm_full_shot")  # pre-Wave 2 legacy
    with pytest.raises(dc.InvalidPipelineStrategyError):
        dc._canonical_pipeline_name("discovery_agentic")  # dropped in W3-B


def test_canonical_pipeline_name_rejects_hyphen_form() -> None:
    """Hyphen form is admin-side naming; wire accepts underscore only."""
    with pytest.raises(dc.InvalidPipelineStrategyError):
        dc._canonical_pipeline_name("fan-out")


@pytest.mark.parametrize("strategy", CANONICAL)
def test_pipeline_ref_from_strategy_builds_v1_ref(strategy: str) -> None:
    ref = dc._pipeline_ref_from_strategy(strategy)
    assert ref.name == strategy.replace("_", "-")
    assert ref.version == "v1"


def test_pipeline_id_from_compare_args_prefers_ref() -> None:
    """Ref wins over strategy when both are supplied (both canonical)."""
    result = dc._pipeline_id_from_compare_args("fan_out", "agentic_flash")
    assert result == "agentic-flash"


def test_pipeline_id_from_compare_args_uses_strategy_when_no_ref() -> None:
    assert dc._pipeline_id_from_compare_args("discovery_deep", "") == "discovery-deep"


def test_pipeline_id_from_compare_args_empty_returns_none() -> None:
    assert dc._pipeline_id_from_compare_args("", "") is None


def test_pipeline_id_from_compare_args_rejects_noncanonical() -> None:
    with pytest.raises(dc.InvalidPipelineStrategyError):
        dc._pipeline_id_from_compare_args("emergent", "")
    with pytest.raises(dc.InvalidPipelineStrategyError):
        dc._pipeline_id_from_compare_args("", "llm_full_shot")


def test_pipeline_ref_from_pipeline_id_defaults_to_fan_out() -> None:
    """Used internally when Compare tab runs baseline KGSpin without a
    user-selected pipeline_id. ``None`` → ``fan-out`` (canonical zero-LLM)."""
    ref = dc._pipeline_ref_from_pipeline_id(None)
    assert ref.name == "fan-out"
    assert ref.version == "v1"


def test_pipeline_ref_from_pipeline_id_passthrough_hyphen() -> None:
    ref = dc._pipeline_ref_from_pipeline_id("discovery-rapid")
    assert ref.name == "discovery-rapid"
    assert ref.version == "v1"
