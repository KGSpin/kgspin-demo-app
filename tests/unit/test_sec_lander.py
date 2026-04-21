"""Sprint 09 Task 3 — tests for ``SecLander(DocumentFetcher)``.

Covers the VP Eng test-eval criteria:
1. ``fetch()`` returns a ``FetchResult`` with FilePointer + metadata + hash
2. metadata includes cik / accession / filing_type / etag / bytes_written / source_url
3. hash is computed from the bytes written to disk (verify by re-hashing)
4. FetcherNotFoundError on empty Atom feed
5. FetcherError on 5xx after retries
6. ticker + form identifier validation
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
    FetcherNotFoundError,
)
from kgspin_interface.resources import FilePointer


SAMPLE_ATOM_FEED_OK = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>10-K — Test Company (0000123456)</title>
    <link href="https://www.sec.gov/Archives/edgar/data/123/000012345-25-000001-index.htm" />
    <category term="000012345-25-000001" />
    <updated>2025-02-13T00:00:00-05:00</updated>
  </entry>
</feed>
"""

SAMPLE_ATOM_FEED_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
</feed>
"""

SAMPLE_FILING_HTML = b"<html><body>10-K test filing body</body></html>"


@pytest.fixture
def sec_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.setenv("SEC_USER_AGENT", "Test Tester test@example.com")
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(tmp_path / "corpus"))
    return {"output_root": tmp_path / "corpus"}


def test_sec_lander_is_a_document_fetcher() -> None:
    from kgspin_demo_app.landers.sec import SecLander
    lander = SecLander()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "sec_edgar"
    assert lander.version == "2.0.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_fetch_happy_path_returns_fetch_result(sec_env: dict, httpx_mock) -> None:
    """fetch() returns a properly-shaped FetchResult."""
    from kgspin_demo_app.landers import sec as sec_mod

    # Two expected SEC calls: the Atom feed, then the filing itself.
    # Use requests_mock-style fallthrough via monkeypatching the low-level
    # helper so we don't need pytest_httpx for requests (only httpx).
    # Simpler: patch _get_with_retry to return canned responses.
    atom_resp = _mock_response(text=SAMPLE_ATOM_FEED_OK, status=200)
    filing_resp = _mock_response(content_bytes=SAMPLE_FILING_HTML, status=200,
                                 headers={"ETag": "W/\"abc123\""})

    call_log: list[str] = []
    def fake_get(url, **kwargs):
        call_log.append(url)
        if "browse-edgar" in url:
            return atom_resp
        return filing_resp

    from kgspin_demo_app.landers import sec as sec_mod_local
    orig = sec_mod_local._get_with_retry
    sec_mod_local._get_with_retry = fake_get  # type: ignore[assignment]
    try:
        lander = sec_mod_local.SecLander()
        result = lander.fetch(
            domain="financial",
            source="sec_edgar",
            identifier={"ticker": "TST", "form": "10-K"},
            output_root=sec_env["output_root"],
            date="2025-02-13",
        )
    finally:
        sec_mod_local._get_with_retry = orig  # type: ignore[assignment]

    # FetchResult shape
    assert isinstance(result, FetchResult)
    assert isinstance(result.pointer, FilePointer)
    assert result.pointer.type == "file"
    landed_path = Path(result.pointer.value)
    assert landed_path.is_file()
    assert landed_path.read_bytes() == SAMPLE_FILING_HTML

    # Path is under the caller-supplied output_root
    assert sec_env["output_root"] in landed_path.parents

    # metadata contains the required fields
    assert result.metadata["cik"] == "TST"
    assert result.metadata["accession"] == "000012345-25-000001"
    assert result.metadata["filing_type"] == "10-K"
    assert result.metadata["etag"] == 'W/"abc123"'
    assert result.metadata["bytes_written"] == len(SAMPLE_FILING_HTML)
    assert result.metadata["source_url"].endswith(".txt")
    assert result.metadata["lander_name"] == "sec_edgar"
    assert result.metadata["lander_version"] == "2.0.0"
    assert result.metadata["http_status"] == 200

    # VP Eng test-eval: hash is computed from the bytes actually on disk —
    # re-hash the file independently and compare.
    expected_hash = hashlib.sha256(landed_path.read_bytes()).hexdigest()
    assert result.hash == expected_hash


def test_fetch_empty_feed_raises_fetcher_not_found(sec_env: dict) -> None:
    """A ticker with no filings → FetcherNotFoundError, not FetcherError."""
    from kgspin_demo_app.landers import sec as sec_mod
    atom_resp = _mock_response(text=SAMPLE_ATOM_FEED_EMPTY, status=200)

    orig = sec_mod._get_with_retry
    sec_mod._get_with_retry = lambda url, **kw: atom_resp  # type: ignore[assignment]
    try:
        lander = sec_mod.SecLander()
        with pytest.raises(FetcherNotFoundError) as excinfo:
            lander.fetch(
                domain="financial",
                source="sec_edgar",
                identifier={"ticker": "ZZZ", "form": "10-K"},
                output_root=sec_env["output_root"],
            )
        assert "ZZZ" in str(excinfo.value) or "10-K" in str(excinfo.value)
    finally:
        sec_mod._get_with_retry = orig  # type: ignore[assignment]


def test_fetch_invalid_ticker_raises_fetcher_error(sec_env: dict) -> None:
    from kgspin_demo_app.landers.sec import SecLander
    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            domain="financial",
            source="sec_edgar",
            identifier={"ticker": "JNJ; rm -rf /", "form": "10-K"},
            output_root=sec_env["output_root"],
        )
    assert "invalid ticker" in str(excinfo.value).lower()


def test_fetch_invalid_form_raises_fetcher_error(sec_env: dict) -> None:
    from kgspin_demo_app.landers.sec import SecLander
    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            domain="financial",
            source="sec_edgar",
            identifier={"ticker": "JNJ", "form": "NOT-A-FORM"},
            output_root=sec_env["output_root"],
        )
    assert "invalid form" in str(excinfo.value).lower()


def test_fetch_missing_user_agent_raises_fetcher_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sprint 11 post-env-var consolidation: SEC auth comes from either
    EDGAR_IDENTITY (primary, pre-existing convention) OR SEC_USER_AGENT
    (fallback). Both must be missing for the FetcherError to surface.
    """
    from kgspin_demo_app.landers.sec import SecLander
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    lander = SecLander()
    with pytest.raises(FetcherError) as excinfo:
        lander.fetch(
            domain="financial",
            source="sec_edgar",
            identifier={"ticker": "JNJ", "form": "10-K"},
            output_root=tmp_path / "corpus",
        )
    err = str(excinfo.value).lower()
    assert "edgar_identity" in err or "sec_user_agent" in err


def test_fetch_5xx_after_retries_raises_fetcher_error(
    sec_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After tenacity exhausts retries on a 5xx, we surface FetcherError."""
    import requests
    from kgspin_demo_app.landers import sec as sec_mod

    # Simulate the Atom feed returning 500 — after retry exhaustion
    # tenacity lets the HTTPError escape; our fetch() wraps it.
    def always_500(url, **kwargs):
        resp = requests.Response()
        resp.status_code = 500
        req = requests.PreparedRequest()
        req.method = "GET"
        req.url = url
        resp.request = req
        raise requests.HTTPError("500 Server Error", response=resp)

    orig = sec_mod._get_with_retry
    sec_mod._get_with_retry = always_500  # type: ignore[assignment]
    try:
        lander = sec_mod.SecLander()
        with pytest.raises(FetcherError) as excinfo:
            lander.fetch(
                domain="financial",
                source="sec_edgar",
                identifier={"ticker": "JNJ", "form": "10-K"},
                output_root=sec_env["output_root"],
            )
        assert "500" in str(excinfo.value) or "http" in str(excinfo.value).lower()
    finally:
        sec_mod._get_with_retry = orig  # type: ignore[assignment]


# ---- helpers --------------------------------------------------------------


def _mock_response(
    *,
    text: str | None = None,
    content_bytes: bytes | None = None,
    status: int = 200,
    headers: dict | None = None,
):
    """Minimal duck-typed requests.Response replacement."""
    class _Resp:
        def __init__(self):
            self.text = text or ""
            self._content = content_bytes or (text.encode("utf-8") if text else b"")
            self.status_code = status
            self.headers = headers or {}

        def iter_content(self, chunk_size=64 * 1024):
            # Yield the whole body in one chunk — simpler than real streaming.
            if self._content:
                yield self._content

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(f"{self.status_code} error", response=self)
    return _Resp()
