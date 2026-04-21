"""Sprint 09 Task 6 — mock_provider deprecation + DocumentFetcher rewrite."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
    FetcherNotFoundError,
)


def test_import_triggers_deprecation_warning() -> None:
    """Importing the module emits a DeprecationWarning (VP Eng requirement)."""
    import importlib
    import sys

    # Force a fresh import so the warning fires even if the module was
    # already imported earlier in the test session.
    if "kgspin_demo_app.corpus.mock_provider" in sys.modules:
        del sys.modules["kgspin_demo_app.corpus.mock_provider"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("kgspin_demo_app.corpus.mock_provider")
        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert dep_warnings, \
            "expected DeprecationWarning on import; got: " \
            f"{[(w.category.__name__, str(w.message)[:80]) for w in caught]}"
        assert "deprecated" in str(dep_warnings[0].message).lower()
        assert "hardening" in str(dep_warnings[0].message).lower()


def test_mock_is_a_document_fetcher() -> None:
    # Suppress the deprecation warning for these tests — we're exercising
    # the preserved surface, not discouraging its use.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kgspin_demo_app.corpus.mock_provider import MockDocumentFetcher

    lander = MockDocumentFetcher()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "demo_mock"
    assert lander.version == "2.0.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_mock_fetch_returns_fetch_result_for_existing_fixture(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kgspin_demo_app.corpus.mock_provider import MockDocumentFetcher

    # Create a sample fixture in a temp fixture_root.
    fixture_root = tmp_path / "corpus"
    fixture_root.mkdir(parents=True)
    (fixture_root / "HELLO.html").write_bytes(b"<html>hi</html>")

    lander = MockDocumentFetcher(fixture_root=fixture_root)
    result = lander.fetch(
        domain="mock",
        source="demo_mock",
        identifier={"stem": "HELLO"},
    )
    assert isinstance(result, FetchResult)
    assert result.pointer.type == "file"
    assert Path(result.pointer.value).is_file()
    assert result.metadata["stem"] == "HELLO"
    assert result.metadata["deprecated"] is True
    assert result.hash  # sha256 hex digest


def test_mock_fetch_missing_fixture_raises_not_found(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kgspin_demo_app.corpus.mock_provider import MockDocumentFetcher

    lander = MockDocumentFetcher(fixture_root=tmp_path / "corpus")
    with pytest.raises(FetcherNotFoundError):
        lander.fetch(
            domain="mock",
            source="demo_mock",
            identifier={"stem": "DOES_NOT_EXIST"},
        )


def test_mock_missing_stem_raises_fetcher_error(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kgspin_demo_app.corpus.mock_provider import MockDocumentFetcher

    lander = MockDocumentFetcher(fixture_root=tmp_path / "corpus")
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="mock",
            source="demo_mock",
            identifier={},  # missing stem
        )


def test_filestore_reader_is_deleted() -> None:
    """D6: src/kgspin_demo_app/corpus/filestore_reader.py is gone."""
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("kgspin_demo_app.corpus.filestore_reader")


def test_no_in_repo_import_of_filestore_reader() -> None:
    """D6 acceptance: no surviving `from kgspin_demo_app.corpus.filestore_reader`
    import in production code (src/). Test files that reference the symbol
    name as a literal (like this one) are excluded."""
    import subprocess
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py",
         "from kgspin_demo_app.corpus.filestore_reader",
         "src/"],
        capture_output=True, text=True,
    )
    # grep returns 1 when no matches found, which is what we want.
    assert result.returncode == 1, \
        f"found stale filestore_reader imports in src/:\n{result.stdout}"
