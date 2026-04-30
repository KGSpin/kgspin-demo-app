"""Schema + content validation for the hand-authored gold fixtures.

PRD-004 v5 Phase 5A — deliverable G. 11 fixtures total:
  5 fin templates × 2 tickers (AAPL + JNJ) + 1 clinical (JNJ-Stelara).

Asserts each fixture file:
  - Exists at the expected path.
  - Parses as JSON with the schema fields the F1 scorer expects.
  - ``key_fields`` ⊆ keys of every ``expected_answer.structured`` row.
  - ``scenario_id`` matches the directory name.
  - When the corpus is built (source.txt exists), source_spans resolve
    to non-empty substrings of source.txt. When the corpus isn't yet
    built (build is gated on the live extractor), this assertion is
    skipped — fixtures still validate structurally.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLD_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "multi-hop-gold"
RAG_CORPUS = PROJECT_ROOT / "tests" / "fixtures" / "rag-corpus"

EXPECTED_FIXTURES: list[tuple[str, str]] = [
    ("subsidiaries_litigation_jurisdiction", "AAPL"),
    ("subsidiaries_litigation_jurisdiction", "JNJ"),
    ("neo_compensation_stock_awards", "AAPL"),
    ("neo_compensation_stock_awards", "JNJ"),
    ("segments_revenue_litigation_accrual", "AAPL"),
    ("segments_revenue_litigation_accrual", "JNJ"),
    ("supplier_concentration_ma_termination", "AAPL"),
    ("supplier_concentration_ma_termination", "JNJ"),
    ("warrants_options_proxy_executives", "AAPL"),
    ("warrants_options_proxy_executives", "JNJ"),
    ("stelara_adverse_events_cohort_v5", "JNJ-Stelara"),
]


SCHEMA_REQUIRED_FIELDS = {
    "scenario_id", "ticker", "resolved_question", "bindings",
    "expected_answer", "key_fields", "source_spans",
    "expected_difficulty", "narrative_recovery", "notes",
    "authored_by", "confidence",
}


def _load(scenario_id: str, ticker: str) -> dict:
    path = GOLD_ROOT / scenario_id / f"{ticker}.gold.json"
    assert path.exists(), f"missing fixture {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_eleven_fixtures_present():
    found_files = sorted(GOLD_ROOT.rglob("*.gold.json"))
    assert len(found_files) == 11, (
        f"expected 11 gold files, found {len(found_files)}: "
        f"{[str(p.relative_to(GOLD_ROOT)) for p in found_files]}"
    )


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_fixture_has_required_schema_fields(scenario_id: str, ticker: str):
    gold = _load(scenario_id, ticker)
    missing = SCHEMA_REQUIRED_FIELDS - set(gold.keys())
    assert not missing, f"{scenario_id}/{ticker} missing fields: {missing}"


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_fixture_scenario_id_matches_directory(scenario_id: str, ticker: str):
    gold = _load(scenario_id, ticker)
    assert gold["scenario_id"] == scenario_id


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_fixture_ticker_matches_filename(scenario_id: str, ticker: str):
    gold = _load(scenario_id, ticker)
    assert gold["ticker"] == ticker


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_expected_answer_has_summary_and_structured(scenario_id: str, ticker: str):
    gold = _load(scenario_id, ticker)
    ea = gold["expected_answer"]
    assert "summary" in ea and isinstance(ea["summary"], str) and ea["summary"]
    assert "structured" in ea and isinstance(ea["structured"], list)


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_key_fields_subset_of_structured_rows(scenario_id: str, ticker: str):
    """Every ``key_fields`` entry must be a key in every structured row.

    Empty structured lists are valid (gold may legitimately answer
    'none qualify'); the assertion only fires when there's at least
    one structured row to validate against.
    """
    gold = _load(scenario_id, ticker)
    key_fields = set(gold["key_fields"])
    assert key_fields, f"{scenario_id}/{ticker} has empty key_fields"
    structured = gold["expected_answer"]["structured"]
    for i, row in enumerate(structured):
        missing = key_fields - set(row.keys())
        assert not missing, (
            f"{scenario_id}/{ticker} structured[{i}] missing key_fields {missing}"
        )


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_confidence_in_allowed_set(scenario_id: str, ticker: str):
    gold = _load(scenario_id, ticker)
    assert gold["confidence"] in ("high", "partial", "low", "draft"), (
        f"{scenario_id}/{ticker} confidence={gold['confidence']!r} not in allowed set"
    )


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_expected_difficulty_in_allowed_set(scenario_id: str, ticker: str):
    gold = _load(scenario_id, ticker)
    assert gold["expected_difficulty"] in ("easy", "medium", "hard")


@pytest.mark.parametrize("scenario_id,ticker", EXPECTED_FIXTURES)
def test_source_spans_resolve_when_corpus_built(scenario_id: str, ticker: str):
    """When ``source.txt`` exists for this ticker, every source_span
    must resolve to a non-empty substring of source.txt.

    When source.txt isn't built (corpus build hasn't run), the test
    skips — fixtures stay structurally valid even before the live
    extraction lands.
    """
    source_path = RAG_CORPUS / ticker / "source.txt"
    if not source_path.exists():
        pytest.skip(f"source.txt for {ticker} not built yet")

    src = source_path.read_text(encoding="utf-8")
    gold = _load(scenario_id, ticker)
    spans = gold.get("source_spans", [])
    for i, span in enumerate(spans):
        start = int(span.get("char_offset_start", 0))
        end = int(span.get("char_offset_end", 0))
        if start == 0 and end == 0:
            # Placeholder offsets pre-corpus-build — skip without failing.
            continue
        assert 0 <= start < end <= len(src), (
            f"{scenario_id}/{ticker} source_spans[{i}] offsets out of range"
        )
        assert src[start:end].strip(), (
            f"{scenario_id}/{ticker} source_spans[{i}] resolves to empty text"
        )


def test_resolved_questions_match_template_resolution():
    """Each fixture's resolved_question should match what
    scenario_resolver.resolve(template, ticker) produces."""
    from kgspin_demo_app.services import scenario_resolver as sr
    for scenario_id, ticker in EXPECTED_FIXTURES:
        gold = _load(scenario_id, ticker)
        try:
            template = sr.get_template(scenario_id)
        except sr.ScenarioNotFound:
            pytest.fail(f"Template {scenario_id} not found")
        resolved = sr.resolve(template, ticker=ticker)
        # Allow whitespace differences (gold may have trailing newlines etc).
        assert resolved.question.strip() == gold["resolved_question"].strip(), (
            f"{scenario_id}/{ticker} resolved_question drift"
        )
