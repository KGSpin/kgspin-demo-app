"""Clinical trial → gold-standard triples generator.

Ported from ``KGenSkills/research/clinical_gold_generator.py``. Preserves
the 6-predicate ontology (``sponsors``, ``investigated_in``, ``treats``,
``studies``, ``has_phase``, ``has_status``) and the
``source_field`` provenance convention from KGenSkills ADR-005 so future
tuner clinical-parity runs can consume these gold fixtures without
needing KGenSkills installed.

Key differences from the KGenSkills original:

* No ``BaseDataSource`` / ``CachedDocument`` coupling — the module
  embeds a minimal ClinicalTrials.gov v2 client that uses ``urllib``
  (same rationale as KGenSkills: httpx is blocked by the endpoint's
  TLS fingerprinting).
* No PubMed linkage. KGenSkills'' PubMed/PMC integration doesn't exist
  in kgspin-demo and the gold-triple generation path is orthogonal to
  document linkage. ``input_documents`` stays in the schema as a
  placeholder list; callers that want PubMed linkage can fill it
  post-hoc or port the PubMed data source in a follow-up sprint.
* Accepts ``llm_alias`` / ``llm_provider`` / ``llm_model`` kwargs per
  ADR-002 §7 even though the generator is mostly deterministic — this
  keeps the call-site shape consistent with every other LLM-adjacent
  entry point in the demo. The kwargs are currently stored in
  ``GoldDataRecord.metadata.llm`` for reproducibility and are NOT
  used to invoke an LLM today.

CLI::

    uv run kgspin-demo-generate-clinical-gold --nct-ids NCT01234567 NCT07654321 \\
        --output-dir tests/fixtures/gold/clinical/
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


CLINICAL_TRIALS_BASE = "https://clinicaltrials.gov/api/v2"
DEFAULT_USER_AGENT = (
    "kgspin-demo/1.0 (clinical-trial-research; ops@kgspin.example)"
)
# ClinicalTrials.gov is rate-limit-friendly — the KGenSkills original
# sleeps 200ms between calls. Mirror that.
POLITE_DELAY_SECONDS = 0.2


# --- Data types -------------------------------------------------------------


@dataclass
class ClinicalTrial:
    """Minimal structured representation of a ClinicalTrials.gov v2 record.

    Only the fields the gold-triple generator consumes. The raw API
    payload is preserved under ``raw_data`` for downstream callers that
    need richer context.
    """

    nct_id: str
    title: str
    status: str
    phase: str
    conditions: list[str]
    interventions: list[dict[str, str]]
    sponsor: str
    start_date: str | None = None
    completion_date: str | None = None
    enrollment: int = 0
    study_type: str = ""
    brief_summary: str = ""
    detailed_description: str = ""
    eligibility: dict[str, Any] = field(default_factory=dict)
    locations: list[dict[str, str]] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoldTriple:
    """A single gold standard triple derived from structured trial data.

    Provenance flows via ``source_field`` — which ClinicalTrials.gov
    structured field this triple came from (sponsor, interventions,
    conditions, phase, status, interventions+conditions). Gold confidence
    is always 1.0 — these are deterministic mappings from registry data,
    not probabilistic extractions.
    """

    subject_text: str
    subject_type: str
    predicate: str
    object_text: str
    object_type: str
    source: str = "ClinicalTrials.gov"
    source_field: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class GoldDataRecord:
    """Complete gold data record for one clinical trial."""

    nct_id: str
    trial_title: str
    gold_triples: list[dict[str, Any]]
    input_documents: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nct_id": self.nct_id,
            "trial_title": self.trial_title,
            "gold_triples": self.gold_triples,
            "input_documents": self.input_documents,
            "metadata": self.metadata,
        }


# --- ClinicalTrials.gov client ---------------------------------------------


class ClinicalTrialsClient:
    """Minimal ClinicalTrials.gov v2 client.

    Uses ``urllib.request`` directly because the endpoint's TLS
    fingerprinting blocks ``httpx`` / ``httpcore`` (documented in
    KGenSkills Sprint 77 VP Eng review).
    """

    def __init__(
        self,
        *,
        base_url: str = CLINICAL_TRIALS_BASE,
        user_agent: str | None = None,
        timeout_seconds: int = 30,
        polite_delay: float = POLITE_DELAY_SECONDS,
    ) -> None:
        self.base_url = base_url
        self.user_agent = (
            user_agent
            or os.environ.get("CT_GOV_IDENTITY")
            or DEFAULT_USER_AGENT
        )
        self.timeout_seconds = timeout_seconds
        self.polite_delay = polite_delay

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        qs = urlencode(params)
        url = f"{self.base_url}/{endpoint}"
        if qs:
            url = f"{url}?{qs}"
        time.sleep(self.polite_delay)
        try:
            req = Request(url, headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            })
            with urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            logger.warning("[CT.gov] HTTP %d for %s", e.code, url)
            return None
        except (URLError, TimeoutError) as e:
            logger.warning("[CT.gov] transport error for %s: %s", url, e)
            return None

    def get_trial(self, nct_id: str) -> ClinicalTrial | None:
        """Fetch one trial by NCT id and parse into a ``ClinicalTrial``."""
        data = self._request(f"studies/{nct_id}", {"format": "json"})
        if not data:
            return None
        return _parse_study(data)


def _parse_study(study: dict[str, Any]) -> ClinicalTrial | None:
    """Parse a ClinicalTrials.gov v2 study payload into ``ClinicalTrial``.

    Mirrors KGenSkills healthcare.py ``_parse_study`` exactly so fixture
    outputs remain byte-compatible with the KGenSkills gold format.
    """
    try:
        protocol = study.get("protocolSection", {})
        id_module = protocol.get("identificationModule", {})
        status_module = protocol.get("statusModule", {})
        desc_module = protocol.get("descriptionModule", {})
        design_module = protocol.get("designModule", {})
        eligibility_module = protocol.get("eligibilityModule", {})
        sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
        conditions_module = protocol.get("conditionsModule", {})
        interventions_module = protocol.get("armsInterventionsModule", {})
        locations_module = protocol.get("contactsLocationsModule", {})

        interventions = [
            {
                "type": i.get("type", ""),
                "name": i.get("name", ""),
                "description": i.get("description", ""),
            }
            for i in interventions_module.get("interventions", [])
        ]
        locations = [
            {
                "facility": loc.get("facility", ""),
                "city": loc.get("city", ""),
                "state": loc.get("state", ""),
                "country": loc.get("country", ""),
            }
            for loc in locations_module.get("locations", [])[:10]
        ]
        lead_sponsor = sponsor_module.get("leadSponsor", {})

        return ClinicalTrial(
            nct_id=id_module.get("nctId", ""),
            title=id_module.get("officialTitle") or id_module.get("briefTitle", ""),
            status=status_module.get("overallStatus", ""),
            phase=", ".join(design_module.get("phases", [])),
            conditions=list(conditions_module.get("conditions", [])),
            interventions=interventions,
            sponsor=lead_sponsor.get("name", ""),
            start_date=status_module.get("startDateStruct", {}).get("date"),
            completion_date=status_module.get("completionDateStruct", {}).get("date"),
            enrollment=design_module.get("enrollmentInfo", {}).get("count", 0) or 0,
            study_type=design_module.get("studyType", ""),
            brief_summary=desc_module.get("briefSummary", ""),
            detailed_description=desc_module.get("detailedDescription", ""),
            eligibility=dict(eligibility_module),
            locations=locations,
            raw_data=study,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[CT.gov] failed to parse study payload: %s", e)
        return None


# --- Gold triple generation -------------------------------------------------


def generate_gold_triples(trial: ClinicalTrial) -> list[GoldTriple]:
    """Map a ``ClinicalTrial`` to the KGenSkills 6-predicate gold set.

    Predicate ordering mirrors KGenSkills so byte-compatible fixtures
    round-trip without diffs:

    1. ORGANIZATION --[sponsors]--> CLINICAL_TRIAL
    2. DRUG/PROCEDURE --[investigated_in]--> CLINICAL_TRIAL
    3. DRUG/PROCEDURE --[treats]--> CONDITION
    4. CLINICAL_TRIAL --[studies]--> CONDITION
    5. CLINICAL_TRIAL --[has_phase]--> PHASE
    6. CLINICAL_TRIAL --[has_status]--> STATUS
    """
    triples: list[GoldTriple] = []

    if trial.sponsor:
        triples.append(GoldTriple(
            subject_text=trial.sponsor,
            subject_type="ORGANIZATION",
            predicate="sponsors",
            object_text=trial.nct_id,
            object_type="CLINICAL_TRIAL",
            source_field="sponsor",
        ))

    seen: set[str] = set()
    for intervention in trial.interventions:
        name = intervention.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        int_type = intervention.get("type", "OTHER")
        entity_type = "DRUG" if int_type in ("DRUG", "BIOLOGICAL") else "PROCEDURE"

        triples.append(GoldTriple(
            subject_text=name,
            subject_type=entity_type,
            predicate="investigated_in",
            object_text=trial.nct_id,
            object_type="CLINICAL_TRIAL",
            source_field="interventions",
        ))
        for condition in trial.conditions:
            triples.append(GoldTriple(
                subject_text=name,
                subject_type=entity_type,
                predicate="treats",
                object_text=condition,
                object_type="CONDITION",
                source_field="interventions+conditions",
            ))

    for condition in trial.conditions:
        triples.append(GoldTriple(
            subject_text=trial.nct_id,
            subject_type="CLINICAL_TRIAL",
            predicate="studies",
            object_text=condition,
            object_type="CONDITION",
            source_field="conditions",
        ))

    if trial.phase and trial.phase != "N/A":
        triples.append(GoldTriple(
            subject_text=trial.nct_id,
            subject_type="CLINICAL_TRIAL",
            predicate="has_phase",
            object_text=trial.phase,
            object_type="PHASE",
            source_field="phase",
        ))

    if trial.status:
        triples.append(GoldTriple(
            subject_text=trial.nct_id,
            subject_type="CLINICAL_TRIAL",
            predicate="has_status",
            object_text=trial.status,
            object_type="STATUS",
            source_field="status",
        ))

    return triples


def generate_gold_record(
    nct_id: str,
    client: ClinicalTrialsClient | None = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> GoldDataRecord | None:
    """Fetch ``nct_id`` and return the full ``GoldDataRecord``.

    ``llm_*`` kwargs are accepted for ADR-002 §7 shape consistency. The
    generator is deterministic today; the kwargs are recorded in
    ``metadata.llm`` so future callers that DO want LLM-augmented
    provenance (e.g. narrative cross-linking) have the selector
    pre-wired.
    """
    client = client or ClinicalTrialsClient()
    trial = client.get_trial(nct_id)
    if trial is None:
        logger.error("[CT.gov] trial %s not found", nct_id)
        return None

    gold_triples = generate_gold_triples(trial)

    return GoldDataRecord(
        nct_id=nct_id,
        trial_title=trial.title,
        gold_triples=[t.to_dict() for t in gold_triples],
        input_documents=[],  # PubMed linkage deferred; see module docstring.
        metadata={
            "sponsor": trial.sponsor,
            "phase": trial.phase,
            "status": trial.status,
            "conditions": trial.conditions,
            "enrollment": trial.enrollment,
            "num_publications": 0,
            "num_with_full_text": 0,
            "llm": {
                "alias": llm_alias,
                "provider": llm_provider,
                "model": llm_model,
            },
        },
    )


def generate_gold_records(
    nct_ids: Iterable[str],
    client: ClinicalTrialsClient | None = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> list[GoldDataRecord]:
    client = client or ClinicalTrialsClient()
    records: list[GoldDataRecord] = []
    for nct_id in nct_ids:
        rec = generate_gold_record(
            nct_id,
            client,
            llm_alias=llm_alias,
            llm_provider=llm_provider,
            llm_model=llm_model,
        )
        if rec is not None:
            records.append(rec)
    return records


# --- CLI --------------------------------------------------------------------


def _write_record(record: GoldDataRecord, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{record.nct_id}.json"
    out.write_text(json.dumps(record.to_dict(), indent=2))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate clinical trial gold data")
    parser.add_argument("--nct-ids", nargs="+", default=None,
                        help="One or more NCT ids (NCT followed by digits).")
    parser.add_argument("--batch", type=Path,
                        help="File with one NCT id per line.")
    parser.add_argument("--output-dir", type=Path, default=Path("tests/fixtures/gold/clinical"),
                        help="Where to write <NCT_ID>.json files.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print triples without writing files.")
    parser.add_argument("--llm-alias", default=None, help="ADR-002 LLM alias selector.")
    parser.add_argument("--llm-provider", default=None,
                        help="ADR-002 direct-mode provider (requires --llm-model).")
    parser.add_argument("--llm-model", default=None,
                        help="ADR-002 direct-mode model id.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")

    nct_ids: list[str] = []
    if args.nct_ids:
        nct_ids.extend(args.nct_ids)
    if args.batch:
        for line in args.batch.read_text().splitlines():
            line = line.strip()
            if line.startswith("NCT"):
                nct_ids.append(line)
    if not nct_ids:
        parser.error("Either --nct-ids or --batch is required")

    client = ClinicalTrialsClient()
    written: list[Path] = []
    for nct_id in nct_ids:
        logger.info("Processing %s", nct_id)
        if args.dry_run:
            trial = client.get_trial(nct_id)
            if trial is None:
                logger.error("  %s not found", nct_id)
                continue
            triples = generate_gold_triples(trial)
            print(f"\n=== {nct_id}: {trial.title} ===")
            print(f"Sponsor: {trial.sponsor}")
            print(f"Phase: {trial.phase}, Status: {trial.status}")
            print(f"Gold triples: {len(triples)}")
            for t in triples:
                print(
                    f"  {t.subject_text} ({t.subject_type}) "
                    f"--[{t.predicate}]--> {t.object_text} ({t.object_type})"
                )
            continue

        record = generate_gold_record(
            nct_id, client,
            llm_alias=args.llm_alias,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
        )
        if record is None:
            continue
        out_path = _write_record(record, args.output_dir)
        logger.info("  wrote %s (%d triples)", out_path, len(record.gold_triples))
        written.append(out_path)

    if written:
        print(f"\nWrote {len(written)} gold files to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
