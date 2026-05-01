"""Lander-side canonical-plaintext + manifest persistence.

PRD-004 v5 Phase 5B (commit 2 / D2). After a lander writes its raw
artifact (``raw.html`` for SEC, ``raw.json`` for ClinicalTrials), this
module produces two side files in the same directory:

- ``source.txt`` — canonical plaintext, generated via
  ``kgspin_interface.text.normalize.canonical_plaintext_from_html`` /
  ``canonical_plaintext_from_clinical_json``. Single producer of the
  byte-stable plaintext that the rag-corpus builder + scenario runners
  + lineage UI all read against.
- ``manifest.json`` — small JSON record pinning the canonicalization
  inputs/outputs (raw SHA, plaintext SHA, normalization_version,
  lander identity, fetch timestamp). Downstream consumers (notably
  the joinable two-store cache builder) use this to detect
  source-doc drift without re-canonicalizing.

This module is the only place inside ``kgspin_demo_app/landers/`` that
imports ``kgspin_interface``. ``_shared.py`` is intentionally
protocol-adjacent (no kgspin_interface dep); see its module docstring.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


MANIFEST_SCHEMA_VERSION = "1"
MANIFEST_FILENAME = "manifest.json"
SOURCE_TEXT_FILENAME = "source.txt"


@dataclass(frozen=True)
class CanonicalArtifacts:
    """Paths + summary returned to the lander after canonicalization."""

    source_text_path: Path
    manifest_path: Path
    plaintext_sha: str
    plaintext_bytes: int
    normalization_version: str
    # Only populated for ``kind="clinical_json"`` — the JSON canonicalizer
    # also extracts the lead-sponsor name (the lander wires this into
    # extraction's H-module ``main_entity``).
    sponsor: str | None = None


def write_canonical_artifacts(
    *,
    raw_path: Path,
    raw_bytes: bytes,
    raw_sha: str,
    kind: Literal["html", "clinical_json"],
    domain: str,
    source: str,
    lander_name: str,
    lander_version: str,
    fetch_timestamp_utc: str,
) -> CanonicalArtifacts:
    """Write ``source.txt`` + ``manifest.json`` next to the raw artifact.

    Returns a :class:`CanonicalArtifacts` summary the lander can fold
    into its ``FetchResult.metadata`` extras (so admin's registry sees
    the plaintext_sha + normalization_version on the same
    ``corpus_document`` row as the raw_sha — see D3).

    For ``kind="html"``: the bytes are decoded as UTF-8 (best-effort,
    ``errors="ignore"``) before stripping. SEC HTML is well-formed UTF-8
    in practice; the ``ignore`` is defense-in-depth.

    For ``kind="clinical_json"``: the bytes are decoded as UTF-8 strict.
    ClinicalTrials.gov v2 returns valid UTF-8 JSON.
    """
    from kgspin_interface.text.normalize import (
        NORMALIZATION_VERSION,
        canonical_plaintext_from_clinical_json,
        canonical_plaintext_from_html,
    )

    sponsor: str | None = None
    if kind == "html":
        plaintext, plaintext_sha = canonical_plaintext_from_html(
            raw_bytes.decode("utf-8", errors="ignore"),
        )
    elif kind == "clinical_json":
        plaintext, plaintext_sha, sponsor = canonical_plaintext_from_clinical_json(
            raw_bytes.decode("utf-8"),
        )
    else:  # pragma: no cover — Literal type guards this
        raise ValueError(f"Unknown canonicalizer kind: {kind!r}")

    parent = raw_path.parent
    source_text_path = parent / SOURCE_TEXT_FILENAME
    manifest_path = parent / MANIFEST_FILENAME

    source_text_path.write_text(plaintext, encoding="utf-8")
    plaintext_bytes = source_text_path.stat().st_size

    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "domain": domain,
        "source": source,
        "raw": {
            "filename": raw_path.name,
            "sha256": raw_sha,
            "bytes": len(raw_bytes),
        },
        "source_text": {
            "filename": SOURCE_TEXT_FILENAME,
            "sha256": plaintext_sha,
            "bytes": plaintext_bytes,
            "normalization_version": NORMALIZATION_VERSION,
        },
        "lander": {
            "name": lander_name,
            "version": lander_version,
        },
        "fetched_at": fetch_timestamp_utc,
    }
    if sponsor is not None:
        manifest["clinical"] = {"sponsor": sponsor}

    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return CanonicalArtifacts(
        source_text_path=source_text_path,
        manifest_path=manifest_path,
        plaintext_sha=plaintext_sha,
        plaintext_bytes=plaintext_bytes,
        normalization_version=NORMALIZATION_VERSION,
        sponsor=sponsor,
    )


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of in-memory bytes (helper for tests + callers
    that already have the raw bytes in hand and don't want to re-read disk)."""
    return hashlib.sha256(data).hexdigest()
