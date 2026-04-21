"""Sprint 12 Task 10.1 — clinical-query derivation service tests.

Pins the service extracted from ``demo_compare.py`` per VP Eng Phase 3
"God Method risk" flag. Coverage was previously implicit (through
``demo_compare`` integration tests); this file makes it explicit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from kgspin_interface.resources import CorpusDocumentMetadata, FilePointer

from kgspin_demo_app.services.clinical_query import derive_clinical_query_from_nct
from tests.fakes.registry_client import FakeRegistryClient


def _seed_trial(
    fake: FakeRegistryClient,
    tmp_path: Path,
    nct: str,
    *,
    condition: str | None = None,
    interventions: object = None,
    fetch_time: datetime | None = None,
    body: bytes = b"{}",
) -> None:
    """Register one clinical corpus_document with seed extras."""
    fetch_time = fetch_time or datetime.now(timezone.utc)
    extras: dict[str, object] = {}
    if condition is not None:
        extras["condition"] = condition
    if interventions is not None:
        extras["interventions"] = interventions
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / f"{nct}.json"
    raw.write_bytes(body)
    fake.register_corpus_document(
        CorpusDocumentMetadata(
            domain="clinical",
            source="clinicaltrials_gov",
            identifier={"nct": nct},
            fetch_timestamp=fetch_time,
            source_extras=extras or None,
        ),
        FilePointer(value=str(raw)),
        "test:clinical_query",
    )


def test_returns_default_when_registry_empty(tmp_path: Path) -> None:
    fake = FakeRegistryClient()
    out = derive_clinical_query_from_nct(fake, "NCT12345678", default="fallback")
    assert out == "fallback"


def test_returns_default_when_nct_not_registered(tmp_path: Path) -> None:
    fake = FakeRegistryClient()
    _seed_trial(fake, tmp_path, "NCT99999999", condition="diabetes")
    out = derive_clinical_query_from_nct(fake, "NCT12345678", default="fallback")
    assert out == "fallback"


def test_returns_condition_only_when_no_interventions(tmp_path: Path) -> None:
    fake = FakeRegistryClient()
    _seed_trial(fake, tmp_path, "NCT12345678", condition="lung cancer")
    out = derive_clinical_query_from_nct(fake, "NCT12345678")
    assert out == "lung cancer"


def test_combines_condition_plus_top_two_interventions_from_list(tmp_path: Path) -> None:
    """Interventions list → condition + first 2 only (keeps query short)."""
    fake = FakeRegistryClient()
    _seed_trial(
        fake, tmp_path, "NCT12345678",
        condition="obesity",
        interventions=["semaglutide", "tirzepatide", "liraglutide", "orlistat"],
    )
    out = derive_clinical_query_from_nct(fake, "NCT12345678")
    # Only top-2 interventions travel; liraglutide / orlistat dropped.
    assert out == "obesity semaglutide tirzepatide"


def test_combines_interventions_from_comma_separated_string(tmp_path: Path) -> None:
    """Accepts the legacy shape where interventions ship as a CSV string."""
    fake = FakeRegistryClient()
    _seed_trial(
        fake, tmp_path, "NCT12345678",
        condition="heart failure",
        interventions="sacubitril, valsartan, dapagliflozin",
    )
    out = derive_clinical_query_from_nct(fake, "NCT12345678")
    assert out == "heart failure sacubitril valsartan"


def test_sanitizes_invalid_chars_to_spaces(tmp_path: Path) -> None:
    """Condition with punctuation / special chars → alphanumerics only."""
    fake = FakeRegistryClient()
    _seed_trial(
        fake, tmp_path, "NCT12345678",
        condition="<script>covid-19</script>!!!",
        interventions=None,
    )
    out = derive_clinical_query_from_nct(fake, "NCT12345678")
    # Angle brackets, slash, bangs all stripped; whitespace collapsed;
    # hyphen + underscore preserved (matches _NEWS_QUERY_RE).
    assert "<" not in out and ">" not in out and "!" not in out
    assert "covid-19" in out


def test_truncates_to_100_chars(tmp_path: Path) -> None:
    """Very long condition → capped at 100 chars."""
    fake = FakeRegistryClient()
    long_condition = "x" * 500
    _seed_trial(fake, tmp_path, "NCT12345678", condition=long_condition)
    out = derive_clinical_query_from_nct(fake, "NCT12345678")
    assert len(out) == 100


def test_returns_default_when_registry_raises(tmp_path: Path) -> None:
    """Graceful-degrade: any exception from client.list() → default."""

    class BrokenClient:
        def list(self, *args, **kwargs):
            raise RuntimeError("admin exploded")

    out = derive_clinical_query_from_nct(BrokenClient(), "NCT12345678", default="fb")
    assert out == "fb"


def test_returns_default_when_derived_is_empty_after_sanitize(tmp_path: Path) -> None:
    """Condition with ONLY invalid chars → sanitized to empty → default."""
    fake = FakeRegistryClient()
    _seed_trial(fake, tmp_path, "NCT12345678", condition="<<<>>>///!!!")
    out = derive_clinical_query_from_nct(fake, "NCT12345678", default="fb")
    assert out == "fb"


def test_ignores_non_list_non_string_interventions(tmp_path: Path) -> None:
    """If interventions is a dict or None, fall back to condition only."""
    fake = FakeRegistryClient()
    _seed_trial(
        fake, tmp_path, "NCT12345678",
        condition="asthma",
        interventions={"drug": "albuterol"},  # wrong shape
    )
    out = derive_clinical_query_from_nct(fake, "NCT12345678")
    assert out == "asthma"
