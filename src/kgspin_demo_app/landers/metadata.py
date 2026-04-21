"""Source-metadata helpers for Sprint 09 DocumentFetcher landers.

Sprint 07's ``write_metadata_sidecar`` is gone — admin's registry (via
``CorpusDocumentMetadata`` + ``register_corpus_document``) is the
authoritative metadata sink post-REQ-007. This module now provides:

- ``build_source_extras(...)`` — builds the ``dict[str, Any]`` that each
  lander attaches to ``FetchResult.metadata`` so the caller (CLI or
  admin's registry) has everything it needs to construct a
  ``CorpusDocumentMetadata``. Same keys the old sidecar held, minus
  ones the ``FetchResult`` / ``CorpusDocumentMetadata`` pydantic models
  already require separately (``bytes_written``, ``etag``, ``source_url``
  are first-class on ``CorpusDocumentMetadata``).

Backwards-compat: ``write_metadata_sidecar`` is retained as a
deprecated no-op that raises ``RuntimeError`` so stale callers fail
loud rather than silently writing to the wrong place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def iso_utc_now() -> str:
    """Current time in ISO-8601 UTC with millisecond precision."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_source_extras(
    *,
    lander_name: str,
    lander_version: str,
    fetch_timestamp_utc: str,
    http_status: int | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``dict`` the caller attaches to ``FetchResult.metadata``.

    Contract (Sprint 09):
    - ``lander_name`` + ``lander_version`` are always present so admin's
      registry knows which lander produced the artifact.
    - ``fetch_timestamp_utc`` is ISO-8601 UTC millisecond-precision.
    - ``http_status`` is present when the source is HTTP-backed
      (SEC, ClinicalTrials.gov, NewsAPI all are).
    - ``extra_fields`` is a per-source dict of extras. For SEC these
      include ``cik``, ``accession``, ``filing_type``. For NewsAPI they
      include ``source_name``, ``author``, ``url``.

    The returned dict is consumed by two downstream paths:
    1. The CLI's ``CorpusDocumentMetadata(source_extras=...)`` builder.
    2. Test helpers asserting lander behavior.

    It is **never** dumped to a sidecar file. Admin's registry is the
    authoritative sink.
    """
    out: dict[str, Any] = {
        "lander_name": lander_name,
        "lander_version": lander_version,
        "fetch_timestamp_utc": fetch_timestamp_utc,
    }
    if http_status is not None:
        out["http_status"] = http_status
    if extra_fields:
        out.update(extra_fields)
    return out


def write_metadata_sidecar(*args: Any, **kwargs: Any) -> None:
    """Deprecated Sprint 07 sidecar writer. Fails loud (VP Eng mandate).

    Sprint 09 removed the sidecar contract — admin's registry holds
    the metadata now. Any remaining caller is a stale import that
    should be migrated to pass the same data into
    ``FetchResult.metadata`` via ``build_source_extras`` instead.
    """
    raise RuntimeError(
        "write_metadata_sidecar() was removed in Sprint 09 (REQ-007). "
        "Metadata now flows to admin's registry via "
        "ResourceRegistryClient.register_corpus_document(metadata=CorpusDocumentMetadata(...)). "
        "Use landers.metadata.build_source_extras() to build the extras dict "
        "and CorpusDocumentMetadata(source_extras=..., bytes_written=..., "
        "etag=..., source_url=...) to build the registry record."
    )


__all__ = ["build_source_extras", "iso_utc_now", "write_metadata_sidecar"]
