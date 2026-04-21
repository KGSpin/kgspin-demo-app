"""Shared utilities for all 4 Sprint 09 DocumentFetcher landers.

Centralizes:
- ``KGSPIN_CORPUS_ROOT`` resolution (env var → default)
- ``default_artifact_path`` — demo-internal on-disk convention (not a
  cross-repo contract). Previous name ``provisional_artifact_path``
  was tied to the retired ``FileStoreLayout`` stand-in; REQ-004
  removed that interface, so the name is repurposed as a sane
  per-source path helper.
- Required-env-var enforcement (fail-fast with structured stderr)
- Logging setup for CLI entry points
- Streaming HTTP download with size cap (uses ``_net_safety``)
- File-content SHA-256 (``sha256_file``)

Every helper here is Protocol-adjacent: it does NOT import
``kgspin_interface`` and does NOT participate in the Resource/Pointer
contract. That's Task 3/4's job (the DocumentFetcher subclasses).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ._net_safety import MAX_ARTIFACT_BYTES, STREAM_CHUNK_BYTES, DownloadTooLargeError
from ._path_safety import SecurityError, resolve_under_root, sanitize_component


DEFAULT_CORPUS_ROOT = Path.home() / ".kgspin" / "corpus"

# Per core team's sharpening: validate YYYY-MM-DD in the lander BEFORE
# calling FileStoreLayout.artifact_path so the error surface is the
# lander, not the path helper (which would raise ValueError deep inside
# a call stack with poor provenance).
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get_corpus_root() -> Path:
    """Resolve ``$KGSPIN_CORPUS_ROOT`` with the default fallback.

    Creates the directory if missing. The returned path is guaranteed
    to be an absolute, existing directory.

    Sprint 09 VP Sec: parent directory tree is created with mode=0o700
    so user-only restricts directory traversal even when the operator's
    umask is permissive. Files inherit umask; the path restriction
    prevents sibling processes from listing / traversing.
    """
    raw = os.environ.get("KGSPIN_CORPUS_ROOT", "").strip()
    root = Path(raw).expanduser().resolve() if raw else DEFAULT_CORPUS_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # If root already existed with different mode, tighten it.
    try:
        root.chmod(0o700)
    except PermissionError:
        pass  # not our dir to tighten; caller accepts umask-default
    return root


def validate_date(date_str: str) -> str:
    """Validate a date string matches the ``YYYY-MM-DD`` form.

    Returns the validated string unchanged. Raises ``ValueError`` with
    a clear provenance message on mismatch — the caller is expected to
    invoke this in the lander's main entry point BEFORE any path
    construction, so the error surfaces with full CLI context.
    """
    if not _ISO_DATE_RE.fullmatch(date_str or ""):
        raise ValueError(
            f"Expected date in YYYY-MM-DD format, got {date_str!r}. "
            f"Provide --date with ISO-8601 form (e.g. '2026-04-15')."
        )
    return date_str


def today_iso_utc() -> str:
    """Today's date in UTC as YYYY-MM-DD. Default for lander --date flag."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def default_artifact_path(
    corpus_root: Path,
    *,
    domain: str,
    source: str,
    identifier: str,
    date: str,
    artifact_type: str,
    filename: str,
) -> Path:
    """Return the on-disk path for a landed artifact.

    Demo-internal convention (not a cross-repo contract post-REQ-004):

        <corpus_root>/<domain>/<source>/<identifier>/<date>/<artifact_type>/<filename>

    All components are sanitized via ``_path_safety.sanitize_component``
    and the final resolved path is verified to live under ``corpus_root``
    (rejects traversal attempts).

    Previously named ``provisional_artifact_path`` back when it was a
    stand-in for ``kgspin_interface.landers.FileStoreLayout``. REQ-004
    removed that interface entirely; the function is now the canonical
    per-lander path builder, not a stand-in for anything.
    """
    validate_date(date)
    path = resolve_under_root(
        corpus_root, domain, source, identifier, date, artifact_type, filename,
    )
    # VP Sec: ensure each parent directory tightens to 0o700 when created.
    # Walk up from the file's parent to the corpus root.
    for parent in list(path.parents):
        if parent == corpus_root or corpus_root in parent.parents:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                parent.chmod(0o700)
            except PermissionError:
                pass
        if parent == corpus_root:
            break
    return path


# Backwards-compat alias — landers may still import the old name until
# Sprint 09 Task 6 finishes the corpus/ cleanup. New code uses
# ``default_artifact_path``.
provisional_artifact_path = default_artifact_path


def require_env_var(name: str, *, hint: str = "") -> str:
    """Fetch ``$name`` or fail-fast with non-zero exit + structured stderr.

    VP Eng mandate: no dry-run fallback. If a required env var is
    missing, the lander exits (status 2) with a message that includes
    the var name + an actionable hint.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        msg = (
            f"[LANDER_CONFIG] Required environment variable {name!r} is not set."
        )
        if hint:
            msg += f"\n  Hint: {hint}"
        sys.stderr.write(msg + "\n")
        sys.exit(2)
    return value


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file's raw bytes.

    Note: ``kgspin_interface.hashing.sha256_hex`` was suggested by the
    core team but serializes through canonical JSON first (per
    ADR-036) — that helper is for hashing JSON-serializable metadata,
    not raw file content. We use stdlib ``hashlib`` for the file bytes
    and flag the discrepancy in Sprint 07's dev report so the core team
    can clarify the intended helper for content hashing.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def stream_to_file(
    body_iter: Iterator[bytes],
    dest: Path,
    *,
    source_url: str,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> int:
    """Stream ``body_iter`` into ``dest``, enforcing ``max_bytes``.

    Aborts (raises ``DownloadTooLargeError``) without writing a partial
    file to disk if the total exceeds ``max_bytes``. Returns the number
    of bytes written on success.

    The implementation writes to ``dest.with_suffix(dest.suffix + ".part")``
    first, then atomic-renames on success. Aborts delete the partial
    file. This keeps the file store free of truncated artifacts.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    total = 0
    try:
        with open(part, "wb") as out:
            for chunk in body_iter:
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadTooLargeError(source_url, max_bytes)
                out.write(chunk)
        part.replace(dest)
        return total
    except BaseException:
        # Any failure mid-download → clean up the partial file.
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        raise


def setup_logging(lander_name: str, *, verbose: bool = False) -> logging.Logger:
    """Configure stderr-only logging for a lander CLI.

    stdout is reserved for structured status output (e.g., the final
    artifact path); stderr carries logs so downstream consumers can
    parse stdout without log-line noise.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    return logging.getLogger(lander_name)


__all__ = [
    "DEFAULT_CORPUS_ROOT",
    "DownloadTooLargeError",
    "MAX_ARTIFACT_BYTES",
    "SecurityError",
    "STREAM_CHUNK_BYTES",
    "default_artifact_path",
    "get_corpus_root",
    "provisional_artifact_path",  # deprecated alias for default_artifact_path
    "require_env_var",
    "sha256_file",
    "setup_logging",
    "stream_to_file",
    "today_iso_utc",
    "validate_date",
]
