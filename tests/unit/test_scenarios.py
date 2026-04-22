"""Tests for the multi-hop scenario pack loader (PRD-004 v4 #9)."""
import pytest

from demos.extraction.scenarios import (
    Scenario,
    get_scenario,
    load_scenarios,
    scenario_to_dict,
)


def test_load_scenarios_returns_four_scenarios():
    scenarios = load_scenarios()
    assert len(scenarios) == 4


def test_each_scenario_has_required_fields():
    for s in load_scenarios():
        assert isinstance(s, Scenario)
        assert s.scenario_id
        assert s.domain in {"financial", "clinical"}
        assert s.question
        assert s.expected_hops >= 2
        assert s.talking_track


def test_scenarios_split_two_financial_two_clinical():
    by_domain: dict[str, int] = {}
    for s in load_scenarios():
        by_domain[s.domain] = by_domain.get(s.domain, 0) + 1
    assert by_domain.get("financial") == 2
    assert by_domain.get("clinical") == 2


def test_get_scenario_by_id():
    s = get_scenario("jnj_acquisitions_litigation")
    assert s.domain == "financial"
    assert "acquired" in s.question.lower()


def test_get_scenario_unknown_raises():
    with pytest.raises(KeyError):
        get_scenario("not_a_real_scenario_id")


def test_scenario_ids_unique():
    ids = [s.scenario_id for s in load_scenarios()]
    assert len(set(ids)) == len(ids)


def test_scenario_to_dict_shape():
    s = load_scenarios()[0]
    d = scenario_to_dict(s)
    assert set(d.keys()) == {"id", "domain", "question", "expected_hops", "talking_track"}
    assert d["id"] == s.scenario_id
