"""Unit tests for ``services/scenario_resolver``.

PRD-004 v5 Phase 5A — deliverable F. Loads the v5 YAML, exercises
the placeholder substitution + missing-binding paths.
"""
from __future__ import annotations

import textwrap

import pytest

from kgspin_demo_app.services import scenario_resolver as sr


@pytest.fixture(autouse=True)
def _reset_yaml_path():
    """Each test starts with the default YAML path + clean cache."""
    sr.set_yaml_path(sr._DEFAULT_YAML_PATH)
    yield
    sr.set_yaml_path(sr._DEFAULT_YAML_PATH)


def test_loads_ten_phase5a_templates():
    """Phase 5A fixup: 5 fin (ready) + 1 clinical (ready, JNJ-Stelara hedge)
    + 4 clinical scaffolds = 10 total."""
    templates = sr.load_v5_templates()
    assert len(templates) == 10
    ids = {t.scenario_id for t in templates}
    # 5 fin scenarios (ready).
    assert "subsidiaries_litigation_jurisdiction" in ids
    assert "neo_compensation_stock_awards" in ids
    assert "segments_revenue_litigation_accrual" in ids
    assert "supplier_concentration_ma_termination" in ids
    assert "warrants_options_proxy_executives" in ids
    # 1 clinical hedge (ready).
    assert "stelara_adverse_events_cohort_v5" in ids
    # 4 clinical scaffolds (status=scaffold).
    assert "clinical_scaffold_phase_progression_endpoints" in ids
    assert "clinical_scaffold_adverse_events_dose" in ids
    assert "clinical_scaffold_cross_trial_inclusion" in ids
    assert "clinical_scaffold_regulatory_submission" in ids


def test_scaffold_templates_have_status_flag():
    """The 4 scaffold entries carry status='scaffold'; the other 6 carry
    the default status='ready'."""
    by_id = {t.scenario_id: t for t in sr.load_v5_templates()}
    scaffold_ids = {
        "clinical_scaffold_phase_progression_endpoints",
        "clinical_scaffold_adverse_events_dose",
        "clinical_scaffold_cross_trial_inclusion",
        "clinical_scaffold_regulatory_submission",
    }
    for sid in scaffold_ids:
        assert by_id[sid].status == "scaffold", f"{sid} should be scaffold"
    for sid, t in by_id.items():
        if sid not in scaffold_ids:
            assert t.status == "ready", f"{sid} should be ready (got {t.status!r})"


def test_each_template_has_required_fields():
    """Ready templates carry placeholders + key_fields. Scaffolds are
    skipped — they're placeholder entries with intentionally empty
    placeholders/key_fields so they parse and load without breaking
    the picker, but they don't run end-to-end (the frontend disables
    Run on scaffold selection)."""
    for t in sr.load_v5_templates():
        if t.status == "scaffold":
            continue
        assert t.scenario_id
        assert t.question_template
        assert t.expected_hops >= 1
        assert t.placeholders, f"{t.scenario_id} has no placeholders"
        assert t.key_fields, f"{t.scenario_id} has no key_fields"
        assert t.expected_difficulty in ("easy", "medium", "hard")


def test_get_template_happy_path():
    t = sr.get_template("subsidiaries_litigation_jurisdiction")
    assert t.scenario_id == "subsidiaries_litigation_jurisdiction"
    assert "Exhibit 21" in t.question_template


def test_get_template_unknown_raises_scenario_not_found():
    with pytest.raises(sr.ScenarioNotFound):
        sr.get_template("does_not_exist")


def test_resolve_substitutes_company():
    t = sr.get_template("subsidiaries_litigation_jurisdiction")
    resolved = sr.resolve(t, ticker="AAPL")
    assert "Apple Inc." in resolved.question
    assert "{company}" not in resolved.question
    assert resolved.bindings["company"] == "Apple Inc."


def test_resolve_handles_year_placeholder():
    t = sr.get_template("neo_compensation_stock_awards")
    resolved = sr.resolve(t, ticker="JNJ")
    assert "Johnson & Johnson" in resolved.question
    assert "fiscal 2025" in resolved.question


def test_resolve_clinical_hedge_uses_drug_sponsor_bindings():
    t = sr.get_template("stelara_adverse_events_cohort_v5")
    resolved = sr.resolve(t, ticker="JNJ-Stelara")
    assert "Stelara" in resolved.question
    assert "Centocor" in resolved.question
    assert "NCT00174785" in resolved.question


def test_resolve_missing_binding_raises_resolution_error():
    t = sr.get_template("stelara_adverse_events_cohort_v5")
    # "AAPL" doesn't have drug/sponsor/trial_id bindings.
    with pytest.raises(sr.ScenarioResolutionError) as excinfo:
        sr.resolve(t, ticker="AAPL")
    assert excinfo.value.scenario_id == "stelara_adverse_events_cohort_v5"
    assert set(excinfo.value.missing) >= {"drug", "sponsor", "trial_id"}


def test_resolve_extra_bindings_override_metadata():
    t = sr.get_template("subsidiaries_litigation_jurisdiction")
    resolved = sr.resolve(t, ticker="AAPL", extra_bindings={"company": "FAKE CORP"})
    assert "FAKE CORP" in resolved.question
    assert "Apple Inc." not in resolved.question


def test_resolve_unknown_ticker_falls_back_to_ticker_echo(tmp_path):
    """Even without metadata, the template still resolves if all
    required placeholders come from ticker echo + extra_bindings."""
    yaml_text = textwrap.dedent("""
    scenarios:
      - scenario_id: only_ticker
        domain: fin
        expected_hops: 1
        expected_difficulty: easy
        placeholders: [ticker]
        key_fields: [thing]
        question_template: "What about {ticker}?"
        talking_track: "Test."
    """).strip()
    yaml_path = tmp_path / "tiny.yaml"
    yaml_path.write_text(yaml_text)
    sr.set_yaml_path(yaml_path)

    t = sr.get_template("only_ticker")
    resolved = sr.resolve(t, ticker="ZZZZ")
    assert resolved.question == "What about ZZZZ?"


def test_placeholder_multi_occurrence_substitutes_all():
    t = sr.get_template("subsidiaries_litigation_jurisdiction")
    # Template uses {company} twice → both occurrences should be replaced.
    resolved = sr.resolve(t, ticker="AAPL")
    assert resolved.question.count("Apple Inc.") == 2


def test_template_placeholders_match_yaml_declaration():
    """Each ready template's ``placeholders`` field must match what the
    question_template actually contains. Scaffolds skipped — their
    placeholder copy doesn't carry `{name}` markers."""
    for t in sr.load_v5_templates():
        if t.status == "scaffold":
            continue
        text_phs = set(sr._placeholders_in_template(t.question_template))
        declared = set(t.placeholders)
        assert text_phs == declared, (
            f"{t.scenario_id}: declared placeholders {declared} "
            f"don't match template {text_phs}"
        )
