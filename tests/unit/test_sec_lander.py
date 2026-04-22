"""SecLander tests — edgartools-backed (LANDER_VERSION 3.0.0, 2026-04-22).

Covers the VP Eng test-eval criteria for the DocumentFetcher contract:
1. ``fetch()`` returns a ``FetchResult`` with FilePointer + metadata + hash
2. metadata includes accession / filing_date / cik (real) / company_name_as_filed
   / period_of_report / filing_type / source_url / bytes_written / company (nested)
3. hash is computed from the bytes written to disk (verify by re-hashing)
4. FetcherNotFoundError on empty filings list
5. FetcherError on edgartools downstream failures
6. ticker + form identifier validation
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
    FetcherNotFoundError,
)
from kgspin_interface.resources import FilePointer


SAMPLE_FILING_HTML = "<html><body>10-K test filing body — J&J, Stelara, Abiomed</body></html>"


@pytest.fixture
def sec_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.setenv("SEC_USER_AGENT", "Test Tester test@example.com")
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(tmp_path / "corpus"))
    return {"output_root": tmp_path / "corpus"}


def _fake_edgartools_module(
    *,
    filings_count: int = 1,
    html: str = SAMPLE_FILING_HTML,
    not_found: bool = False,
    filing_kwargs: dict | None = None,
    company_kwargs: dict | None = None,
) -> MagicMock:
    """Build a mock of the ``edgar`` module surface SecLander uses.

    ``filings_count`` drives how many filings the mock Company returns; 0
    triggers FetcherNotFoundError. ``html`` is returned by
    ``filing.html()``. ``not_found=True`` flips the Company's not_found
    flag (some delisted tickers).
    """
    fk = filing_kwargs or {}
    ck = company_kwargs or {}

    filing = MagicMock()
    filing.html.return_value = html
    filing.accession_number = fk.get("accession_number", "0000200406-26-000016")
    filing.filing_date = fk.get("filing_date", "2026-02-11")
    filing.filing_url = fk.get("filing_url", "https://www.sec.gov/Archives/edgar/data/200406/000020040626000016/")
    filing.cik = fk.get("cik", "0000200406")
    filing.company = fk.get("company", "JOHNSON & JOHNSON")
    filing.period_of_report = fk.get("period_of_report", "2025-12-28")

    filings = MagicMock()
    filings.__len__.return_value = filings_count
    filings.__getitem__.return_value = filing

    address = MagicMock()
    address.street1 = "One Johnson & Johnson Plaza"
    address.street2 = None
    address.city = "New Brunswick"
    address.state_or_country = "NJ"
    address.zipcode = "08933"

    company = MagicMock()
    company.not_found = not_found
    company.name = ck.get("name", "JOHNSON & JOHNSON")
    company.display_name = ck.get("display_name", "Johnson & Johnson")
    company.cik = ck.get("cik", "0000200406")
    company.sic = ck.get("sic", "2834")
    company.industry = ck.get("industry", "Pharmaceutical Preparations")
    company.business_category = ck.get("business_category", "Life Sciences")
    company.fiscal_year_end = ck.get("fiscal_year_end", "1228")
    company.business_address = address
    company.mailing_address = address
    company.tickers = ck.get("tickers", ["JNJ"])
    company.filer_category = ck.get("filer_category", "Large Accelerated Filer")
    company.filer_type = ck.get("filer_type", "Operating Company")
    company.is_foreign = ck.get("is_foreign", False)
    company.is_fund = ck.get("is_fund", False)
    company.get_filings.return_value = filings

    edgar_mod = MagicMock()
    edgar_mod.set_identity = MagicMock()
    edgar_mod.Company = MagicMock(return_value=company)
    return edgar_mod


def _inject_fake_edgartools(monkeypatch: pytest.MonkeyPatch, fake_mod: MagicMock) -> None:
    """Install ``fake_mod`` as ``import edgar`` (SecLander imports edgar lazily inside the fetch)."""
    import sys
    monkeypatch.setitem(sys.modules, "edgar", fake_mod)


def test_sec_lander_is_a_document_fetcher() -> None:
    from kgspin_demo_app.landers.sec import SecLander
    lander = SecLander()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "sec_edgar"
    assert lander.version == "3.0.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_fetch_happy_path_returns_fetch_result(
    sec_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch() returns a FetchResult with primary-HTML-only on disk + full metadata."""
    from kgspin_demo_app.landers.sec import SecLander

    fake_edgar = _fake_edgartools_module()
    _inject_fake_edgartools(monkeypatch, fake_edgar)

    lander = SecLander()
    result = lander.fetch(
        ticker="JNJ",
        form="10-K",
        output_root=sec_env["output_root"],
        date="2026-04-22",
    )

    # FetchResult shape
    assert isinstance(result, FetchResult)
    assert isinstance(result.pointer, FilePointer)
    assert result.pointer.type == "file"
    landed_path = Path(result.pointer.value)
    assert landed_path.is_file()
    assert landed_path.read_text(encoding="utf-8") == SAMPLE_FILING_HTML

    # Path is under the caller-supplied output_root
    assert sec_env["output_root"] in landed_path.parents

    # Filing-level metadata (accession, filing_date, real CIK, company name as filed)
    assert result.metadata["accession"] == "0000200406-26-000016"
    assert result.metadata["filing_date"] == "2026-02-11"
    assert result.metadata["filing_url"].startswith("https://www.sec.gov/")
    assert result.metadata["cik"] == "0000200406"  # real CIK, NOT the ticker
    assert result.metadata["company_name_as_filed"] == "JOHNSON & JOHNSON"
    assert result.metadata["period_of_report"] == "2025-12-28"
    assert result.metadata["filing_type"] == "10-K"
    assert result.metadata["source_url"].startswith("https://www.sec.gov/")
    assert result.metadata["bytes_written"] == len(SAMPLE_FILING_HTML.encode("utf-8"))
    assert result.metadata["lander_name"] == "sec_edgar"
    assert result.metadata["lander_version"] == "3.0.0"
    assert result.metadata["http_status"] == 200

    # Company-level metadata — new in 3.0.0
    company = result.metadata["company"]
    assert company["canonical_name"] == "JOHNSON & JOHNSON"
    assert company["cik"] == "0000200406"
    assert company["sic"] == "2834"
    assert company["industry"] == "Pharmaceutical Preparations"
    assert company["business_category"] == "Life Sciences"
    assert company["fiscal_year_end"] == "1228"
    assert company["business_address"]["city"] == "New Brunswick"
    assert company["business_address"]["state_or_country"] == "NJ"
    assert company["business_address"]["zipcode"] == "08933"
    assert company["tickers"] == ["JNJ"]
    assert company["filer_category"] == "Large Accelerated Filer"

    # VP Eng test-eval: hash is computed from the bytes actually on disk.
    expected_hash = hashlib.sha256(landed_path.read_bytes()).hexdigest()
    assert result.hash == expected_hash


def test_fetch_empty_filings_raises_fetcher_not_found(
    sec_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ticker with no filings → FetcherNotFoundError, not FetcherError."""
    from kgspin_demo_app.landers.sec import SecLander

    fake_edgar = _fake_edgartools_module(filings_count=0)
    _inject_fake_edgartools(monkeypatch, fake_edgar)

    lander = SecLander()
    with pytest.raises(FetcherNotFoundError) as excinfo:
        lander.fetch(
            ticker="ZZZZZ",
            form="10-K",
            output_root=sec_env["output_root"],
        )
    assert "ZZZZZ" in str(excinfo.value) or "10-K" in str(excinfo.value)


def test_fetch_not_found_company_raises_fetcher_not_found(
    sec_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ticker that edgartools flags not_found → FetcherNotFoundError."""
    from kgspin_demo_app.landers.sec import SecLander

    fake_edgar = _fake_edgartools_module(not_found=True)
    _inject_fake_edgartools(monkeypatch, fake_edgar)

    lander = SecLander()
    with pytest.raises(FetcherNotFoundError):
        lander.fetch(
            ticker="XXXXX",
            form="10-K",
            output_root=sec_env["output_root"],
        )


def test_fetch_html_empty_raises_fetcher_error(
    sec_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edgartools ``filing.html()`` returning empty → FetcherError."""
    from kgspin_demo_app.landers.sec import SecLander

    fake_edgar = _fake_edgartools_module(html="")
    _inject_fake_edgartools(monkeypatch, fake_edgar)

    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            ticker="JNJ",
            form="10-K",
            output_root=sec_env["output_root"],
        )
    assert "empty" in str(excinfo.value).lower()


def test_fetch_edgartools_downstream_error_wraps_as_fetcher_error(
    sec_env: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception from edgartools internals → FetcherError (not bare exception)."""
    from kgspin_demo_app.landers.sec import SecLander

    fake_edgar = _fake_edgartools_module()
    # Make filing.html() blow up
    company = fake_edgar.Company.return_value
    filings = company.get_filings.return_value
    filings.__getitem__.return_value.html.side_effect = RuntimeError("edgartools: network timeout")
    _inject_fake_edgartools(monkeypatch, fake_edgar)

    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            ticker="JNJ",
            form="10-K",
            output_root=sec_env["output_root"],
        )
    assert "filing.html()" in str(excinfo.value) or "network timeout" in str(excinfo.value)


def test_fetch_invalid_ticker_raises_fetcher_error(sec_env: dict) -> None:
    from kgspin_demo_app.landers.sec import SecLander
    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            ticker="JNJ; rm -rf /",
            form="10-K",
            output_root=sec_env["output_root"],
        )
    assert "invalid ticker" in str(excinfo.value).lower()


def test_fetch_invalid_form_raises_fetcher_error(sec_env: dict) -> None:
    from kgspin_demo_app.landers.sec import SecLander
    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            ticker="JNJ",
            form="NOT-A-FORM",  # type: ignore[arg-type]
            output_root=sec_env["output_root"],
        )
    assert "invalid form" in str(excinfo.value).lower()


def test_fetch_missing_user_agent_raises_fetcher_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """SEC auth comes from EDGAR_IDENTITY (primary) OR SEC_USER_AGENT
    (fallback). Both must be missing for the FetcherError to surface.
    """
    from kgspin_demo_app.landers.sec import SecLander
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            ticker="JNJ",
            form="10-K",
            output_root=tmp_path / "corpus",
        )
    err = str(excinfo.value).lower()
    assert "edgar_identity" in err or "sec_user_agent" in err
