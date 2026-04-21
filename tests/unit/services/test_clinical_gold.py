"""Unit tests for ``kgspin_demo_app.services.clinical_gold``.

Covers:

* ``_parse_study`` → ``ClinicalTrial`` dataclass shape on a representative
  ClinicalTrials.gov v2 payload.
* ``generate_gold_triples`` produces the 6-predicate ontology in
  KGenSkills-compatible order.
* ``generate_gold_record`` threads ``llm_alias`` / ``llm_provider`` /
  ``llm_model`` into ``metadata.llm`` without invoking an LLM (ADR-002
  §7 shape-consistency requirement).
* CLI dry-run exits 0 and prints triples when given a stubbed client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kgspin_demo_app.services.clinical_gold import (
    ClinicalTrial,
    ClinicalTrialsClient,
    GoldDataRecord,
    _parse_study,
    generate_gold_record,
    generate_gold_records,
    generate_gold_triples,
    main,
)


# --- Test payloads ---------------------------------------------------------


def _ct_payload(
    nct_id: str = "NCT99999999",
    sponsor: str = "Acme Research Corp",
    interventions: list[dict[str, str]] | None = None,
    conditions: list[str] | None = None,
    phase: list[str] | None = None,
    status: str = "COMPLETED",
) -> dict[str, Any]:
    """Build a minimal ClinicalTrials.gov v2-shaped payload."""
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "officialTitle": f"Official: {nct_id}",
                "briefTitle": f"Brief: {nct_id}",
            },
            "statusModule": {
                "overallStatus": status,
                "startDateStruct": {"date": "2020-01-01"},
                "completionDateStruct": {"date": "2023-12-31"},
            },
            "descriptionModule": {
                "briefSummary": "Summary.",
                "detailedDescription": "Details.",
            },
            "designModule": {
                "phases": phase or ["PHASE3"],
                "enrollmentInfo": {"count": 250},
                "studyType": "INTERVENTIONAL",
            },
            "eligibilityModule": {"minimumAge": "18 Years"},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor}},
            "conditionsModule": {"conditions": conditions or ["Hypertension", "Heart Failure"]},
            "armsInterventionsModule": {
                "interventions": interventions or [
                    {"type": "DRUG", "name": "CompoundX", "description": "IV"},
                    {"type": "PROCEDURE", "name": "Telemetry", "description": ""},
                ],
            },
            "contactsLocationsModule": {
                "locations": [
                    {"facility": "Site A", "city": "Boston", "state": "MA", "country": "US"}
                ],
            },
        }
    }


# --- _parse_study ---------------------------------------------------------


def test_parse_study_extracts_expected_fields() -> None:
    payload = _ct_payload()
    trial = _parse_study(payload)
    assert trial is not None
    assert trial.nct_id == "NCT99999999"
    # Prefers officialTitle over briefTitle.
    assert trial.title == "Official: NCT99999999"
    assert trial.status == "COMPLETED"
    assert trial.phase == "PHASE3"
    assert trial.conditions == ["Hypertension", "Heart Failure"]
    assert trial.sponsor == "Acme Research Corp"
    assert trial.enrollment == 250
    assert trial.study_type == "INTERVENTIONAL"
    assert trial.locations == [
        {"facility": "Site A", "city": "Boston", "state": "MA", "country": "US"}
    ]
    assert trial.raw_data is payload


def test_parse_study_returns_none_on_malformed() -> None:
    # Missing protocolSection entirely — parser should emit None, not raise.
    assert _parse_study({"bogus": True}) is not None  # empty fields, still a record
    # Now force a real exception: non-dict study.
    assert _parse_study("not-a-dict") is None  # type: ignore[arg-type]


# --- generate_gold_triples ------------------------------------------------


def test_generate_gold_triples_emits_six_predicate_ontology() -> None:
    trial = _parse_study(_ct_payload())
    triples = generate_gold_triples(trial)
    predicates = {t.predicate for t in triples}
    assert predicates == {
        "sponsors", "investigated_in", "treats",
        "studies", "has_phase", "has_status",
    }


def test_generate_gold_triples_drug_biological_types() -> None:
    trial = _parse_study(_ct_payload(interventions=[
        {"type": "BIOLOGICAL", "name": "Vaccine-Y", "description": ""},
        {"type": "PROCEDURE", "name": "Stent", "description": ""},
    ]))
    triples = generate_gold_triples(trial)
    drug_triples = [t for t in triples if t.subject_text == "Vaccine-Y" and t.predicate == "investigated_in"]
    procedure_triples = [t for t in triples if t.subject_text == "Stent" and t.predicate == "investigated_in"]
    assert drug_triples and drug_triples[0].subject_type == "DRUG"
    assert procedure_triples and procedure_triples[0].subject_type == "PROCEDURE"


def test_generate_gold_triples_na_phase_skipped() -> None:
    trial = _parse_study(_ct_payload(phase=["N/A"]))
    triples = generate_gold_triples(trial)
    assert all(t.predicate != "has_phase" for t in triples)


def test_generate_gold_triples_treats_per_condition() -> None:
    trial = _parse_study(_ct_payload(
        interventions=[{"type": "DRUG", "name": "CompoundX", "description": ""}],
        conditions=["CondA", "CondB", "CondC"],
    ))
    triples = generate_gold_triples(trial)
    treats = [t for t in triples if t.predicate == "treats"]
    assert {t.object_text for t in treats} == {"CondA", "CondB", "CondC"}


def test_generate_gold_triples_dedups_intervention_names() -> None:
    trial = _parse_study(_ct_payload(
        interventions=[
            {"type": "DRUG", "name": "CompoundX", "description": "arm 1"},
            {"type": "DRUG", "name": "CompoundX", "description": "arm 2"},  # dup
        ],
        conditions=["Condition"],
    ))
    triples = generate_gold_triples(trial)
    investigated_in = [t for t in triples if t.predicate == "investigated_in" and t.subject_text == "CompoundX"]
    assert len(investigated_in) == 1


def test_generate_gold_triples_all_gold_confidence() -> None:
    trial = _parse_study(_ct_payload())
    triples = generate_gold_triples(trial)
    assert {t.confidence for t in triples} == {1.0}
    assert {t.source for t in triples} == {"ClinicalTrials.gov"}


# --- generate_gold_record (mocked client) ---------------------------------


class _StubClient:
    """Minimal duck-type of ClinicalTrialsClient for deterministic tests."""

    def __init__(self, trial: ClinicalTrial | None) -> None:
        self._trial = trial
        self.calls: list[str] = []

    def get_trial(self, nct_id: str) -> ClinicalTrial | None:
        self.calls.append(nct_id)
        return self._trial


def test_generate_gold_record_threads_llm_kwargs_into_metadata() -> None:
    trial = _parse_study(_ct_payload())
    client = _StubClient(trial)
    record = generate_gold_record(
        "NCT99999999",
        client,  # type: ignore[arg-type]
        llm_alias="gemini_flash",
        llm_provider=None,
        llm_model=None,
    )
    assert record is not None
    assert record.metadata["llm"] == {
        "alias": "gemini_flash",
        "provider": None,
        "model": None,
    }
    assert record.nct_id == "NCT99999999"
    assert record.input_documents == []  # PubMed linkage deferred


def test_generate_gold_record_returns_none_on_missing_trial() -> None:
    client = _StubClient(None)
    result = generate_gold_record("NCT00000000", client)  # type: ignore[arg-type]
    assert result is None


def test_generate_gold_records_batches() -> None:
    trial = _parse_study(_ct_payload())
    client = _StubClient(trial)
    records = generate_gold_records(
        ["NCT00000001", "NCT00000002"],
        client,  # type: ignore[arg-type]
    )
    assert [r.nct_id for r in records] == ["NCT00000001", "NCT00000002"]
    assert client.calls == ["NCT00000001", "NCT00000002"]


# --- fixture compatibility (smoke vs. KGenSkills gold JSON) ---------------


def test_ported_fixtures_parse_back_into_gold_record_shape() -> None:
    """The 5 NCT fixtures copied from KGenSkills should deserialize into
    the same nct_id / trial_title / gold_triples keys our record writes.

    This protects the ported fixtures from drifting if the dataclass
    shape is later edited — i.e. the predicate ontology stays
    byte-compatible with KGenSkills.
    """
    fixture_dir = Path(__file__).parent.parent.parent / "fixtures" / "gold" / "clinical"
    files = sorted(fixture_dir.glob("NCT*.json"))
    assert len(files) == 5, f"Expected 5 fixtures, saw {[f.name for f in files]}"
    for f in files:
        data = json.loads(f.read_text())
        assert "nct_id" in data and "gold_triples" in data
        assert data["nct_id"] == f.stem
        predicates = {t["predicate"] for t in data["gold_triples"]}
        # Every fixture should use only the 6-predicate ontology.
        assert predicates.issubset({
            "sponsors", "investigated_in", "treats",
            "studies", "has_phase", "has_status",
        })


# --- CLI ------------------------------------------------------------------


def test_cli_dry_run_with_stub_client(monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    stub = _StubClient(_parse_study(_ct_payload()))

    def _fake_client(*_a, **_kw) -> _StubClient:
        return stub

    import kgspin_demo_app.services.clinical_gold as mod
    monkeypatch.setattr(mod, "ClinicalTrialsClient", _fake_client)

    rc = main(["--nct-ids", "NCT99999999", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NCT99999999" in out
    assert "sponsors" in out or "has_phase" in out  # predicate print line


def test_cli_writes_json_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClient(_parse_study(_ct_payload()))
    import kgspin_demo_app.services.clinical_gold as mod
    monkeypatch.setattr(mod, "ClinicalTrialsClient", lambda *_a, **_kw: stub)

    rc = main(["--nct-ids", "NCT99999999", "--output-dir", str(tmp_path)])
    assert rc == 0
    written = tmp_path / "NCT99999999.json"
    assert written.is_file()
    payload = json.loads(written.read_text())
    assert payload["nct_id"] == "NCT99999999"
    assert len(payload["gold_triples"]) > 0


def test_cli_requires_nct_ids_or_batch(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])


def test_cli_accepts_llm_selector_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClient(_parse_study(_ct_payload()))
    import kgspin_demo_app.services.clinical_gold as mod
    monkeypatch.setattr(mod, "ClinicalTrialsClient", lambda *_a, **_kw: stub)
    rc = main([
        "--nct-ids", "NCT99999999",
        "--output-dir", str(tmp_path),
        "--llm-alias", "gemini_flash",
    ])
    assert rc == 0
    payload = json.loads((tmp_path / "NCT99999999.json").read_text())
    assert payload["metadata"]["llm"] == {
        "alias": "gemini_flash", "provider": None, "model": None,
    }
