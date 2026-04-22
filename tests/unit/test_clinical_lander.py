"""Sprint 09 Task 4 — ClinicalLander tests."""

from __future__ import annotations

import hashlib
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


SAMPLE_STUDY_JSON = b'{"protocolSection":{"identificationModule":{"nctId":"NCT01234567"}}}'


class _FakeResp:
    def __init__(self, *, status: int, content: bytes = b"", headers: dict | None = None):
        self.status_code = status
        self._content = content
        self.headers = headers or {}

    def iter_content(self, chunk_size=64 * 1024):
        if self._content:
            yield self._content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def test_clinical_lander_is_a_document_fetcher() -> None:
    from kgspin_demo_app.landers.clinical import ClinicalLander
    lander = ClinicalLander()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "clinicaltrials_gov"
    assert lander.version == "2.1.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_fetch_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kgspin_demo_app.landers import clinical as mod

    resp = _FakeResp(status=200, content=SAMPLE_STUDY_JSON, headers={"ETag": "abc"})
    monkeypatch.setattr(mod, "_get_study", lambda nct, **kw: resp)

    lander = mod.ClinicalLander()
    result = lander.fetch(
        nct="NCT01234567",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )

    assert isinstance(result, FetchResult)
    assert isinstance(result.pointer, FilePointer)
    landed = Path(result.pointer.value)
    assert landed.is_file()
    assert landed.read_bytes() == SAMPLE_STUDY_JSON
    assert result.metadata["nct_id"] == "NCT01234567"
    assert result.metadata["source_url"].endswith("NCT01234567")
    assert result.metadata["etag"] == "abc"
    assert result.metadata["bytes_written"] == len(SAMPLE_STUDY_JSON)
    # Hash is from bytes on disk (VP Eng test-eval)
    assert result.hash == hashlib.sha256(landed.read_bytes()).hexdigest()


def test_fetch_404_raises_fetcher_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kgspin_demo_app.landers import clinical as mod

    def _raise_nf(nct, **kw):
        raise FetcherNotFoundError(f"ClinicalTrials.gov has no study {nct}")
    monkeypatch.setattr(mod, "_get_study", _raise_nf)

    lander = mod.ClinicalLander()
    with pytest.raises(FetcherNotFoundError):
        lander.fetch(
            nct="NCT99999999",
            output_root=tmp_path / "corpus",
        )


def test_invalid_nct_raises_fetcher_error(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.clinical import ClinicalLander
    lander = ClinicalLander()
    for bad in ["../../etc", "NCT123", "NOT-AN-NCT", ""]:
        with pytest.raises(FetcherError):
            lander.fetch(
                nct=bad,
                output_root=tmp_path / "corpus",
            )
