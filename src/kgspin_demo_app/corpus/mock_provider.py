"""Deprecated mock DocumentFetcher — kept for cross-repo compatibility.

Sprint 09 Task 6 (VP Eng MAJOR directive): this module's previous
incarnation implemented the retired ``CorpusProvider`` Protocol (from
``kgspin_core.corpus``). REQ-005 removed that Protocol; Sprint 09
migrates to ``kgspin_interface.DocumentFetcher``. VP Eng directed:
do NOT delete this file even if no in-repo importer exists —
cross-repo consumers (tuner? core tests?) may still import it, and
the cost of breaking a sibling outweighs the cost of one deprecated
file.

This file now:
1. Defines ``MockDocumentFetcher(DocumentFetcher)`` — a canned-fixture
   fetcher satisfying the new ABC.
2. Triggers ``DeprecationWarning`` at module import time.
3. Is explicitly scheduled for removal in a post-Sprint-09 Hardening
   sprint once ecosystem validation confirms no external consumers.

See ADR-003 §mock_provider for the removal criteria + schedule.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
    FetcherNotFoundError,
)
from kgspin_interface.resources import FilePointer


# Emit at import time so any stale consumer (even one that only does
# ``import kgspin_demo_app.corpus.mock_provider``) sees the signal.
warnings.warn(
    "kgspin_demo_app.corpus.mock_provider is deprecated and preserved only for "
    "cross-repo compatibility during Sprint 09 ecosystem-validation. "
    "Use kgspin_interface.DocumentFetcher subclasses (e.g. SecLander) + "
    "tests.fakes.registry_client.FakeRegistryClient in new code. "
    "Scheduled for removal in a post-Sprint-09 Hardening sprint.",
    DeprecationWarning,
    stacklevel=2,
)


# Fixture root — same location the Sprint 07 mock provider used.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_FIXTURE_ROOT = _REPO_ROOT / "tests" / "fixtures" / "corpus"


class MockDocumentFetcher(DocumentFetcher):
    """Fixture-backed DocumentFetcher for offline demo + CI use.

    DEPRECATED — see module docstring. New code should build a real
    ``DocumentFetcher`` subclass and use ``FakeRegistryClient`` in
    tests rather than importing this class.

    ``identifier`` dict shape: ``{"stem": "<file stem>"}``. Files are
    looked up at ``tests/fixtures/corpus/{stem}.{html,txt}``.
    """

    name = "demo_mock"
    version = "2.0.0"
    contract_version = DOCUMENT_FETCHER_CONTRACT_VERSION

    def __init__(self, fixture_root: Path | None = None) -> None:
        self._fixture_root = Path(fixture_root) if fixture_root else _DEFAULT_FIXTURE_ROOT

    def _find_fixture(self, stem: str) -> Path | None:
        for ext in ("html", "txt"):
            p = self._fixture_root / f"{stem}.{ext}"
            if p.is_file():
                return p
        return None

    def fetch(
        self,
        domain: str,
        source: str,
        identifier: dict[str, str],
        **kwargs: Any,
    ) -> FetchResult:
        stem = (identifier.get("stem") or "").strip()
        if not stem:
            raise FetcherError(
                f"MockDocumentFetcher: identifier must include 'stem'; "
                f"got {identifier!r}"
            )
        fixture = self._find_fixture(stem)
        if fixture is None:
            raise FetcherNotFoundError(
                f"MockDocumentFetcher: no fixture {stem!r} at {self._fixture_root}"
            )

        # Hash computed from bytes on disk — consistent with real landers.
        import hashlib
        h = hashlib.sha256()
        with open(fixture, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)

        return FetchResult(
            pointer=FilePointer(value=str(fixture)),
            metadata={
                "lander_name": self.name,
                "lander_version": self.version,
                "stem": stem,
                "fixture_root": str(self._fixture_root),
                "bytes_written": fixture.stat().st_size,
                "deprecated": True,
            },
            hash=h.hexdigest(),
        )


__all__ = ["MockDocumentFetcher"]
