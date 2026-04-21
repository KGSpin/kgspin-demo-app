"""Derive a term-scoped news query from a clinical trial's registered metadata.

Sprint 11 VP Prod requirement (plan Task 6): when the operator clicks
"Refresh All Clinical News" for an NCT trial, derive the NewsAPI
query from the trial's ``source_extras.condition`` + top intervention
names instead of asking the operator to type a freeform query.

Sprint 12 Task 10.1 (VP Eng Phase 3 directive): extracted from
``demos/extraction/demo_compare.py`` into its own module so it can
grow without turning that 8k-line file into a God Method hotspot.
Signature + behavior unchanged — contract-compatible with every
caller (``demo_compare.py`` currently calls this in 2 places).

The module is a pure function over a ``ResourceRegistryClient``:

- Takes an NCT id + an optional default fallback string.
- Reads the most-recent clinical ``corpus_document`` for that NCT.
- Extracts ``condition`` + top-2 intervention names from
  ``source_extras``.
- Sanitizes the result to match the demo's ``_NEWS_QUERY_RE``
  (alphanumerics / spaces / ``_-`` only, ≤ 100 chars).

If the registry read fails or the trial isn't registered, returns
the ``default`` arg. Callers decide what "empty default" means —
in demo the fan-out endpoint falls back to the NCT id itself,
while the drill-down endpoint returns a 400 asking the operator
to type the query.
"""

from __future__ import annotations

import logging
import re

from kgspin_interface.registry_client import (
    ResourceKind,
    ResourceRegistryClient,
)


logger = logging.getLogger(__name__)


_QUERY_SANITIZE_RE = re.compile(r"[^A-Za-z0-9 _\-]")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_QUERY_CHARS = 100
_MAX_INTERVENTIONS = 2


def derive_clinical_query_from_nct(
    client: ResourceRegistryClient,
    nct: str,
    *,
    default: str = "",
) -> str:
    """Return a sanitized term-scoped query string for ``nct``.

    ``client`` is the admin ``ResourceRegistryClient`` (any Protocol-
    conforming implementation — real HTTP, fake, or in-process).
    Passing the client in (rather than fetching it via a module-level
    global) keeps this function trivially testable without
    monkey-patching.

    Returns ``default`` on any failure path: registry unreachable,
    trial not registered, metadata missing ``condition`` +
    ``interventions``, or derived string empty after sanitization.
    """
    try:
        resources = client.list(
            ResourceKind.CORPUS_DOCUMENT,
            domain="clinical",
            source="clinicaltrials_gov",
        )
    except Exception:  # noqa: BLE001 — graceful-degrade on any client error
        logger.debug("derive_clinical_query: registry unreachable for %r", nct)
        return default

    matches = [
        r for r in resources
        if (r.metadata or {}).get("identifier", {}).get("nct") == nct
    ]
    if not matches:
        return default
    matches.sort(
        key=lambda r: (r.metadata or {}).get("fetch_timestamp", ""),
        reverse=True,
    )
    extras = ((matches[0].metadata or {}).get("source_extras") or {})
    parts: list[str] = []
    condition = (extras.get("condition") or "").strip()
    if condition:
        parts.append(condition)
    parts.extend(_parse_interventions(extras.get("interventions"))[:_MAX_INTERVENTIONS])

    safe = _sanitize_query(" ".join(parts))
    return safe or default


def _parse_interventions(raw: object) -> list[str]:
    """Normalize Clinical-Trials ``interventions`` into a list of names.

    The field ships as either a comma-separated string or a
    JSON-serialized list depending on which version of the clinical
    lander registered the trial. Both shapes flow through this
    normalizer.
    """
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(s).strip() for s in raw if str(s).strip()]
    return []


def _sanitize_query(raw: str) -> str:
    """Strip chars outside ``_NEWS_QUERY_RE`` + collapse whitespace.

    Truncates to ``_MAX_QUERY_CHARS``. Mirrors the sanitization rule
    baked into demo_compare's ``_NEWS_QUERY_RE`` so the returned
    string is directly usable as a ``--query`` value without further
    validation.
    """
    cleaned = _QUERY_SANITIZE_RE.sub(" ", raw).strip()
    return _WHITESPACE_RE.sub(" ", cleaned)[:_MAX_QUERY_CHARS]
