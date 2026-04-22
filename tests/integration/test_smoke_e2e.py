"""Sprint 09 Task 9 — full smoke: shim admin + register-fetchers + sec lander.

Exercises the complete CLI path against an in-process FastAPI shim so
the smoke passes in CI without a live kgspin-admin instance. Validates
the post-Task-2 shape fixes (register_fetcher body carries pointer;
pointer endpoint is /pointer/{id}).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def admin_shim():
    """Start a local admin shim on 127.0.0.1:<random> — see
    tests/manual/admin_shim.py for the shape it implements."""
    from tests.manual.admin_shim import start_shim
    with start_shim() as shim:
        yield shim


@pytest.fixture
def env_points_at_shim(admin_shim, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KGSPIN_ADMIN_URL", admin_shim.url)
    yield admin_shim


def test_register_fetchers_against_shim(env_points_at_shim) -> None:
    """Sprint 11 (ADR-004): register-fetchers against the shim produces
    5 fetcher records (sec_edgar, clinicaltrials_gov, marketaux,
    yahoo_rss, newsapi) and a second run keeps the same 5 (idempotency)."""
    from kgspin_demo_app.cli.register_fetchers import main

    rc1 = main([])
    assert rc1 == 0
    listed = env_points_at_shim.store
    fetcher_resources = [r for r in listed.values() if r.kind.value == "fetcher"]
    assert len(fetcher_resources) == 5

    rc2 = main([])
    assert rc2 == 0
    fetcher_resources_2 = [r for r in listed.values() if r.kind.value == "fetcher"]
    assert len(fetcher_resources_2) == 5, \
        f"second run produced {len(fetcher_resources_2)} fetchers; expected 5 (idempotency)"


def test_sec_lander_cli_against_shim(
    tmp_path: Path, env_points_at_shim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full CLI invocation against the shim. Stub network to avoid
    live EDGAR hits."""
    from kgspin_demo_app.landers import sec as sec_mod
    from kgspin_demo_app.landers.sec import main

    monkeypatch.setenv("SEC_USER_AGENT", "Smoke Test smoke@example.com")

    SAMPLE_FILING_HTML = "<html>smoke 10-K</html>"

    # 2026-04-22: rewritten to mock edgartools (SecLander 3.0.0).
    import sys as _sys
    from unittest.mock import MagicMock as _MagicMock
    _filing = _MagicMock()
    _filing.html.return_value = SAMPLE_FILING_HTML
    _filing.accession_number = "000000000-00-000000"
    _filing.filing_date = "2026-04-17"
    _filing.filing_url = "https://www.sec.gov/smoke-index.htm"
    _filing.cik = "0000000000"
    _filing.company = "SMOKE CO"
    _filing.period_of_report = "2025-12-31"
    _filings = _MagicMock()
    _filings.__len__.return_value = 1
    _filings.__getitem__.return_value = _filing
    _company = _MagicMock()
    _company.not_found = False
    _company.name = "SMOKE CO"
    _company.cik = "0000000000"
    _company.get_filings.return_value = _filings
    _fake_edgar = _MagicMock()
    _fake_edgar.set_identity = _MagicMock()
    _fake_edgar.Company = _MagicMock(return_value=_company)
    monkeypatch.setitem(_sys.modules, "edgar", _fake_edgar)

    rc = main([
        "--ticker", "TST",
        "--filing", "10-K",
        "--date", "2026-04-17",
        "--output-root", str(tmp_path / "corpus"),
    ])
    assert rc == 0, f"sec lander CLI exited {rc}"

    # Shim should now have a corpus_document record
    corpus_docs = [r for r in env_points_at_shim.store.values()
                   if r.kind.value == "corpus_document"]
    assert len(corpus_docs) == 1
    record = corpus_docs[0]
    # Pointer points at the bytes on disk
    assert Path(record.pointer.value).read_text(encoding="utf-8") == SAMPLE_FILING_HTML
    # Actor was set by the lander CLI
    assert record.provenance.registered_by == "fetcher:sec_edgar"
    # Metadata round-tripped
    assert record.metadata["domain"] == "financial"
    assert record.metadata["source"] == "sec_edgar"
    assert record.metadata["identifier"]["ticker"] == "TST"
