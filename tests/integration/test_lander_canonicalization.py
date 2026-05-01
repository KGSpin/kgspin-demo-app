"""Integration tests for lander-side canonical-plaintext persistence (D2).

PRD-004 v5 Phase 5B commit 2 / D2. Asserts that after a lander writes
its raw artifact, ``source.txt + manifest.json`` are also persisted in
the same directory with content + hashes that round-trip through
``kgspin_interface.text.normalize``.

These tests don't hit live network — SecLander.fetch is monkey-patched
to bypass edgartools and ClinicalLander.fetch is exercised via
write_canonical_artifacts directly with synthetic inputs. The lander's
fetch+canonicalize+register seam is what we're pinning, not the
upstream HTTP behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kgspin_demo_app.landers.canonical import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    SOURCE_TEXT_FILENAME,
    sha256_bytes,
    write_canonical_artifacts,
)
from kgspin_interface.text.normalize import (
    NORMALIZATION_VERSION,
    canonical_plaintext_from_clinical_json,
    canonical_plaintext_from_html,
    plaintext_sha256,
)


# ---------------------------------------------------------------------------
# Direct write_canonical_artifacts tests (HTML)
# ---------------------------------------------------------------------------


def test_html_canonicalize_persists_source_text_and_manifest(tmp_path: Path):
    raw = b"<html><body><p>Apple Inc. is a company.</p></body></html>"
    raw_path = tmp_path / "raw.html"
    raw_path.write_bytes(raw)
    raw_sha = sha256_bytes(raw)

    artifacts = write_canonical_artifacts(
        raw_path=raw_path,
        raw_bytes=raw,
        raw_sha=raw_sha,
        kind="html",
        domain="financial",
        source="sec_edgar",
        lander_name="sec_edgar",
        lander_version="3.0.0",
        fetch_timestamp_utc="2026-05-01T12:00:00.000Z",
    )

    # Files exist in the same directory as raw.
    assert artifacts.source_text_path == tmp_path / SOURCE_TEXT_FILENAME
    assert artifacts.manifest_path == tmp_path / MANIFEST_FILENAME
    assert artifacts.source_text_path.exists()
    assert artifacts.manifest_path.exists()

    # source.txt content matches what canonical_plaintext_from_html returns.
    expected_plaintext, expected_sha = canonical_plaintext_from_html(raw.decode("utf-8"))
    assert artifacts.source_text_path.read_text(encoding="utf-8") == expected_plaintext
    assert artifacts.plaintext_sha == expected_sha
    assert artifacts.normalization_version == NORMALIZATION_VERSION
    assert artifacts.sponsor is None  # HTML path doesn't extract sponsor


def test_html_manifest_schema_shape(tmp_path: Path):
    raw = b"<html><body><p>Test.</p></body></html>"
    raw_path = tmp_path / "raw.html"
    raw_path.write_bytes(raw)

    write_canonical_artifacts(
        raw_path=raw_path,
        raw_bytes=raw,
        raw_sha=sha256_bytes(raw),
        kind="html",
        domain="financial",
        source="sec_edgar",
        lander_name="sec_edgar",
        lander_version="3.0.0",
        fetch_timestamp_utc="2026-05-01T12:00:00.000Z",
    )
    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))

    assert manifest["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["domain"] == "financial"
    assert manifest["source"] == "sec_edgar"
    assert manifest["raw"]["filename"] == "raw.html"
    assert manifest["raw"]["bytes"] == len(raw)
    assert manifest["raw"]["sha256"] == sha256_bytes(raw)
    assert manifest["source_text"]["filename"] == SOURCE_TEXT_FILENAME
    assert manifest["source_text"]["normalization_version"] == NORMALIZATION_VERSION
    assert manifest["lander"]["name"] == "sec_edgar"
    assert manifest["lander"]["version"] == "3.0.0"
    assert manifest["fetched_at"] == "2026-05-01T12:00:00.000Z"
    # HTML kind doesn't carry a clinical block.
    assert "clinical" not in manifest


def test_plaintext_sha_in_manifest_matches_recompute(tmp_path: Path):
    raw = b"<html><body><p>Round-trip me.</p></body></html>"
    raw_path = tmp_path / "raw.html"
    raw_path.write_bytes(raw)

    write_canonical_artifacts(
        raw_path=raw_path,
        raw_bytes=raw,
        raw_sha=sha256_bytes(raw),
        kind="html",
        domain="financial",
        source="sec_edgar",
        lander_name="sec_edgar",
        lander_version="3.0.0",
        fetch_timestamp_utc="2026-05-01T12:00:00.000Z",
    )
    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    plaintext = (tmp_path / SOURCE_TEXT_FILENAME).read_text(encoding="utf-8")

    # The SHA in the manifest must match a fresh recompute over the
    # written source.txt (catches any silent drift in the writer).
    assert manifest["source_text"]["sha256"] == plaintext_sha256(plaintext)


# ---------------------------------------------------------------------------
# Direct write_canonical_artifacts tests (clinical JSON)
# ---------------------------------------------------------------------------


_NCT_PAYLOAD = json.dumps({
    "protocolSection": {
        "identificationModule": {
            "officialTitle": "Stelara in Crohn's Disease",
            "nctId": "NCT00174785",
        },
        "descriptionModule": {"briefSummary": "Test summary."},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Centocor, Inc."}},
    },
}).encode("utf-8")


def test_clinical_canonicalize_persists_and_extracts_sponsor(tmp_path: Path):
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(_NCT_PAYLOAD)

    artifacts = write_canonical_artifacts(
        raw_path=raw_path,
        raw_bytes=_NCT_PAYLOAD,
        raw_sha=sha256_bytes(_NCT_PAYLOAD),
        kind="clinical_json",
        domain="clinical",
        source="clinicaltrials_gov",
        lander_name="clinicaltrials_gov",
        lander_version="2.1.0",
        fetch_timestamp_utc="2026-05-01T12:00:00.000Z",
    )

    assert artifacts.sponsor == "Centocor, Inc."
    expected_plaintext, expected_sha, expected_sponsor = canonical_plaintext_from_clinical_json(
        _NCT_PAYLOAD.decode("utf-8"),
    )
    assert artifacts.source_text_path.read_text(encoding="utf-8") == expected_plaintext
    assert artifacts.plaintext_sha == expected_sha
    assert expected_sponsor == "Centocor, Inc."


def test_clinical_manifest_carries_sponsor_block(tmp_path: Path):
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(_NCT_PAYLOAD)

    write_canonical_artifacts(
        raw_path=raw_path,
        raw_bytes=_NCT_PAYLOAD,
        raw_sha=sha256_bytes(_NCT_PAYLOAD),
        kind="clinical_json",
        domain="clinical",
        source="clinicaltrials_gov",
        lander_name="clinicaltrials_gov",
        lander_version="2.1.0",
        fetch_timestamp_utc="2026-05-01T12:00:00.000Z",
    )
    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["clinical"] == {"sponsor": "Centocor, Inc."}


# ---------------------------------------------------------------------------
# End-to-end via SecLander (edgartools mocked) and ClinicalLander
# ---------------------------------------------------------------------------


def test_sec_lander_persists_canonical_artifacts_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """SecLander.fetch's seam — bypass edgartools, exercise the
    canonicalize-and-write side effect."""
    from kgspin_demo_app.landers import sec as sec_mod

    fake_html = "<html><body><p>Apple Inc. designs smartphones.</p></body></html>"
    fake_filing_extras = {
        "accession": "0000320193-25-000001",
        "filing_date": "2025-09-30",
        "filing_url": "https://www.sec.gov/cgi-bin/browse-edgar",
        "cik": "0000320193",
        "company_name_as_filed": "APPLE INC",
        "period_of_report": "2025-09-30",
    }
    fake_company_extras = {
        "canonical_name": "Apple Inc.",
        "cik": "0000320193",
        "sic": "3571",
        "industry": "ELECTRONIC COMPUTERS",
        "business_category": None,
        "fiscal_year_end": "0930",
        "business_address": None,
        "mailing_address": None,
        "tickers": ["AAPL"],
        "filer_category": None,
        "filer_type": None,
        "is_foreign": False,
    }

    def _fake_edgartools_fetch(ticker, form, user_agent):
        return fake_html, fake_filing_extras, fake_company_extras

    monkeypatch.setattr(sec_mod, "_fetch_filing_via_edgartools", _fake_edgartools_fetch)
    monkeypatch.setenv("EDGAR_IDENTITY", "Test User test@example.com")

    lander = sec_mod.SecLander()
    result = lander.fetch(ticker="AAPL", form="10-K", output_root=tmp_path, date="2026-05-01")

    raw_path = Path(result.pointer.value)
    assert raw_path.exists()
    assert raw_path.read_text(encoding="utf-8") == fake_html

    # source.txt + manifest.json sit alongside raw.html.
    source_text_path = raw_path.parent / SOURCE_TEXT_FILENAME
    manifest_path = raw_path.parent / MANIFEST_FILENAME
    assert source_text_path.exists(), "lander did not write source.txt"
    assert manifest_path.exists(), "lander did not write manifest.json"

    # Plaintext content + SHA round-trip cleanly.
    plaintext, plaintext_sha = canonical_plaintext_from_html(fake_html)
    assert source_text_path.read_text(encoding="utf-8") == plaintext

    # Manifest pins all the identity fields the registry will read in D3.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["domain"] == "financial"
    assert manifest["source"] == "sec_edgar"
    assert manifest["raw"]["sha256"] == result.hash
    assert manifest["source_text"]["sha256"] == plaintext_sha
    assert manifest["source_text"]["normalization_version"] == NORMALIZATION_VERSION
    assert manifest["lander"]["name"] == "sec_edgar"

    # Result.metadata extras carry the new fields for D3 (admin registry).
    extras = result.metadata
    assert extras["plaintext_sha"] == plaintext_sha
    assert extras["plaintext_bytes"] == source_text_path.stat().st_size
    assert extras["normalization_version"] == NORMALIZATION_VERSION


def test_clinical_lander_persists_canonical_artifacts_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """ClinicalLander.fetch end-to-end — bypass requests.get."""
    from kgspin_demo_app.landers import clinical as clinical_mod

    payload = _NCT_PAYLOAD

    class _FakeResp:
        status_code = 200
        headers = {"etag": "fake-etag"}

        def iter_content(self, chunk_size: int):
            yield payload

        def raise_for_status(self):
            pass

    monkeypatch.setattr(clinical_mod, "_get_study", lambda *a, **kw: _FakeResp())

    lander = clinical_mod.ClinicalLander()
    result = lander.fetch(nct="NCT00174785", output_root=tmp_path, date="2026-05-01")

    raw_path = Path(result.pointer.value)
    assert raw_path.exists()

    source_text_path = raw_path.parent / SOURCE_TEXT_FILENAME
    manifest_path = raw_path.parent / MANIFEST_FILENAME
    assert source_text_path.exists()
    assert manifest_path.exists()

    plaintext, plaintext_sha, sponsor = canonical_plaintext_from_clinical_json(
        payload.decode("utf-8"),
    )
    assert source_text_path.read_text(encoding="utf-8") == plaintext
    assert sponsor == "Centocor, Inc."

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["domain"] == "clinical"
    assert manifest["source_text"]["sha256"] == plaintext_sha
    assert manifest["clinical"]["sponsor"] == "Centocor, Inc."

    extras = result.metadata
    assert extras["plaintext_sha"] == plaintext_sha
    assert extras["normalization_version"] == NORMALIZATION_VERSION
    assert extras["sponsor"] == "Centocor, Inc."
