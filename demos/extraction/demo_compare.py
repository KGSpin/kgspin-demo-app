#!/usr/bin/env python3
"""
KGSpin vs Gemini LLM Comparison Demo.

Web-based side-by-side comparison of semantic fingerprinting (KGSpin)
vs pure LLM (Gemini) for knowledge graph extraction from SEC filings.

Usage:
    uv run python demos/extraction/demo_compare.py
    # Open http://localhost:8080

Requires:
    pip install -e ".[demo,gliner,gemini]"
    export GEMINI_API_KEY="your-key"
    export EDGAR_IDENTITY="Your Name email@domain.com"
"""

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # demos/extraction for gemini_extractor

# Sprint 33.7: Demo debug logger — persistent file log for post-mortem analysis
logging.basicConfig(level=logging.INFO)

_demo_log_path = Path(__file__).parent / "demo_debug.log"
_file_handler = logging.FileHandler(_demo_log_path, mode="w")  # Clear on startup
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(name)s %(levelname)s %(message)s"
))
# kgenskills namespace at DEBUG (captures [FINAL_PURGE], [GLIREL_TRUNCATION], etc.)
logging.getLogger("kgenskills").addHandler(_file_handler)
logging.getLogger("kgenskills").setLevel(logging.DEBUG)
# Root logger at WARNING (captures throttle errors from google, requests, etc.)
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)
logger.info(f"Demo debug log: {_demo_log_path}")

# Import shared pipeline logic
from pipeline_common import (  # noqa: E402
    KNOWN_TICKERS,
    BUNDLE_PATH,
    PATTERNS_PATH,
    DATA_LAKE_ROOT,
    OUTPUT_ROOT,
    DOMAIN_BUNDLES_DIR,
    DOMAIN_YAMLS_DIR,
    list_bundles,
    list_bundle_options,
    list_available_pipelines,
    resolve_bundle_path,
    resolve_domain_bundle_path,
    resolve_domain_yaml_path,
    resolve_ticker,
    html_to_text,
    strip_ixbrl,
    select_content_chunks,
)

# Sprint 90: Filter utility — ensures analysis/scores use same clean data as graph
# INIT-001 Sprint 01: vendored locally per ADR-001 (docs/architecture/decisions/ADR-001-kg-filters-placement.md)
from kgspin_demo_app.utils.kg_filters import filter_kg_for_display, compute_schema_compliance  # noqa: E402

# INIT-001 Sprint 03: CorpusProvider registrations via side-effect imports.
# Both calls must happen at module-load time so the providers are available
# when the first /api/refresh-kgen request comes in.
#
#   kgspin_plugin_financial  → registers "edgar" (requires SEC_USER_AGENT
#                               env var at construction; caller handles
#                               graceful fallthrough in _try_corpus_fetch)
#   kgspin_demo_app.corpus       → registers "demo_mock" (fixture loader)
#
# See ADR-002 for the cut-over plan and the mock's permanent CI role.
from kgspin_demo_app.corpus import CorpusFetchError, ProviderConfigurationError  # noqa: E402
# Sprint 10: admin's ResourceRegistryClient is the canonical read path
# for landed corpus documents. filestore_reader.py was retired; landers
# still write to FileStoreLayout but the registry is the single index.
from kgspin_demo_app.registry_http import HttpResourceRegistryClient  # noqa: E402
from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS  # noqa: E402
from kgspin_interface import (  # noqa: E402
    ResourceMetadataValidationError,
    typed_metadata,
)
from kgspin_interface.registry_client import (  # noqa: E402
    Resource,
    ResourceKind,
)
from kgspin_interface.resources import FilePointer  # noqa: E402

logger.info(
    "Sprint 10 mode: extraction reads corpus documents from admin's "
    "ResourceRegistryClient (KGSPIN_ADMIN_URL, default http://127.0.0.1:8750). "
    "Use `uv run kgspin-demo-lander-*` CLIs or the Refresh Local Corpus UI button to populate it."
)


# --- Sprint 10 Task 4: post-subprocess registry-poll tuning constants ---
# [VP Eng MAJOR-2] Named module-level constants, not magic numbers. Task 8
# measures end-to-end post-subprocess registry latency; if any sample
# exceeds 3.0s, _POST_LANDER_POLL_MAX_SEC is bumped to 10.0 in the same
# PR before the sprint is marked Done (Definition of Done checkbox).
_POST_LANDER_POLL_INTERVAL_SEC = 0.5
_POST_LANDER_POLL_MAX_SEC = 5.0
_POST_LANDER_POLL_REGISTERED_WINDOW_SEC = 60.0


@functools.lru_cache(maxsize=1)
def _get_registry_client() -> HttpResourceRegistryClient:
    """Lazy module-level registry client.

    One ``httpx.Client`` connection is reused across all extraction
    requests. The FastAPI ``shutdown`` hook (below) closes it on
    process exit. Tests monkeypatch this function to inject a
    ``FakeRegistryClient``.
    """
    return HttpResourceRegistryClient()


# Per-process warning flags so we don't spam the log on every fetch.
_edgar_provider_unavailable_logged = False
_corpus_provider_cache: dict = {}

# Sprint 06 Task 2: simple in-process TTL cache for news discovery results.
# The plugin provider has its own per-article cache; this caches the
# top-level "give me articles about ticker X" query so a second
# /api/intelligence/{ticker} call within TTL doesn't re-hit newsapi.org.
# 15 minutes = 900s; well under the 100/day rate limit budget.
_NEWS_DISCOVERY_TTL_SEC = 900
_news_discovery_cache: dict = {}  # key: (ticker, days) → (timestamp, [articles])


def _read_pointer_bytes(pointer) -> bytes:
    """Read raw bytes backing a ``FilePointer``.

    CORPUS_DOCUMENT resources in this demo always resolve to local
    ``FilePointer`` values (landers write to ``FileStoreLayout`` then
    register the absolute path). Any other pointer scheme is a
    programmer error in this read path (Sprint 10 Task 1 ACK).
    """
    if not isinstance(pointer, FilePointer):
        raise CorpusFetchError(
            doc_id="<unknown>",
            reason="unexpected pointer scheme",
            actionable_hint=(
                f"Corpus-document pointer is {type(pointer).__name__}; "
                f"demo read path only handles FilePointer. "
                f"Check kgspin-admin registry entry."
            ),
            attempted=[type(pointer).__name__],
        )
    return Path(pointer.value).read_bytes()


def _adapt_to_sec_doc_shape(raw_bytes: bytes, metadata: dict, ticker: str):
    """Adapt registry-backed corpus bytes + metadata into the EdgarDocument-
    shaped namespace the demo's downstream ``_parse_and_chunk`` + cache
    code expects. Extracted from the former ``_corpus_doc_to_sec_shape``
    so Task 2's news reader can share the decoding convention.

    Defect 1 (2026-04-24): `company_name` resolution now prefers the
    post-Wave-A SEC lander keys (`company_name_as_filed` at top of
    `source_extras`, then nested `company.canonical_name`). The legacy
    `company_name` key stays in the fallback chain for older cached
    documents. The ticker-echo only fires when every source-of-truth
    key is absent.
    """
    from types import SimpleNamespace
    identifier = metadata.get("identifier") or {}
    source_extras = metadata.get("source_extras") or {}
    company_extras = (source_extras.get("company") or {}) if isinstance(
        source_extras.get("company"), dict
    ) else {}
    text = raw_bytes.decode("utf-8", errors="ignore")
    resolved_company_name = (
        source_extras.get("company_name_as_filed")
        or company_extras.get("canonical_name")
        or source_extras.get("company_name")        # legacy pre-Wave-A cache
        or identifier.get("ticker")
        or ticker
    )
    return SimpleNamespace(
        raw_html=text,
        company_name=resolved_company_name,
        cik=source_extras.get("cik", "") or company_extras.get("cik", ""),
        accession_number=source_extras.get("accession_number", "")
            or source_extras.get("accession", ""),
        filing_date=source_extras.get("filing_date", ""),
        fiscal_year_end=source_extras.get("fiscal_year_end", ""),
        source_path=(metadata.get("source_url") or ""),
        loaded_from_cache=True,
    )


_NEWS_SOURCES_BY_DOMAIN = {
    "financial": ("marketaux", "yahoo_rss", "newsapi"),
    "clinical": ("newsapi",),
}


def _fetch_newsapi_articles(query: str, limit: int = 5) -> list[dict]:
    """Sprint 11 (ADR-004): read landed news from all backend-named sources.

    Post-rename the Sprint 09 sources (``newsapi_financial`` /
    ``newsapi_health``) are replaced by the backend-named landers per
    ``DOMAIN_FETCHERS``. This reader enumerates each news source listed
    in ``_NEWS_SOURCES_BY_DOMAIN`` for both financial (ticker-scoped) and
    clinical (query-substring) paths, merges hits, and returns the
    unified list — the Sprint 09 registry-read contract is preserved.

    Returns the same dict shape as the Sprint 06 live client:
        {title, source_name, url, published_at, text}.

    Raises ``ProviderConfigurationError`` when zero articles match —
    the SSE handler turns this into a "Corpus Missing: click Refresh"
    hint. Hint text now lists the Sprint 11 CLI names.
    """
    client = _get_registry_client()
    results: list[dict] = []

    def _append_hit(resource: Resource, raw_bytes: bytes) -> None:
        meta = resource.metadata or {}
        results.append({
            "title": "",
            "source_name": "",
            "url": meta.get("source_url", ""),
            "published_at": "",
            "text": raw_bytes.decode("utf-8", errors="ignore"),
        })

    # Financial path: ticker-shaped identifier; try each configured
    # financial news source in order.
    for source in _NEWS_SOURCES_BY_DOMAIN["financial"]:
        resources = client.list(
            ResourceKind.CORPUS_DOCUMENT, domain="financial", source=source,
        )
        ticker_matches = [
            r for r in resources
            if (r.metadata or {}).get("identifier", {}).get("ticker") == query.upper()
        ]
        ticker_matches.sort(
            key=lambda r: (r.metadata or {}).get("fetch_timestamp", ""),
            reverse=True,
        )
        for resource in ticker_matches[:limit - len(results)]:
            pointer = client.resolve_pointer(resource.id)
            _append_hit(resource, _read_pointer_bytes(pointer))
        if len(results) >= limit:
            break

    # Clinical path (only if financial didn't cover): substring match on
    # source_extras.query across all clinical news sources.
    if not results:
        q_lower = query.lower()
        for source in _NEWS_SOURCES_BY_DOMAIN["clinical"]:
            resources = client.list(
                ResourceKind.CORPUS_DOCUMENT, domain="clinical", source=source,
            )
            query_matches: list[Resource] = []
            for resource in resources:
                # Wave A: typed_metadata at the consumption boundary. The
                # helper validates the CorpusDocumentMetadata envelope on
                # read (catches cross-repo schema skew) — we still reach
                # into `source_extras` as a loose dict per CTO Q6.
                try:
                    md = typed_metadata(resource)
                except ResourceMetadataValidationError as exc:
                    logger.warning(
                        "skipping resource %s: metadata failed validation: %s",
                        resource.id, exc,
                    )
                    continue
                stored_q = ((md.source_extras or {}).get("query") or "").lower()
                if stored_q and (stored_q in q_lower or q_lower in stored_q):
                    query_matches.append(resource)
            query_matches.sort(
                key=lambda r: (typed_metadata(r).fetch_timestamp.isoformat()
                               if r.metadata else ""),
                reverse=True,
            )
            for resource in query_matches[:limit - len(results)]:
                pointer = client.resolve_pointer(resource.id)
                _append_hit(resource, _read_pointer_bytes(pointer))
            if len(results) >= limit:
                break

    if not results:
        raise ProviderConfigurationError(
            provider_id="registry_news",
            missing_var="landed news",
            hint=(
                f"No landed news articles match {query!r}. Run one of:\n"
                f"  uv run kgspin-demo-lander-marketaux --ticker {query}\n"
                f"  uv run kgspin-demo-lander-yahoo-rss --ticker {query}\n"
                f"  uv run kgspin-demo-lander-newsapi --query {query!r}\n"
                f"...then retry. Or click 'Refresh All News' in the UI."
            ),
        )
    logger.info(
        "[REGISTRY_NEWS] query=%r matched=%d articles via admin registry",
        query, len(results),
    )
    return results


def _auto_land_corpus(
    *, ticker: str, domain: str, source: str, identifier_key: str,
    normalized_id: str, is_clinical: bool,
):
    """In-process lander invocation: fetch + register when admin has no
    artifact. Returns the ``FetchResult``-like pointer/metadata pair on
    success, or raises to let the caller surface the error.

    Mirrors the SecLander / ClinicalLander CLI entry points in their
    ``main()`` functions but skips the subprocess boundary so the demo
    can self-bootstrap on first Run. Requires ``EDGAR_IDENTITY`` for
    the SEC path (the lander raises ``FetcherError`` on missing creds,
    and that propagates with an actionable message).
    """
    from datetime import datetime as _dt
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient
    from kgspin_interface.resources import CorpusDocumentMetadata

    if is_clinical:
        from kgspin_demo_app.landers.clinical import ClinicalLander
        lander = ClinicalLander()
        register_identifier: dict[str, str] = {"nct": normalized_id}
        result = lander.fetch(nct=normalized_id)
    else:
        from kgspin_demo_app.landers.sec import SecLander
        lander = SecLander()
        register_identifier = {"ticker": normalized_id, "form": "10-K"}
        result = lander.fetch(ticker=normalized_id, form="10-K")

    extras = result.metadata or {}
    doc_meta = CorpusDocumentMetadata(
        domain=domain,
        source=source,
        identifier=register_identifier,
        fetch_timestamp=_dt.fromisoformat(
            extras.get("fetch_timestamp_utc", _dt.utcnow().isoformat() + "Z")
            .replace("Z", "+00:00")
        ),
        mime_type=extras.get("mime_type", "text/html"),
        bytes_written=extras.get("bytes_written"),
        etag=extras.get("etag"),
        source_url=extras.get("source_url"),
        source_extras={
            k: v for k, v in extras.items()
            if k not in {"bytes_written", "etag", "source_url", "mime_type"}
        },
    )

    client = HttpResourceRegistryClient()
    client.register_corpus_document(
        metadata=doc_meta, pointer=result.pointer,
        actor=f"auto_land:{source}",
    )
    return doc_meta, result.pointer


def _try_corpus_fetch(ticker: str):
    """Resolve a landed corpus artifact for ``ticker`` via admin.

    On admin miss, auto-invoke the appropriate lander (SEC for tickers,
    ClinicalLander for NCT ids), register the fetched artifact, and
    retry. This makes first-run extraction self-bootstrapping — the
    operator doesn't need a separate "Refresh Local Corpus" click.

    Raises ``CorpusFetchError`` only when both the admin lookup AND the
    auto-land path fail. The error carries the real upstream reason
    (e.g. ``EDGAR_IDENTITY not set``) rather than a generic not-found
    message so the UI can surface a useful failure state.
    """
    is_clinical = ticker.startswith("NCT")
    if is_clinical:
        domain, source, identifier_key = "clinical", "clinicaltrials_gov", "nct"
        normalized_id = ticker
    else:
        domain, source, identifier_key = "financial", "sec_edgar", "ticker"
        normalized_id = ticker.upper()
    attempted = [source]

    def _read_from_admin() -> Any:
        client = _get_registry_client()
        candidates = client.list(
            ResourceKind.CORPUS_DOCUMENT, domain=domain, source=source,
        )
        matches = [
            r for r in candidates
            if (r.metadata or {}).get("identifier", {}).get(identifier_key) == normalized_id
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda r: (r.metadata or {}).get("fetch_timestamp", ""),
            reverse=True,
        )
        latest = matches[0]
        pointer = client.resolve_pointer(latest.id)
        raw_bytes = _read_pointer_bytes(pointer)
        return _adapt_to_sec_doc_shape(raw_bytes, latest.metadata or {}, ticker)

    # 1. Existing artifact in admin?
    try:
        existing = _read_from_admin()
        if existing is not None:
            return existing
    except CorpusFetchError as cfe:
        # Re-raise with ticker on the envelope.
        raise CorpusFetchError(
            doc_id=ticker, reason=cfe.reason,
            actionable_hint=cfe.actionable_hint,
            attempted=attempted + cfe.attempted,
        )

    # 2. Auto-land: invoke the real lander in-process.
    logger.info(
        "[AUTO_LAND] admin has no %s/%s artifact for %s — running lander in-process",
        domain, source, ticker,
    )
    try:
        _auto_land_corpus(
            ticker=ticker, domain=domain, source=source,
            identifier_key=identifier_key, normalized_id=normalized_id,
            is_clinical=is_clinical,
        )
    except Exception as exc:
        logger.warning("[AUTO_LAND] failed for %s: %s", ticker, exc)
        auto_land_error = str(exc)
        auto_land_class = type(exc).__name__
    else:
        # 2b. Post-land retry.
        try:
            refreshed = _read_from_admin()
            if refreshed is not None:
                logger.info("[AUTO_LAND] registered + loaded %s", ticker)
                return refreshed
        except CorpusFetchError as cfe:
            raise CorpusFetchError(
                doc_id=ticker, reason=cfe.reason,
                actionable_hint=cfe.actionable_hint,
                attempted=attempted + cfe.attempted,
            )
        auto_land_error = "post-land lookup found no artifact"
        auto_land_class = "MissingArtifact"

    # 3. Offline fixture fallback (tests/fixtures/corpus/<ticker>.html).
    attempted.append("demo_mock_fixture")
    try:
        from kgspin_demo_app.corpus.mock_provider import MockDocumentFetcher
        fetcher = _corpus_provider_cache.get("demo_mock") or MockDocumentFetcher()
        _corpus_provider_cache["demo_mock"] = fetcher
        result = fetcher.fetch(domain=domain, source=source, identifier={"stem": ticker})
        fixture_bytes = Path(result.pointer.value).read_bytes()
        from types import SimpleNamespace
        return SimpleNamespace(
            raw_html=fixture_bytes.decode("utf-8", errors="ignore"),
            company_name=ticker,
            cik="",
            accession_number="",
            filing_date="",
            fiscal_year_end="",
            source_path=result.pointer.value,
            loaded_from_cache=True,
        )
    except Exception:
        pass

    # 4. Everything failed. Surface the real auto-land error, not a generic hint.
    if is_clinical:
        hint = (
            f"Could not fetch {ticker} from ClinicalTrials.gov.\n"
            f"Auto-land attempt raised {auto_land_class}: {auto_land_error}"
        )
    else:
        hint = (
            f"Could not fetch {ticker} from SEC EDGAR.\n"
            f"Auto-land attempt raised {auto_land_class}: {auto_land_error}\n"
            f"If the error mentions EDGAR_IDENTITY: set the env var to an "
            f"email (e.g. EDGAR_IDENTITY='you@example.com') and restart the demo."
        )
    raise CorpusFetchError(
        doc_id=ticker,
        reason="no landed artifact",
        actionable_hint=hint,
        attempted=attempted,
    )


# Keep the old name as an alias so any stale callers don't break.
_try_mock_fetch = _try_corpus_fetch


def _classify_llm_error(exc: Exception) -> tuple[str, str]:
    """Return ``(reason, error_type)`` for a backend exception.

    ``reason`` is a short machine-readable tag the UI checks to pick an
    icon / copy ("context_exceeded" triggers the red "Failed to generate"
    overlay with an explanatory note about model limits). ``error_type``
    is a broader category for the analysis agent's judgement: callers
    can tell "context limits" apart from "quota" apart from "network".
    """
    msg = str(exc).lower()
    if "input token count exceeds" in msg or "context length" in msg:
        return "context_exceeded", "context_exceeded"
    if "quota" in msg or "resource_exhausted" in msg or "429" in msg:
        return "quota_exceeded", "quota_exceeded"
    if "max_tokens" in msg or "max_output_tokens" in msg:
        return "output_truncated", "output_truncated"
    if "safety" in msg or "block" in msg:
        return "safety_block", "safety_block"
    if "timeout" in msg or "deadline" in msg or "network" in msg:
        return "backend_unreachable", "network_error"
    return "extraction_failed", "unknown_error"

# Sprint 33.3: Cache invalidation — bump when UI/schema changes break compatibility.
# Changing this constant auto-invalidates all existing cached run logs.
DEMO_CACHE_VERSION = "5.0.0"  # 4.0.0: Sprint 118 pipeline/domain split

# Sprint 33.3: Byte-based corpus sizing — replaces MAX_DEMO_CHUNKS
VALID_CORPUS_KB = {0, 100, 200, 500}
DEFAULT_CORPUS_KB = 0  # Sprint 80: Full document is default (CEO directive)

# Sprint 141: Chunk size is now max chars per chunk (size-based, not count-based).
# Default 30K chars (~8K tokens). Legacy count values (1, 6, 12, 24) mapped to 30K.
VALID_CHUNK_SIZES = {3000, 10000, 30000, 50000, 0, 1, 6, 12, 24}
DEFAULT_CHUNK_SIZE = 30000

# Model selection with pricing (per 1M tokens).
# Wave 3 follow-up: Gemini 3.x preview models added 2026-04-22. All 3.x
# variants are Preview (no GA tier yet); pricing is not published on the
# aistudio pricing page, so the dollar figures are placeholders marked
# with "preview_pricing: true". Surface them in the UI as "(preview)"
# so operators understand the model tier — and rotate GA pricing in when
# Google publishes it.
GEMINI_MODEL_PRICING = {
    "gemini-2.5-flash-lite":        {"input": 0.10, "output": 0.40, "label": "2.5 Flash Lite"},
    "gemini-2.5-flash":             {"input": 0.30, "output": 2.50, "label": "2.5 Flash"},
    # Gemini 3.x — Preview, pricing TBD. See
    # https://ai.google.dev/gemini-api/docs/changelog (Mar/Apr 2026 entries).
    "gemini-3-flash-preview":       {"input": 0.30, "output": 2.50, "label": "3 Flash (preview)",       "preview_pricing": True},
    "gemini-3.1-flash-lite-preview": {"input": 0.10, "output": 0.40, "label": "3.1 Flash Lite (preview)", "preview_pricing": True},
    "gemini-3.1-pro-preview":       {"input": 1.25, "output": 10.00, "label": "3.1 Pro (preview)",      "preview_pricing": True},
}
VALID_GEMINI_MODELS = set(GEMINI_MODEL_PRICING.keys())
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Sprint 33.6: Cancel events for Multi-Stage extraction
_modular_cancel_events: dict = {}  # ticker -> threading.Event

# Sprint 33.6: KGSpin CPU cost estimation (AWS m6i.large spot pricing baseline)
_CPU_COST_PER_HOUR = 0.05

# --- INIT-001 Sprint 04: UI slot config (config-driven compare dropdowns) ---
#
# Single source of truth for which pipelines appear in the compare-UI
# dropdowns. See ui_slots.yaml (next to this file) for schema docs.
# Validated at module-import time so misconfig is caught at boot.

# Sprint 12: the yaml-loader validation constants (_VALID_SLOT_BACKENDS,
# _VALID_SLOT_CAPABILITIES, _REQUIRED_SLOT_FIELDS) moved into admin's
# pipeline_config registration contract. Admin enforces the schema on
# POST /resources/pipeline_config per kgspin-interface 0.6.0; demo reads
# trust that contract.


# Sprint 12 Task 3: UI slot loading is now admin-driven per ADR-005 sibling
# of CTO directive 2026-04-19 (dynamic pipeline_config dropdown). Slots come
# from admin's registry via admin_registry_reader at request time, cached
# per-request with a circuit-breaker that falls back to last-known-good
# on admin flaps. The transitional seed YAML at
# docs/sprints/sprint-12/seed-fallback/ui_slots.yaml hydrates the dropdown
# while kgspin-blueprint registers the real pipeline_configs; once they
# land in admin the fallback path stops being exercised.
from pathlib import Path as _Path  # noqa: E402
_SEED_FALLBACK_UI_SLOTS = (
    _Path(__file__).resolve().parent.parent.parent
    / "docs" / "sprints" / "sprint-12" / "seed-fallback" / "ui_slots.yaml"
)


def _current_ui_slots() -> list[dict]:
    """Per-request admin-first read of the UI slot dropdown list.

    Returns the translated slot dicts. Empty list if admin is empty
    AND the seed fallback is missing — UI shows the soft empty-state
    message per VP Prod 2026-04-19 review.
    """
    from kgspin_demo_app.services.admin_registry_reader import list_pipeline_configs
    return list_pipeline_configs(
        _get_registry_client(),
        seed_fallback_path=_SEED_FALLBACK_UI_SLOTS,
    )


# --- Cached models (pre-warmed at startup to avoid first-request latency) ---
from bundle_resolve import (  # noqa: E402  (re-export for legacy call sites)
    _bundle_cache,
    _bundle_id,
    _CACHED_BUNDLE,
    _CACHED_GLINER_BACKEND,
    _get_bundle,
    _get_gliner_backend,
    _init_lock,
    _split_bundle_id,
)
import bundle_resolve as _bundle_resolve  # for purge_caches()


# --- LLM Response Parsing ---

def _parse_llm_json(text: str) -> dict:
    """Strip markdown fences and parse JSON from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)


# --- FastAPI App ---

app = FastAPI(title="KGSpin Comparison Demo")


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Sprint 10 Task 7: admin-down graceful degradation ----------------------
#
# registry_http.py raises ``RuntimeError("admin <url> unreachable: ...")``
# on httpx connection failures. Catch that specific shape at the FastAPI
# boundary and render an actionable JSON 503 instead of a generic 500.
#
# [VP Eng MAJOR-1] Conjunctive substring match: BOTH ``"admin "`` AND
# ``"unreachable"`` must appear in the message. This prevents swallowing
# other ``RuntimeError``s that share either token alone. The Sprint 11
# refactor to a typed ``AdminUnreachableError`` is tracked at
# ``docs/backlog/tech-debt/sprint-11-registry-http-typed-errors.md``.


def _is_admin_unreachable(exc: BaseException) -> bool:
    """True iff ``exc`` is a ``RuntimeError`` carrying the registry_http
    admin-unreachable signature. Conjunctive match per VP Eng MAJOR-1."""
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc)
    return "admin " in msg and "unreachable" in msg


def _emit_admin_unreachable_sse(context_label: str, exc: BaseException) -> str:
    """Render the admin-down condition as a single SSE ``error`` event.

    Used from inside an SSE generator's top-level ``try:``. The caller is
    responsible for also emitting a ``done`` event and returning cleanly
    so the stream closes.
    """
    admin_url = os.environ.get("KGSPIN_ADMIN_URL", "http://127.0.0.1:8750").rstrip("/")
    return sse_event("error", {
        "step": context_label,
        "message": (
            f"Admin unreachable at {admin_url}. Start admin with "
            f"'kgspin-admin serve' or set KGSPIN_ADMIN_URL to its listening "
            f"address. (underlying: {exc})"
        ),
        "recoverable": True,
    })


@app.exception_handler(RuntimeError)
async def _admin_unreachable_handler(request: Request, exc: RuntimeError):
    """Render ``admin <url> unreachable: ...`` RuntimeErrors as HTTP 503.

    Non-admin-unreachable RuntimeErrors are re-raised so FastAPI's default
    500 path runs; this keeps the conjunctive substring contract [VP Eng
    MAJOR-1] from swallowing unrelated errors.
    """
    if not _is_admin_unreachable(exc):
        raise exc
    admin_url = os.environ.get("KGSPIN_ADMIN_URL", "http://127.0.0.1:8750").rstrip("/")
    return JSONResponse(
        status_code=503,
        content={
            "error": "Admin unreachable",
            "detail": admin_url,
            "hint": (
                "Start admin with 'kgspin-admin serve' or set KGSPIN_ADMIN_URL "
                "to its listening address."
            ),
        },
    )


@app.on_event("shutdown")
async def _close_registry_client() -> None:
    """[VP Eng MINOR-1] Close the cached ``httpx.Client`` on process shutdown."""
    cached = getattr(_get_registry_client, "cache_info", None)
    if cached is not None and cached().currsize > 0:
        try:
            _get_registry_client().close()
        except Exception:
            logger.exception("Error closing HttpResourceRegistryClient on shutdown")
        _get_registry_client.cache_clear()


def _ensure_spacy_model(model_name: str = "en_core_web_sm") -> None:
    """Ensure a spaCy model is installed locally, auto-downloading on first boot.

    INIT-001 Sprint 02 (BUG-010 follow-up): `uv sync` doesn't track spaCy
    language models, so every `uv sync` prunes the installed `en_core_web_sm`.
    The structural extraction path hard-fails on a missing model, and the
    emergent path warns-and-continues with reduced quality. Drift is silent
    and painful. This pre-flight catches it: try to load the model, and on
    OSError auto-download it from the spaCy release channel.

    Design notes:
    - No network call in the happy path (model already present → 200ms load).
    - Download is synchronous in warmup, so first-ever boot takes ~5s longer.
    - If download fails (no network, CDN 504), we log a warning and let the
      structural path surface its own error later. Non-fatal.
    - This is a demo-side concern per core team guidance in BUG-010 response
      — kgspin-core deliberately does not auto-download, and I agree.
    """
    try:
        import spacy
    except ImportError:
        logger.warning(
            "spaCy is not installed; structural extraction will fail. "
            "Expected to be pulled in transitively by kgspin-core."
        )
        return

    try:
        spacy.load(model_name)
        logger.info("spaCy model '%s' already installed", model_name)
        return
    except OSError:
        pass  # fall through to download

    logger.warning(
        "spaCy model '%s' not installed — auto-downloading (~12 MiB). "
        "This only happens on first boot or after `uv sync` prunes it.",
        model_name,
    )
    try:
        from spacy.cli.download import download as spacy_download
        spacy_download(model_name)
        spacy.load(model_name)  # re-verify
        logger.info("spaCy model '%s' installed successfully", model_name)
    except Exception as e:
        logger.warning(
            "spaCy model auto-download failed: %s. Structural extraction "
            "will fail until you run: `uv run python -m spacy download %s`",
            e, model_name,
        )


@app.on_event("startup")
async def check_admin_pipelines():
    """W1-D (ADR-003 §5): warn the operator if admin has no pipelines.

    Demo UI shows an empty pipeline dropdown when this returns []; do
    not crash. The fix is to register pipelines from the blueprint:
    ``kgspin-admin sync pipelines``.
    """
    pipeline_ids = list_available_pipelines()
    if not pipeline_ids:
        logger.warning(
            "No pipelines registered in admin. "
            "Run `kgspin-admin sync pipelines` (W1-C) to register from archetypes. "
            "Demo will boot but the pipeline dropdown will be empty."
        )
    else:
        logger.info(
            "Admin reports %d registered pipelines: %s",
            len(pipeline_ids), ", ".join(pipeline_ids[:10]),
        )


@app.on_event("startup")
async def warmup_models():
    """Pre-load models at startup so first demo request is fast."""
    # INIT-001 Sprint 01: skip warmup entirely when no default bundle is present.
    # Sprint 02 (bundle/corpus wiring) restored the pre-warm path.
    if BUNDLE_PATH is None:
        logger.warning(
            "Skipping model pre-warm — BUNDLE_PATH is None (no compiled bundles found). "
            "Server will boot but extraction endpoints will fail until bundles are compiled."
        )
        return
    logger.info("Pre-warming models...")

    def _warmup():
        # INIT-001 Sprint 02 (Option B / BUG-010 response): ensure the spaCy
        # model is present before extraction paths need it. Auto-downloads on
        # first boot or after a sync that prunes it.
        _ensure_spacy_model("en_core_web_sm")
        # Load bundle (fast, but cache it)
        _get_bundle()
        # Load GLiNER model (~4s first time, cached by HuggingFace after)
        _get_gliner_backend()
        # Load sentence-transformers embedding engine (~2-5s first time, singleton)
        from kgspin_core.execution.embeddings import get_embedding_engine
        get_embedding_engine()
        logger.info("Models pre-warmed and ready.")

    await asyncio.to_thread(_warmup)


# --- Gemini Run Log ---


from cache.run_log import (  # noqa: E402  (re-export for legacy call sites)
    GeminiRunLog,
    ImpactQARunLog,
    IntelRunLog,
    KGenRunLog,
    ModularRunLog,
    _impact_qa_run_log,
    _intel_run_log,
    _kgen_run_log,
    _modular_run_log,
    _run_log,
)

# Wave B: routes carved into routes/; mount them on the app now that `app` exists.
from routes import runs as _routes_runs  # noqa: E402
from routes import corpus as _routes_corpus  # noqa: E402
from routes import feedback as _routes_feedback  # noqa: E402

app.include_router(_routes_runs.router)
app.include_router(_routes_corpus.router)
app.include_router(_routes_feedback.router)



# ---------------------------------------------------------------------------
# Shared Pipeline Cache Helpers
# ---------------------------------------------------------------------------
# Sprint 78: Standardised cache operations used by both financial and clinical
# pipelines. All config key formats are backward-compatible with existing cached
# files on disk (same kwargs → same key string).
# ---------------------------------------------------------------------------

def _prompt_version_hash(
    extractor_module: str, extractor_class: str,
    bundle_path, patterns_path, model: str,
) -> str:
    """Compute prompt version hash for cache key construction.

    Imports the extractor lazily and hashes its system prompt. Returns an 8-char
    hex digest, or "unknown" if the extractor can't be loaded.
    """
    try:
        mod = __import__(extractor_module)
        cls = getattr(mod, extractor_class)
        ext = cls(bundle_path, patterns_path, model=model)
        return hashlib.md5(ext._build_system_prompt("__hash__").encode()).hexdigest()[:8]
    except Exception:
        return "unknown"


def _build_pipeline_cache_keys(
    bundle_path, patterns_path, corpus_kb: float, model: str,
    bundle_name: str = "", domain_id: str = "",
    pipeline_id: str | None = None,
) -> dict:
    """Build cache config keys for all three extraction pipelines.

    Returns {"gemini": str, "modular": str, "kgen": str}.

    The kgen ``bid`` always includes ``pipeline_id`` when one is supplied,
    so different KGSpin strategies (discovery-deep, fan-out, discovery-rapid)
    on the same bundle land in separate cache entries. Without this, strategy
    selection becomes a no-op after the first run — the cache serves the
    first strategy's KG for every subsequent selection.
    """
    gem_pv = _prompt_version_hash(
        "gemini_extractor", "GeminiKGExtractor", bundle_path, patterns_path, model,
    )
    mod_pv = _prompt_version_hash(
        "gemini_aligned_extractor", "GeminiAlignedExtractor", bundle_path, patterns_path, model,
    )
    kgen_kwargs = {"corpus_kb": corpus_kb}
    if bundle_name:
        bid = bundle_name
        if pipeline_id and f"_p={pipeline_id}" not in bid:
            bid = f"{bid}_p={pipeline_id}"
        kgen_kwargs["bid"] = bid
    gem_kwargs = {"corpus_kb": corpus_kb, "model": model, "pv": gem_pv, "cv": DEMO_CACHE_VERSION}
    mod_kwargs = {"corpus_kb": corpus_kb, "model": model, "pv": mod_pv, "cv": DEMO_CACHE_VERSION}
    if domain_id:
        gem_kwargs["dom"] = domain_id
        mod_kwargs["dom"] = domain_id
    return {
        "gemini": _run_log.config_key("gemini", **gem_kwargs),
        "modular": _modular_run_log.config_key("modular", **mod_kwargs),
        "kgen": _kgen_run_log.config_key("kgen", **kgen_kwargs),
    }


def _build_split_kgen_cache_key(
    domain_id: str, pipeline_id: str, corpus_kb: float,
) -> str:
    """Sprint 118: Build KGSpin cache key for split domain+pipeline bundles.

    Key format: ``kgen_dom={domain_id}_p={pipeline_id}_kb={corpus_kb}``
    Includes both domain and pipeline to prevent stale cache hits when
    switching either independently.
    """
    return _kgen_run_log.config_key(
        "kgen", corpus_kb=corpus_kb,
        bid=_split_bundle_id(domain_id, pipeline_id),
    )


# Map pipeline name → run log singleton
_PIPELINE_LOGS = {
    "gemini": _run_log,
    "modular": _modular_run_log,
    "kgen": _kgen_run_log,
    "intel": _intel_run_log,
    "impact_qa": _impact_qa_run_log,
}


def _cache_lookup(
    pipeline: str, ticker: str, cfg_key: str, *, force_refresh: bool = False,
) -> tuple:
    """Check for a cached extraction run.

    Returns (is_cached: bool, logged_run: dict | None).
    """
    if force_refresh:
        return False, None
    run_log = _PIPELINE_LOGS[pipeline]
    logged_run = run_log.latest(ticker, cfg_key)
    return (True, logged_run) if logged_run else (False, None)


def _cache_save(
    pipeline: str, ticker: str, cfg_key: str, kg: dict,
    tokens: int, elapsed: float,
    *,
    model_fallback: str = "gemini",
    bundle_version: str = "",
    min_entities: int = 1,
    skip_on_errors: bool = False,
    errors: int = 0,
    truncated: bool = False,
    document_context: dict | None = None,
    actual_kb: float = 0,
) -> None:
    """Save an extraction result to the disk cache.

    Skips saving if the KG has fewer entities than min_entities,
    or if skip_on_errors is True and errors > 0 with 0 entities.
    Backward-compatible with all existing save patterns.
    """
    # Sprint 79: Inject document_context into KG before saving
    if document_context and "document_context" not in kg:
        kg["document_context"] = document_context

    # Persist corpus_kb in provenance so throughput can be computed from disk cache
    if actual_kb > 0:
        if "provenance" not in kg:
            kg["provenance"] = {}
        kg["provenance"]["corpus_kb"] = round(actual_kb, 1)

    entity_count = len(kg.get("entities", []))
    if entity_count < min_entities:
        if skip_on_errors and errors > 0:
            logger.warning(f"Skipping {pipeline} cache for {ticker}: {entity_count} entities, {errors} errors")
        elif truncated:
            logger.warning(f"Skipping {pipeline} cache for {ticker}: truncated with {entity_count} entities")
        else:
            logger.warning(f"Skipping {pipeline} cache for {ticker}: {entity_count} entities < {min_entities}")
        return
    # Additional skip logic for gemini: skip if truncated AND 0 entities
    if pipeline == "gemini" and truncated and entity_count == 0:
        logger.warning(f"Skipping {pipeline} cache for {ticker}: truncated with 0 entities")
        return

    run_log = _PIPELINE_LOGS[pipeline]
    run_model = kg.get("provenance", {}).get("model", model_fallback)
    try:
        run_log.log_run(
            ticker, cfg_key, kg,
            tokens, elapsed, run_model,
            cache_version=DEMO_CACHE_VERSION,
            bundle_version=bundle_version,
        )
    except Exception as e:
        logger.warning(f"Failed to log {pipeline} run for {ticker}: {e}")


def _backfill_document_context(
    kg: dict,
    domain: str = "financial",
    info: dict | None = None,
    gold_data: dict | None = None,
    sec_doc_meta: dict | None = None,
) -> dict:
    """Sprint 79: Backfill document_context into a KG dict if missing.

    For cached graphs that predate Sprint 79, this injects the structured
    document context dictionary so the UI can display metadata.

    Args:
        kg: The KG dict (may already have document_context).
        domain: 'financial' or 'clinical'.
        info: Ticker resolution info dict (financial) with 'name', 'ticker'.
        gold_data: Clinical gold data dict with 'metadata', 'nct_id'.
        sec_doc_meta: Optional dict with EDGAR filing metadata
            (filing_date, cik, accession_number, company_name, etc.)

    Returns:
        The KG dict with document_context populated (mutated in place).
    """
    if kg.get("document_context"):
        return kg  # Already populated

    if domain == "financial" and info:
        from kgspin_plugin_financial.domain.plugin import FinancialCorpusPlugin
        meta = sec_doc_meta or {}
        # Try loading EDGAR metadata from cache if not provided
        if not meta:
            try:
                from kgspin_plugin_financial.data_sources.edgar import EdgarDataSource
                _eds = EdgarDataSource()
                _doc = _eds.load_from_cache(info.get("ticker", ""), "10-K")
                if _doc:
                    meta = {
                        "company_name": _doc.company_name,
                        "filing_date": _doc.filing_date,
                        "cik": _doc.cik or "",
                        "accession_number": _doc.accession_number or "",
                        "fiscal_year_end": _doc.fiscal_year_end or "",
                        "source_url": _doc.source_url or "",
                    }
            except Exception:
                pass
        fake_gold = {
            "company": meta.get("company_name") or info.get("name", ""),
            "ticker": info.get("ticker", ""),
            "source": "SEC 10-K",
            "filing_date": meta.get("filing_date", ""),
            "cik": meta.get("cik", ""),
            "accession_number": meta.get("accession_number", ""),
            "fiscal_year_end": meta.get("fiscal_year_end", ""),
            "source_url": meta.get("source_url", ""),
        }
        kg["document_context"] = FinancialCorpusPlugin.build_document_context(
            fake_gold, info.get("ticker", "")
        )
    elif domain == "clinical" and gold_data:
        from kgenskills.domains.clinical.plugin import ClinicalCorpusPlugin
        nct_id = gold_data.get("nct_id", "")
        kg["document_context"] = ClinicalCorpusPlugin.build_document_context(
            gold_data, nct_id
        )

    return kg


def _cached_step_event(
    pipeline: str, step: str, run_log_name: str, ticker: str, cfg_key: str,
    tokens: int = 0,
) -> dict:
    """Build step_complete SSE event data for a cached pipeline result."""
    run_log = _PIPELINE_LOGS[run_log_name]
    run_count = run_log.count(ticker, cfg_key)
    label_map = {
        "kgenskills": "KGSpin",
        "gemini": "LLM Full Shot",
        "modular": "LLM Multi-Stage",
    }
    label_prefix = label_map.get(pipeline, pipeline)
    return {
        "step": step,
        "pipeline": pipeline,
        "label": f"{label_prefix}: Loaded from cache ({run_count} runs available)",
        "duration_ms": 0,
        "tokens": tokens,
    }


def _cached_kg_event(
    pipeline: str, logged_run: dict, actual_kb: float,
    run_log_name: str, ticker: str, cfg_key: str,
    *,
    model: str = "",
    extra_stats: dict | None = None,
    backfill_info: dict | None = None,
    backfill_domain: str = "financial",
) -> dict:
    """Build kg_ready SSE event data for a cached pipeline result."""
    run_log = _PIPELINE_LOGS[run_log_name]
    kg = logged_run["kg"]
    # Sprint 79: Backfill document_context for older cached KGs
    if backfill_info and not kg.get("document_context"):
        _backfill_document_context(kg, domain=backfill_domain, info=backfill_info)
    vis = build_vis_data(kg)
    elapsed = logged_run.get("elapsed_seconds", 0)
    throughput = actual_kb / elapsed if elapsed > 0 else 0
    tokens = logged_run.get("total_tokens", 0)

    stats = {
        "entities": len(vis["nodes"]),
        "relationships": len(vis["edges"]),
        "tokens": tokens,
        "duration_ms": int(elapsed * 1000),
        "throughput_kb_sec": round(throughput, 1),
        "actual_kb": round(actual_kb, 1),
    }
    if extra_stats:
        stats.update(extra_stats)

    event = {
        "pipeline": pipeline,
        "vis": vis,
        "stats": stats,
        "from_log": True,
        "run_index": 0,
        "total_runs": run_log.count(ticker, cfg_key),
        "run_timestamp": logged_run.get("created_at", ""),
    }
    if model:
        event["model"] = model
        event["model_pricing"] = GEMINI_MODEL_PRICING.get(model, {})
    if logged_run.get("bundle_version"):
        event["bundle_version"] = logged_run["bundle_version"]
    # Sprint 79: Include document_context if available
    if kg.get("document_context"):
        event["document_context"] = kg["document_context"]
    return event


def _fresh_kg_event(
    pipeline: str, kg: dict, tokens: int, elapsed: float, actual_kb: float,
    run_log_name: str, ticker: str, cfg_key: str,
    *,
    model: str = "",
    truncated: bool = False,
    extra_stats: dict | None = None,
    backfill_info: dict | None = None,
    backfill_domain: str = "financial",
) -> dict:
    """Build kg_ready SSE event data for a freshly-extracted result."""
    run_log = _PIPELINE_LOGS[run_log_name]
    # Sprint 79: Inject document_context for fresh KGs
    if backfill_info and not kg.get("document_context"):
        _backfill_document_context(kg, domain=backfill_domain, info=backfill_info)
    vis = build_vis_data(kg)
    throughput = actual_kb / elapsed if elapsed > 0 else 0

    stats = {
        "entities": len(vis["nodes"]),
        "relationships": len(vis["edges"]),
        "tokens": tokens,
        "duration_ms": int(elapsed * 1000),
        "throughput_kb_sec": round(throughput, 1),
        "actual_kb": round(actual_kb, 1),
    }
    if truncated:
        stats["truncated"] = truncated
    if extra_stats:
        stats.update(extra_stats)

    event = {
        "pipeline": pipeline,
        "vis": vis,
        "stats": stats,
        "from_log": False,
        "total_runs": run_log.count(ticker, cfg_key),
    }
    if model:
        event["model"] = model
        event["model_pricing"] = GEMINI_MODEL_PRICING.get(model, {})
    # Sprint 79: Include document_context if available
    if kg.get("document_context"):
        event["document_context"] = kg["document_context"]
    return event


@app.get("/")
async def index():
    html_path = STATIC_DIR / "compare.html"
    return HTMLResponse(
        html_path.read_text(),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/tickers")
async def list_tickers():
    return JSONResponse(
        {k: v["name"] for k, v in KNOWN_TICKERS.items()}
    )


@app.get("/api/slots")
async def list_ui_slots():
    """Sprint 12 Task 3: return the admin-driven compare-UI slot list.

    Source of truth is now kgspin-admin's pipeline_config registry.
    Demo reads per-request via the circuit-breakered
    ``admin_registry_reader`` service; falls back to the Sprint-12
    transitional seed YAML at
    ``docs/sprints/sprint-12/seed-fallback/ui_slots.yaml`` when admin
    returns empty (until kgspin-blueprint ships the real pipeline
    registration).

    Empty-state copy (when admin is empty AND no fallback present)
    per VP Prod 2026-04-19 review:
    ``"No pipelines available — ask your admin to register them."``
    """
    slots = _current_ui_slots()
    if not slots:
        return JSONResponse({
            "slots": [],
            "empty_state_message": (
                "No pipelines available — ask your admin to register them."
            ),
        })
    return JSONResponse({"slots": slots})


# --- Sprint 07 Task 7: Refresh Local Corpus ---------------------------------
#
# Triggers a lander subprocess from the UI. All mandates from VP Security
# are enforced: regex input validation (7.1), sys.executable pinning (7.2),
# list-based args (7.3), explicit minimal env dict (7.4).
#
# Landers are invoked via `sys.executable -m kgspin_demo_app.landers.<name>`.
# No shell expansion. No PATH lookup. No user-supplied env vars.

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_NCT_RE = re.compile(r"^NCT[0-9]{8}$")
_NEWS_QUERY_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,100}$")

_LANDER_MODULE_BY_KIND = {
    "sec": "kgspin_demo_app.landers.sec",
    "clinical": "kgspin_demo_app.landers.clinical",
    # Sprint 11 (ADR-004): backend-named landers. "yahoo" kept as an
    # alias for "yahoo-rss" via the shim module; new code uses the
    # new kind names directly.
    "marketaux": "kgspin_demo_app.landers.marketaux",
    "yahoo-rss": "kgspin_demo_app.landers.yahoo_rss",
    "newsapi": "kgspin_demo_app.landers.newsapi",
}

# Per-kind env-var allowlist — only these pass into the subprocess.
# EDGAR_IDENTITY is the pre-existing convention for SEC; SEC_USER_AGENT
# is retained as a fallback for legacy operator scripts (see sec.py auth
# resolution order).
_LANDER_ENV_ALLOWLIST = {
    "sec": ("EDGAR_IDENTITY", "SEC_USER_AGENT", "KGSPIN_CORPUS_ROOT"),
    "clinical": ("CLINICAL_TRIALS_API_KEY", "KGSPIN_CORPUS_ROOT"),
    "marketaux": ("MARKETAUX_API_KEY", "KGSPIN_CORPUS_ROOT"),
    "yahoo-rss": ("KGSPIN_CORPUS_ROOT",),  # no credentials required
    "newsapi": ("NEWSAPI_KEY", "KGSPIN_CORPUS_ROOT"),
}


def _build_lander_env(kind: str) -> dict[str, str]:
    """Build a minimal env dict for a lander subprocess (VP Sec Mandate 7.4).

    Only ``PATH`` + ``PYTHONPATH`` + the per-kind env-var allowlist.
    The parent process environment is NOT propagated.
    """
    env: dict[str, str] = {}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    # HOME is needed for ~/.kgspin/corpus default
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    for var in _LANDER_ENV_ALLOWLIST.get(kind, ()):
        if var in os.environ:
            env[var] = os.environ[var]
    return env


async def _poll_registry_for_registration(
    domain: str,
    source: str,
    identifier: dict[str, str],
) -> Optional[str]:
    """Sprint 10 Task 4: bounded poll loop for a just-landed resource.

    Returns the resource id on success, or ``None`` on timeout. Poll
    parameters are the module-level constants at the top of this file
    (``_POST_LANDER_POLL_*``). Only resources with a ``registered_at``
    within ``_POST_LANDER_POLL_REGISTERED_WINDOW_SEC`` of ``utcnow()``
    count as "just registered by this subprocess" — older resources
    satisfying the same identifier match are treated as pre-existing
    and ignored here (the caller will not emit step_complete for them).
    """
    client = _get_registry_client()
    deadline = time.monotonic() + _POST_LANDER_POLL_MAX_SEC
    window = timedelta(seconds=_POST_LANDER_POLL_REGISTERED_WINDOW_SEC)
    attempt = 0
    while True:
        attempt += 1
        elapsed_ms = int((time.monotonic() - (deadline - _POST_LANDER_POLL_MAX_SEC)) * 1000)
        resources = client.list(
            ResourceKind.CORPUS_DOCUMENT, domain=domain, source=source,
        )
        now = datetime.now(timezone.utc)
        for r in resources:
            meta = r.metadata or {}
            got = meta.get("identifier") or {}
            # Subset match: news landers register per-article with
            # ``identifier={"article_id":..., "ticker":T}``; the caller passes
            # only the common key (``ticker`` or ``query``). SEC/clinical
            # landers register one resource with a fully-specified identifier,
            # so subset == equality for them.
            if not all(got.get(k) == v for k, v in identifier.items()):
                continue
            reg_at = r.provenance.registered_at if r.provenance else None
            if reg_at is None:
                continue
            if reg_at.tzinfo is None:
                reg_at = reg_at.replace(tzinfo=timezone.utc)
            if now - reg_at <= window:
                logger.debug(
                    "[REFRESH_POLL] attempt=%d elapsed_ms=%d HIT id=%s",
                    attempt, elapsed_ms, r.id,
                )
                return r.id
        logger.debug(
            "[REFRESH_POLL] attempt=%d elapsed_ms=%d miss (domain=%s source=%s id=%r)",
            attempt, elapsed_ms, domain, source, identifier,
        )
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(_POST_LANDER_POLL_INTERVAL_SEC)


async def _run_lander_subprocess(
    kind: str,
    cli_args: list[str],
    *,
    registry_key: tuple[str, str, dict[str, str]],
) -> AsyncGenerator[str, None]:
    """Launch a lander subprocess and stream its stderr back as SSE events.

    VP Sec Mandates:
    - 7.2 Executable pinning: ``[sys.executable, "-m", <module>, ...]``
    - 7.3 Argument array (never shell=True)
    - 7.4 Explicit minimal env dict
    - 4.1 50 MiB artifact size cap is enforced INSIDE the lander, not here

    Sprint 10 Task 4: ``registry_key=(domain, source, identifier)`` pins
    the expected registry entry so the post-subprocess poll can confirm
    the lander's write was recorded by admin.

    Yields SSE events: ``step_progress`` per stderr line, ``step_complete``
    or ``error`` at exit. On subprocess-success-but-registry-miss emits
    the specific CTO-mandated diagnostic: "lander succeeded but admin
    didn't record the document (check KGSPIN_ADMIN_URL)" [CTO-AMEND-2].
    """
    import asyncio as _asyncio

    module = _LANDER_MODULE_BY_KIND[kind]
    argv = [sys.executable, "-m", module, *cli_args]
    env = _build_lander_env(kind)
    logger.info(f"[REFRESH] launching lander kind={kind} argv={argv[1:]}")

    yield sse_event("step_start", {
        "step": "refresh_corpus",
        "label": f"Launching {kind} lander...",
    })
    await _asyncio.sleep(0)

    try:
        proc = await _asyncio.create_subprocess_exec(
            *argv,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            env=env,  # explicit minimal env (7.4)
        )
    except FileNotFoundError as e:
        logger.exception("Lander subprocess could not be started")
        yield sse_event("error", {
            "step": "refresh_corpus",
            "message": f"Could not start lander: {e}",
            "recoverable": True,
        })
        yield sse_event("done", {"total_duration_ms": 0})
        return

    stderr_lines: list[str] = []
    stdout_lines: list[str] = []

    async def _stream(stream, collect: list[str], label: str):
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()[:500]
            collect.append(text)
            yield sse_event("step_progress", {
                "step": "refresh_corpus",
                "label": f"[{label}] {text}",
            })
            await _asyncio.sleep(0)

    # Interleave stderr + stdout streams until process exits.
    async def _pump_both():
        async for ev in _stream(proc.stderr, stderr_lines, "stderr"):
            yield ev
        async for ev in _stream(proc.stdout, stdout_lines, "stdout"):
            yield ev

    async for ev in _pump_both():
        yield ev

    returncode = await proc.wait()
    if returncode != 0:
        # Truncate stderr to last 100 lines per plan safety note.
        tail = stderr_lines[-100:]
        yield sse_event("error", {
            "step": "refresh_corpus",
            "message": f"Lander {kind} exited with code {returncode}",
            "exit_code": returncode,
            "stderr_tail": tail,
            "recoverable": True,
        })
        yield sse_event("done", {"total_duration_ms": 0})
        return

    # Subprocess exited 0 — confirm admin recorded the document.
    domain, source, identifier = registry_key
    try:
        resource_id = await _poll_registry_for_registration(domain, source, identifier)
    except RuntimeError as e:
        # Admin unreachable / HTTP error from _poll's client.list call.
        # Task 7's exception handler renders admin-down for non-SSE routes;
        # inside this SSE stream we emit a direct error event instead.
        if "admin " in str(e) and "unreachable" in str(e):
            yield sse_event("error", {
                "step": "refresh_corpus",
                "message": f"Admin unreachable during registry confirmation: {e}",
                "recoverable": True,
            })
        else:
            raise
        yield sse_event("done", {"total_duration_ms": 0})
        return

    if resource_id is None:
        # CTO-AMEND-2: verbatim diagnostic language.
        yield sse_event("error", {
            "step": "refresh_corpus",
            "message": (
                "lander succeeded but admin didn't record the document "
                "(check KGSPIN_ADMIN_URL)"
            ),
            "recoverable": True,
            "attempted_registry": {
                "domain": domain,
                "source": source,
                "identifier": identifier,
            },
        })
    else:
        yield sse_event("step_complete", {
            "step": "refresh_corpus",
            "label": f"Lander {kind} completed: {len(stdout_lines)} artifact(s) landed",
            "landed_paths": stdout_lines,
            "resource_id": resource_id,
        })
    yield sse_event("done", {"total_duration_ms": 0})


# Sprint 12 Task 4: /api/bundles (deprecated since Sprint 86) DELETED.
# Both VPs agreed 2026-04-19 to cut it outright — no transitional shim.
# The richer /api/bundle-options is now the primary bundle data endpoint
# and additionally surfaces admin-registered bundles under
# `admin_bundles`, phasing in admin as the SSoT for bundle discovery.


@app.get("/api/bundle-options")
async def bundle_options(domain: str = "financial"):
    """Return bundle options augmented with admin-registered bundles.

    Sprint 12 Task 4: the response now includes ``admin_bundles`` — a
    list of ``{name, version, domain, description}`` entries read
    from ``GET /resources?kind=bundle_compiled``, filtered by
    ``domain``. When kgspin-blueprint registers the real bundles in
    admin, the frontend's dropdown starts pulling from there; legacy
    disk-walked ``bundles`` / ``domains`` / ``pipelines`` remain for
    backwards compat this sprint and can be removed Sprint 13+ after
    the frontend migrates.

    Circuit-breaker + 2s cache per-request on the admin side — a
    flapping admin does not fail the legacy disk-walk fields.
    """
    options = list_bundle_options(domain)
    try:
        from kgspin_demo_app.services.admin_registry_reader import list_bundle_configs
        options["admin_bundles"] = list_bundle_configs(
            _get_registry_client(),
            domain=domain,
        )
    except Exception as e:  # noqa: BLE001 — legacy path must keep working
        logger.warning(
            "[BUNDLE_OPTIONS] admin read failed (%s: %s); using legacy disk walk only",
            type(e).__name__, str(e)[:100],
        )
        options["admin_bundles"] = []
    return JSONResponse(options)


@app.get("/api/extraction-schema")
async def get_extraction_schema(bundle: str = None):
    """Return the target entity types, hierarchy, and relationship types.

    Source of truth is admin's ``bundle_source_yaml`` registry (Wave 3).
    Query shape:
        bundle=financial-v2  → resolves to the blueprint's domain YAML
        bundle=clinical-v2   → same
        bundle unset         → falls back to ``financial-v2``.
    Raises 500 with a focused message if the admin lookup fails.
    """
    import yaml as _yaml
    try:
        domain_id = (bundle or "financial-v2").replace("bundles/", "").replace(".yaml", "")
        patterns_path = resolve_domain_yaml_path(domain_id)
        with open(patterns_path) as f:
            patterns = _yaml.safe_load(f)

        # Build type hierarchy
        types_section = patterns.get("types", {})
        type_hierarchy = {}
        for parent, info in sorted(types_section.items()):
            subtypes = {}
            for sub, sinfo in sorted(info.get("subtypes", {}).items()):
                subtypes[sub] = sinfo.get("semantic_definition", "")
            type_hierarchy[parent] = {
                "definition": info.get("semantic_definition", ""),
                "subtypes": subtypes,
            }

        # Build relationship list
        relationships = []
        for rp in patterns.get("relationship_patterns", []):
            relationships.append({
                "name": rp["name"],
                "definition": rp.get("semantic_definition", ""),
            })

        # All valid entity type names (parents + subtypes)
        valid_types = set(types_section.keys())
        for info in types_section.values():
            valid_types.update(info.get("subtypes", {}).keys())

        return JSONResponse({
            "type_hierarchy": type_hierarchy,
            "relationships": relationships,
            "valid_entity_types": sorted(valid_types),
            "patterns_file": str(patterns_path),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/prompt-template/{pipeline}")
async def get_prompt_template(pipeline: str, domain: str = "financial"):
    """Return the LLM prompt template for explainability."""
    try:
        bundle_path = BUNDLE_PATH
        patterns_path = PATTERNS_PATH
        if domain == "clinical":
            from pathlib import Path as P
            clinical_bundles = list_bundles("clinical")
            if clinical_bundles:
                bundle_path = P(".bundles") / clinical_bundles[0]
                patterns_path = P("patterns") / "clinical_patterns.yaml"

        # INIT-001 Sprint 03: the demo-owned prompt-display helpers
        # (GeminiKGExtractor.prompt_template, GeminiAlignedExtractor._build_*)
        # were deleted when INIT-004 moved LLM strategies into kgspin-core.
        # The new strategy modules (kgspin_core.strategies.gemini_full_shot and
        # kgspin_core.strategies.aligned_extractor) don't expose a public
        # prompt-preview API today. This endpoint returns a pointer to the
        # source until a proper API lands upstream — tracked as a Sprint 03
        # known limitation in dev-report.md.
        if pipeline in ("fullshot", "multistage"):
            strategy_mod = (
                "kgspin_core.strategies.gemini_full_shot"
                if pipeline == "fullshot"
                else "kgspin_core.strategies.aligned_extractor"
            )
            template = (
                f"Prompt preview is temporarily unavailable post-INIT-004.\n"
                f"The {pipeline} strategy lives in {strategy_mod}; the prompt\n"
                f"is constructed inside that module. Upstream work (core team)\n"
                f"will expose a prompt-preview API in a future initiative."
            )
        else:
            return JSONResponse({"error": f"Unknown pipeline: {pipeline}"}, status_code=400)
        return JSONResponse({"pipeline": pipeline, "template": template})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/domains")
async def available_domains():
    """List available domains with their bundle counts.

    Sprint 77 Task 5a: Domain discovery for the domain selector.
    """
    domains = []
    for domain_name in ["financial", "clinical"]:
        bundles = list_bundles(domain_name)
        if bundles:
            domains.append({
                "name": domain_name,
                "label": domain_name.title(),
                "bundle_count": len(bundles),
                "default_bundle": bundles[0],
            })
    return JSONResponse({"domains": domains})


@app.get("/api/clinical-trials")
async def clinical_trials():
    """List available clinical trials for the corpus selector.

    Sprint 05 Task 3: discovery is now provider-driven. We union the
    ClinicalTrials.gov seed list (live provider) with any gold fixtures
    (legacy path). If neither yields results, return an empty list —
    the frontend renders a friendly "No trials configured" message.
    """
    import json
    from pathlib import Path

    trials = []

    # Sprint 06: kgspin-plugin-clinical's `clinical_trials` provider is the
    # canonical source. Its `list_available` makes a live API call so we
    # cache results in-memory for 15 minutes (VP Eng rate-limit guidance).
    try:
        provider = _corpus_provider_cache.get("clinical_trials")
        if provider is None:
            # Sprint 09 retired the plugin-provider factory; a cold cache
            # means no live data, so fall through to gold fixtures below.
            raise RuntimeError("clinical_trials provider not in cache")

        # Curated seed query: 3 therapeutic areas (oncology / infectious
        # disease / cardiology) per Sprint 05 VP Prod directive on
        # domain-agnosticism. Use the plugin's query DSL.
        seed_results = []
        for query in (
            {"condition": "lung cancer", "phase": "PHASE3"},
            {"condition": "covid-19"},
            {"condition": "heart failure", "phase": "PHASE3"},
        ):
            try:
                seed_results.extend(provider.list_available(query=query, limit=2))
            except Exception:
                logger.exception("clinical_trials list_available failed for query=%s", query)

        # If the live API returned nothing (network down / rate limited),
        # fall back to the static seed NCTs from Sprint 05.
        if not seed_results:
            for nct in ("NCT03456076", "NCT04292899", "NCT02465060"):
                try:
                    doc = provider.fetch(nct)
                    seed_results.append(doc.metadata)
                except Exception:
                    logger.warning("Static seed NCT %s could not be fetched; skipping", nct)

        for meta in seed_results:
            ps = meta.provider_specific or {}
            trials.append({
                "nct_id": meta.identifier,
                "title": meta.title,
                "display": f"{meta.identifier} — {meta.title[:80]}" if meta.title else meta.identifier,
                "drugs": [],
                "num_publications": 0,
                "source": "clinical_trials",
                "source_url": getattr(meta, "source_url", "") or ps.get("source_url", ""),
                "phase": ", ".join(ps.get("phase", []) or []),
                "sponsor": ps.get("sponsor", ""),
                "overall_status": ps.get("overallStatus", ""),
            })
    except Exception:
        logger.exception("clinical_trials seed discovery failed; falling through to gold fixtures")

    gold_dir = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "gold" / "clinical"
    if gold_dir.exists():
        for gold_path in sorted(gold_dir.glob("*.json")):
            if "_mock" in gold_path.stem:
                continue
            try:
                data = json.loads(gold_path.read_text())
                nct_id = data.get("nct_id", gold_path.stem)
                title = data.get("trial_title", nct_id)
                # Extract drug name from gold triples for display
                drugs = set()
                for t in data.get("gold_triples", []):
                    if t.get("subject_type") == "DRUG":
                        drug = t.get("subject_text", "")
                        # Clean dosage info: "Canagliflozin (JNJ-28431754) 100 mg" → "Canagliflozin"
                        drug_clean = drug.split("(")[0].split(" mg")[0].split(" 100")[0].split(" 300")[0].strip()
                        if drug_clean and drug_clean.lower() not in ("placebo",):
                            drugs.add(drug_clean)
                drug_label = ", ".join(sorted(drugs)[:2]) if drugs else ""
                display = f"{nct_id} — {drug_label}" if drug_label else nct_id
                trials.append({
                    "nct_id": nct_id,
                    "title": title[:120],
                    "display": display,
                    "drugs": sorted(drugs),
                    "num_publications": len(data.get("input_documents", [])),
                })
            except (json.JSONDecodeError, OSError):
                continue
    return JSONResponse({"trials": trials})


@app.get("/api/test-sse")
async def test_sse():
    """Minimal SSE test endpoint to verify streaming works."""
    async def generate():
        for i in range(5):
            yield f"event: test\ndata: {{\"count\": {i}}}\n\n"
            await asyncio.sleep(1)
        yield f"event: done\ndata: {{\"message\": \"complete\"}}\n\n"
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/compare-clinical/{doc_id}")
async def compare_clinical(
    doc_id: str, request: Request, bundle: str = "",
    model: str = DEFAULT_GEMINI_MODEL, chunk_size: int = DEFAULT_CHUNK_SIZE,
    force_refresh: str = "",
    source: str = "live",
    llm_alias: str = "",
):
    """Sprint 78: Clinical domain comparison — KGSpin + LLM Full Shot + Multi-Stage.

    Sprint 06 Task 4: corpus source is now an explicit ``?source=live|gold``
    toggle (default ``live``). When ``source=live``, the corpus comes from
    the canonical ``clinical_trials`` provider (live ClinicalTrials.gov).
    When ``source=gold``, the legacy gold-fixture reader at
    ``tests/fixtures/gold/clinical/{nct}.json`` is used. There is NO
    silent fallback between the two — an explicit source is always
    chosen, surfaced in the SSE ``kg_ready`` payload as ``corpus_source``,
    and rendered as a badge in the slot header (per VP Eng "Fixture
    Drift" mandate).

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias. ``model`` is retained as deprecated compat; passing both is
    400 (ambiguous).
    """
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    if source not in ("live", "gold"):
        return JSONResponse(
            {"error": f"Invalid source={source!r}; must be 'live' or 'gold'."},
            status_code=400,
        )
    alias = llm_alias.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied="model" in request.query_params,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    bundle_name = bundle if bundle else "clinical-v2"
    gem_model = model if model in VALID_GEMINI_MODELS else DEFAULT_GEMINI_MODEL
    cs = chunk_size if chunk_size in VALID_CHUNK_SIZES else DEFAULT_CHUNK_SIZE
    refresh_set = set(force_refresh.split(",")) if force_refresh else set()
    if "all" in refresh_set or "1" in refresh_set:
        refresh_set = {"gemini", "modular", "kgen"}
    return StreamingResponse(
        _run_clinical_comparison(
            doc_id, request, bundle_name=bundle_name,
            model=gem_model, chunk_size=cs,
            force_refresh=refresh_set,
            corpus_source=source,
            llm_alias=alias,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# W3-D: canonical 5-pipeline whitelist. The ``strategy=`` query param and
# ``pipeline_config_ref=`` arg must name one of these (underscore form).
# Demo maps them to pipeline config names 1:1 by replacing ``_`` with ``-``.
CANONICAL_PIPELINE_STRATEGIES = (
    "fan_out",
    "discovery_rapid",
    "discovery_deep",
    "agentic_flash",
    "agentic_analyst",
)


class InvalidPipelineStrategyError(ValueError):
    """Raised when an API request names a pipeline strategy outside the
    canonical 5. Rendered by endpoint handlers as a 400 with a focused
    message listing the allowed values."""


def _canonical_pipeline_name(strategy: str) -> str:
    """Return the hyphenated admin pipeline config name for ``strategy``.

    ``strategy`` must be a member of :data:`CANONICAL_PIPELINE_STRATEGIES`
    (underscore form, matches the ``extractor:`` discriminator on the
    pipeline YAMLs). Raises :class:`InvalidPipelineStrategyError` on
    anything else.
    """
    if strategy not in CANONICAL_PIPELINE_STRATEGIES:
        raise InvalidPipelineStrategyError(
            f"strategy={strategy!r} is not one of the canonical pipelines: "
            f"{', '.join(CANONICAL_PIPELINE_STRATEGIES)}. "
            f"Hyphen form is used on the wire to admin "
            f"(e.g. strategy=agentic_flash → pipeline name 'agentic-flash')."
        )
    return strategy.replace("_", "-")


def _pipeline_ref_from_strategy(strategy: str):
    """Return a ``PipelineConfigRef(name=..., version='v1')`` for the
    canonical strategy. Raises :class:`InvalidPipelineStrategyError` on
    unknown values."""
    from kgspin_core.execution.pipeline_resolver_ref import PipelineConfigRef
    return PipelineConfigRef(name=_canonical_pipeline_name(strategy), version="v1")


def _pipeline_ref_from_pipeline_id(pipeline_id: str | None):
    """Build a ``PipelineConfigRef`` from an already-hyphenated pipeline
    config name (e.g. ``"fan-out"``). Defaults to ``"fan-out"`` when the
    caller doesn't specify one — this is the baseline zero-LLM pipeline
    the Compare tab always runs alongside the LLM slots.
    """
    from kgspin_core.execution.pipeline_resolver_ref import PipelineConfigRef
    return PipelineConfigRef(name=pipeline_id or "fan-out", version="v1")


_DEFAULT_CONFIDENCE_FLOOR = 0.55


def _resolve_confidence_floor(
    *, query_value: float, pipeline_name: str | None,
) -> float:
    """Sprint 12 Task 8: resolve confidence_floor per-request.

    Precedence:
    1. Valid caller-supplied ``query_value`` (not the sentinel -1.0).
    2. Admin pipeline_config param ``confidence_floor`` for
       ``pipeline_name``.
    3. Hardcoded fallback ``_DEFAULT_CONFIDENCE_FLOOR`` (0.55).

    Admin lookup graceful-degrades on any failure — operator never
    sees a 500 from a missing param.
    """
    # 1. Caller supplied an explicit value in valid range.
    if query_value != -1.0 and 0.0 <= query_value <= 1.0:
        return query_value

    # 2. Admin pipeline param.
    if pipeline_name:
        from kgspin_demo_app.services.admin_registry_reader import get_pipeline_params
        params = get_pipeline_params(
            _get_registry_client(), pipeline_name,
            defaults={"confidence_floor": _DEFAULT_CONFIDENCE_FLOOR},
        )
        candidate = params.get("confidence_floor", _DEFAULT_CONFIDENCE_FLOOR)
        if isinstance(candidate, (int, float)) and 0.0 <= float(candidate) <= 1.0:
            return float(candidate)

    # 3. Hardcoded fallback.
    return _DEFAULT_CONFIDENCE_FLOOR


def _admin_prompt_or_none(name: str, *, version: str | None = None) -> str | None:
    """Sprint 12 Task 7: optional admin-driven prompt lookup with logging.

    Returns the admin-registered prompt text if present + non-empty,
    else ``None`` so callers fall through to hardcoded prompts. One
    INFO log line per lookup so operators can see whether admin or
    the hardcoded path is in use during a demo.

    Sprint 12 wires this as infrastructure; only the
    ``kg-quality-comparison`` prompt exercises the call-site today.
    Remaining prompts (relationship-audit, qa-analysis,
    entity_system_prompt, entity_prompt_template) are Sprint 13
    migration work per the archetypes handover memo.
    """
    from kgspin_demo_app.services.admin_registry_reader import get_prompt_template_text
    text = get_prompt_template_text(
        _get_registry_client(), name, version=version, fallback="",
    )
    if text:
        logger.info("[PROMPT_TEMPLATE] using admin-registered prompt %r", name)
        return text
    logger.debug("[PROMPT_TEMPLATE] admin missing %r; falling back to hardcoded", name)
    return None


def _strategy_from_compare_args(strategy: str, pipeline_config_ref: str) -> str:
    """Resolve the canonical pipeline strategy from the endpoint args.

    Precedence: ``pipeline_config_ref`` > ``strategy``. Both carry the
    same canonical underscore form (``fan_out``, ``discovery_rapid``,
    ``discovery_deep``, ``agentic_flash``, ``agentic_analyst``) — the
    ``pipeline_config_ref`` arg no longer routes through admin for a
    resolved strategy name; callers hand us the canonical token directly
    and core looks up the YAML via ``PipelineConfigRef``.

    Raises :class:`InvalidPipelineStrategyError` if neither arg resolves
    to a canonical strategy. Empty inputs yield the same error — the
    endpoint decides whether to pass a default (e.g. ``fan_out``) before
    calling this.
    """
    candidate = (pipeline_config_ref or "").strip() or (strategy or "").strip()
    if not candidate:
        raise InvalidPipelineStrategyError(
            "No pipeline strategy supplied. Pass strategy=<canonical> or "
            "pipeline_config_ref=<canonical>, where <canonical> is one of: "
            f"{', '.join(CANONICAL_PIPELINE_STRATEGIES)}."
        )
    # Reject anything outside the 5; _canonical_pipeline_name does the work.
    _canonical_pipeline_name(candidate)
    return candidate


def _pipeline_id_from_compare_args(strategy: str, pipeline_config_ref: str) -> str | None:
    """Return the admin pipeline config name (hyphenated) for the request,
    or ``None`` when no strategy was supplied.

    Strict: any non-empty argument that isn't in the canonical 5 raises
    :class:`InvalidPipelineStrategyError`. Used by endpoints that treat
    ``strategy`` as optional (compare, refresh-discovery, slot-cache-check).
    """
    candidate = (pipeline_config_ref or "").strip() or (strategy or "").strip()
    if not candidate:
        return None
    return _canonical_pipeline_name(candidate)


@app.get("/api/compare/{doc_id}")
async def compare(doc_id: str, request: Request, force_refresh: int = 0, corpus_kb: int = DEFAULT_CORPUS_KB, chunk_size: int = DEFAULT_CHUNK_SIZE, model: str = DEFAULT_GEMINI_MODEL, bundle: str = "", strategy: str = "", pipeline_config_ref: str = "", confidence_floor: float = -1.0, llm_alias: str = ""):
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    alias = llm_alias.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied="model" in request.query_params,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    if chunk_size not in VALID_CHUNK_SIZES:
        chunk_size = DEFAULT_CHUNK_SIZE
    if model not in VALID_GEMINI_MODELS:
        model = DEFAULT_GEMINI_MODEL
    bundle_name = bundle if bundle else None
    # W3-D: canonical-5 whitelist on strategy / pipeline_config_ref;
    # empty is allowed (endpoint default). Anything else → 400.
    try:
        pipeline_id = _pipeline_id_from_compare_args(strategy, pipeline_config_ref)
    except InvalidPipelineStrategyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # Sprint 12 Task 8: confidence_floor precedence — query arg >
    # admin pipeline param > hardcoded fallback (0.55). Query arg of
    # -1.0 means "not supplied by caller" (0.55 is a valid operator
    # choice so we can't use it as the sentinel). Admin lookup uses
    # the resolved pipeline name; graceful-degrade to 0.55 on miss.
    confidence_floor = _resolve_confidence_floor(
        query_value=confidence_floor,
        pipeline_name=pipeline_id,
    )
    return StreamingResponse(
        run_comparison(ticker.upper(), request, force_refresh=bool(force_refresh), corpus_kb=corpus_kb, chunk_size=chunk_size, model=model, bundle_name=bundle_name, pipeline_id=pipeline_id, llm_alias=alias),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/refresh-agentic-flash/{doc_id}")
async def refresh_agentic_flash(doc_id: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, model: str = DEFAULT_GEMINI_MODEL, bundle: str = "", llm_alias: str = ""):
    """INIT-001 Sprint 04: Re-run only Agentic Flash (single-prompt LLM) for this ticker.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias. ``model`` is retained as deprecated compat; passing both is 400.
    """
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    alias = llm_alias.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied="model" in request.query_params,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    if model not in VALID_GEMINI_MODELS:
        model = DEFAULT_GEMINI_MODEL
    bundle_name = bundle if bundle else None
    return StreamingResponse(
        run_single_refresh(ticker.upper(), request, "gemini", corpus_kb, model=model, bundle_name=bundle_name, llm_alias=alias),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/refresh-agentic-analyst/{doc_id}")
async def refresh_agentic_analyst(doc_id: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, chunk_size: int = DEFAULT_CHUNK_SIZE, model: str = DEFAULT_GEMINI_MODEL, bundle: str = "", llm_alias: str = ""):
    """INIT-001 Sprint 04: Re-run only Agentic Analyst (schema-aware chunked LLM) for this ticker.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias. ``model`` is retained as deprecated compat; passing both is 400.
    """
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    alias = llm_alias.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied="model" in request.query_params,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    if chunk_size not in VALID_CHUNK_SIZES:
        chunk_size = DEFAULT_CHUNK_SIZE
    if model not in VALID_GEMINI_MODELS:
        model = DEFAULT_GEMINI_MODEL
    bundle_name = bundle if bundle else None
    return StreamingResponse(
        run_single_refresh(ticker.upper(), request, "modular", corpus_kb, chunk_size=chunk_size, model=model, bundle_name=bundle_name, llm_alias=alias),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/refresh-discovery/{doc_id}")
async def refresh_discovery(doc_id: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, bundle: str = "", strategy: str = "", pipeline_config_ref: str = ""):
    """Re-run a zero-token KGSpin pipeline (fan_out / discovery_rapid /
    discovery_deep). Accepts ``strategy=`` or ``pipeline_config_ref=``
    (both canonical form); anything outside the canonical 5 → 400.
    """
    ticker = doc_id  # Wave A wire-format shim
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    bundle_name = bundle if bundle else None
    try:
        pipeline_id = _pipeline_id_from_compare_args(strategy, pipeline_config_ref)
    except InvalidPipelineStrategyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return StreamingResponse(
        _run_kgen_refresh(ticker.upper(), request, corpus_kb, bundle_name=bundle_name, pipeline_id=pipeline_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/cancel-multistage/{doc_id}")
async def cancel_multistage(doc_id: str):
    """Sprint 33.6: Cancel a running Multi-Stage extraction and show partial results."""
    ticker = doc_id  # Wave A wire-format shim
    event = _modular_cancel_events.get(ticker.upper())
    if event:
        event.set()
        return JSONResponse({"status": "cancelled"})
    return JSONResponse({"status": "not_running"})


@app.get("/api/scores/{doc_id}")
async def get_scores(doc_id: str):
    """PRD-048: Lightweight endpoint to recompute Performance Delta scores from cached KGs."""
    ticker = doc_id  # Wave A wire-format shim
    ticker = ticker.upper()
    with _cache_lock:
        cached = dict(_kg_cache.get(ticker, {}))
    if not cached:
        return JSONResponse({"error": "No cached data"}, status_code=404)

    kgs_kg = cached.get("kgs_kg")
    mod_kg = cached.get("mod_kg")
    gem_kg = cached.get("gem_kg")
    if not kgs_kg:
        return JSONResponse({"error": "No KGSpin data"}, status_code=404)

    if not mod_kg and not gem_kg:
        return JSONResponse({"error": "No LLM data"}, status_code=404)

    company_name = cached.get("info", {}).get("name", "")
    # Sprint 90: Filter KGs so scores match what user sees in graph
    f_kgs = filter_kg_for_display(kgs_kg)
    f_mod = filter_kg_for_display(mod_kg) if mod_kg else None
    f_gem = filter_kg_for_display(gem_kg) if gem_kg else None
    scores = compute_diagnostic_scores(f_kgs, mod_kg=f_mod, gem_kg=f_gem, company_name=company_name)
    return JSONResponse(scores)


@app.post("/api/refresh-analysis/{doc_id}")
async def refresh_analysis(doc_id: str, request: Request):
    """Sprint 33.11: Re-run quality analysis using whatever KGs are currently cached.

    Stage 0.5.4 (ADR-002): accepts optional ``llm_alias`` / ``model`` in
    the JSON body (backwards compatible — an empty body still works).
    """
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    ticker = ticker.upper()
    llm_alias = None
    legacy_model = None
    try:
        body = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    except Exception:
        body = {}
    model_supplied = False
    if isinstance(body, dict):
        llm_alias = (body.get("llm_alias") or "").strip() or None
        legacy_model = (body.get("model") or "").strip() or None
        model_supplied = "model" in body
    try:
        check_endpoint_llm_params(
            llm_alias=llm_alias,
            model_supplied=model_supplied,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with _cache_lock:
        cached = dict(_kg_cache.get(ticker, {}))
    if not cached:
        return JSONResponse({"error": "No cached data"}, status_code=404)

    kgs_kg = cached.get("kgs_kg")
    gem_kg = cached.get("gem_kg")
    mod_kg = cached.get("mod_kg")
    gem_tokens = cached.get("gem_tokens", 0)
    mod_tokens = cached.get("mod_tokens", 0)

    if not kgs_kg:
        return JSONResponse({"error": "No KGSpin data available"}, status_code=404)
    if not gem_kg and not mod_kg:
        return JSONResponse({"error": "No LLM data available for comparison"}, status_code=404)

    # Sprint 90: Filter KGs so analysis/scores match what user sees in graph
    f_kgs = filter_kg_for_display(kgs_kg)
    f_mod = filter_kg_for_display(mod_kg) if mod_kg else None
    f_gem = filter_kg_for_display(gem_kg) if gem_kg else None
    company_name = cached.get("info", {}).get("name", "")
    scores = compute_diagnostic_scores(f_kgs, mod_kg=f_mod, gem_kg=f_gem, company_name=company_name)

    result = await asyncio.to_thread(
        functools.partial(
            run_quality_analysis,
            f_kgs, f_gem or {}, gem_tokens, f_mod, mod_tokens,
            cached.get("kgs_stats"), cached.get("gem_stats"), cached.get("mod_stats"),
            llm_alias=llm_alias,
            legacy_model=legacy_model,
        )
    )
    return JSONResponse({
        "analysis": result["analysis"],
        "tokens": result["tokens"],
        "scores": scores,
    })


# Slot pipeline key → _kg_cache field mapping
_SLOT_PIPELINE_TO_CACHE_KEY = {
    "kgspin-default": "kgs_kg",
    "kgspin-emergent": "kgs_kg",
    "kgspin-structural": "kgs_kg",
    "fullshot": "gem_kg",
    "multistage": "mod_kg",
}

_SLOT_PIPELINE_LABELS = {
    "kgspin-default": "KGSpin Base",
    "kgspin-emergent": "KGSpin Emergent",
    "kgspin-structural": "KGSpin Structural",
    "fullshot": "LLM Full Shot",
    "multistage": "LLM Multi-Stage",
}


@app.post("/api/compare-qa/{doc_id}")
async def compare_qa(doc_id: str, request: Request):
    """Sprint 91: Compare Q&A across slot-loaded graphs.

    Request body: {graphs: [{pipeline, bundle, slot_index}], domain: str,
                   llm_alias?: str, model?: str}
    Response: {results: [{question, answers: [{answer, tokens}]}], analysis: str}

    Stage 0.5.4 (ADR-002): body accepts ``llm_alias`` (preferred) or the
    deprecated ``model`` string. Passing both is a 400 error.
    """
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    ticker = ticker.upper()
    body = await request.json()
    graphs = body.get("graphs", [])
    domain = body.get("domain", "financial")
    llm_alias = (body.get("llm_alias") or "").strip() or None
    legacy_model = (body.get("model") or "").strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=llm_alias,
            model_supplied="model" in body,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if len(graphs) < 2:
        return JSONResponse({"error": "At least 2 graphs required"}, status_code=400)

    # Look up cached KGs for each graph
    with _cache_lock:
        cached = dict(_kg_cache.get(ticker, {}))
    if not cached:
        return JSONResponse({"error": "No cached data for this ticker"}, status_code=404)

    # Build KG context string for each graph
    graph_contexts = []
    for g in graphs:
        pipeline = g.get("pipeline", "")
        cache_key = _SLOT_PIPELINE_TO_CACHE_KEY.get(pipeline)
        if not cache_key:
            return JSONResponse({"error": f"Unknown pipeline: {pipeline}"}, status_code=400)
        kg = cached.get(cache_key)
        if not kg:
            label = _SLOT_PIPELINE_LABELS.get(pipeline, pipeline)
            return JSONResponse({"error": f"No cached KG for {label}"}, status_code=404)
        graph_contexts.append({
            "pipeline": pipeline,
            "label": _SLOT_PIPELINE_LABELS.get(pipeline, pipeline),
            "bundle": g.get("bundle", ""),
            "context": _build_kg_context_string(kg),
        })

    # Get domain-specific questions
    questions = IMPACT_QUESTIONS.get(domain, IMPACT_QUESTIONS.get("financial", []))

    # Domain-specific analyst role
    domain_roles = {
        "financial": "financial analyst",
        "clinical": "clinical research analyst",
    }
    analyst_role = domain_roles.get(domain, "analyst")

    # Ask each question against each graph's KG context
    def _run_compare_qa():
        from kgspin_demo_app.llm_backend import resolve_llm_backend
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="compare_qa",
        )
        results = []

        for q in questions:
            answers = []
            for gc in graph_contexts:
                prompt = (
                    f"You are a {analyst_role} answering questions using a structured knowledge graph.\n\n"
                    f"## Context (Knowledge Graph from {gc['label']})\n{gc['context']}\n\n"
                    f"## Question\n{q}\n\n"
                    f"Provide a clear, factual answer based only on the knowledge graph above. "
                    f"If the graph does not contain enough information, say so."
                )
                try:
                    result = backend.complete(prompt)
                    answers.append({
                        "answer": result.text.strip(),
                        "tokens": result.tokens_used,
                        "pipeline": gc["pipeline"],
                        "label": gc["label"],
                    })
                except Exception as e:
                    answers.append({
                        "answer": f"Error: {e}",
                        "tokens": 0,
                        "pipeline": gc["pipeline"],
                        "label": gc["label"],
                    })
                # Rate limit between calls
                time.sleep(1.5)

            results.append({"question": q, "answers": answers})
            # Rate limit between questions
            time.sleep(1.0)

        # Build comparative analysis prompt
        analysis_text = ""
        if len(results) > 0:
            comparison_lines = []
            for r in results:
                comparison_lines.append(f"Q: {r['question']}")
                for a in r["answers"]:
                    comparison_lines.append(f"  [{a['label']}]: {a['answer'][:500]}")
            comparison_block = "\n".join(comparison_lines)

            analysis_prompt = (
                f"You are evaluating the quality of answers produced by different knowledge graph extraction pipelines.\n\n"
                f"Below are answers to the same questions from different pipelines:\n\n"
                f"{comparison_block}\n\n"
                f"Provide a brief qualitative comparison (3-5 sentences) of which pipeline(s) produced "
                f"more complete, accurate, and useful answers. Note any questions where pipelines disagreed "
                f"or where one pipeline clearly had more information."
            )
            try:
                analysis_result = backend.complete(analysis_prompt)
                analysis_text = analysis_result.text.strip()
            except Exception as e:
                analysis_text = f"Analysis failed: {e}"

        return {"results": results, "analysis": analysis_text}

    try:
        result = await asyncio.to_thread(_run_compare_qa)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Compare Q&A failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/intelligence/{doc_id}")
async def intelligence(doc_id: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, model: str = DEFAULT_GEMINI_MODEL, domain: str = "financial", llm_alias: str = ""):
    """Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered
    alias; ``model`` stays as deprecated compat. Passing both returns 400.
    """
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    alias = llm_alias.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied="model" in request.query_params,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    if model not in VALID_GEMINI_MODELS:
        model = DEFAULT_GEMINI_MODEL
    return StreamingResponse(
        run_intelligence(ticker.upper(), request, corpus_kb=corpus_kb, model=model, domain=domain, llm_alias=alias),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/refresh-intel/{doc_id}")
async def refresh_intel(doc_id: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, model: str = DEFAULT_GEMINI_MODEL, llm_alias: str = ""):
    """Sprint 33.17: Re-run Intelligence pipeline for this ticker.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias; ``model`` retained as deprecated compat. Passing both is 400.
    """
    ticker = doc_id  # Wave A wire-format shim
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    alias = llm_alias.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied="model" in request.query_params,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    if model not in VALID_GEMINI_MODELS:
        model = DEFAULT_GEMINI_MODEL
    return StreamingResponse(
        run_intelligence(ticker.upper(), request, corpus_kb=corpus_kb, model=model, llm_alias=alias),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/model-pricing")
async def model_pricing():
    """Sprint 33.18: Return model pricing table for frontend cost calculations."""
    return JSONResponse(GEMINI_MODEL_PRICING)


@app.get("/api/multihop/scenarios")
async def multihop_scenarios():
    """PRD-004 v4 #9: return the multi-hop scenario pack for the picker."""
    from demos.extraction.scenarios import load_scenarios, scenario_to_dict
    return JSONResponse({
        "scenarios": [scenario_to_dict(s) for s in load_scenarios()],
    })


# Pipeline -> _kg_cache field. The KG-side strategies (fan_out and the
# discovery_* variants) all land in ``kgs_kg``; the LLM-side ones land in
# ``gem_kg`` (agentic_flash) and ``mod_kg`` (agentic_analyst).
_MULTIHOP_PIPELINE_TO_CACHE_KEY = {
    "fan_out": "kgs_kg",
    "discovery_rapid": "kgs_kg",
    "discovery_deep": "kgs_kg",
    "agentic_flash": "gem_kg",
    "agentic_analyst": "mod_kg",
}

# Pipelines whose answers we score via a micro-graph built from the answer
# text itself (PRD-055 #3 "LLM-side score"). KG-side pipelines instead score
# the cached extraction directly.
_MULTIHOP_LLM_PIPELINES = {"agentic_flash", "agentic_analyst"}

_MULTIHOP_JUDGE_ALIAS = "gemini-flash-2.5"
_MULTIHOP_ANSWER_ALIAS = "gemini-flash-2.5"
_MULTIHOP_PER_CALL_TIMEOUT_S = 60.0


def _multihop_cost_usd(model: str, tokens_used: int) -> float:
    """Best-effort cost estimate. We don't break input vs. output here so
    we apply the more conservative output rate; the badge is indicative,
    not invoice-grade. Returns 0.0 for unknown / preview models."""
    pricing = GEMINI_MODEL_PRICING.get(model or "", {})
    rate = pricing.get("output", 0.0)
    if not rate or not tokens_used:
        return 0.0
    return round((tokens_used / 1_000_000.0) * rate, 6)


@app.post("/api/multihop/run")
async def multihop_run(request: Request):
    """PRD-004 v4 #10: parallel fan-out across three slot pipelines for a
    single multi-hop scenario, with topology-health scoring per answer
    and a blinded LLM-as-judge ranking on top.

    Body: ``{doc_id, scenario_id, slot_pipelines: [name, name, name]}``.
    Pipelines must be canonical strategy names (``fan_out``,
    ``agentic_flash``, ``agentic_analyst``, etc). The three answer tasks
    run via ``asyncio.gather`` — deliberately no inter-call sleep
    because the demo narrative depends on parallel dispatch. Per-call
    timeout (``_MULTIHOP_PER_CALL_TIMEOUT_S``) prevents one hung pipeline
    from blocking the others.
    """
    from demos.extraction.judge import JudgeParseError, rank_answers
    from demos.extraction.scenarios import get_scenario, scenario_to_dict
    from kgspin_demo_app.services.micrograph import build_micrograph_from_answer
    from kgspin_demo_app.services.topology_health import health_for_kg

    body = await request.json()
    doc_id = (body.get("doc_id") or "").strip()
    scenario_id = (body.get("scenario_id") or "").strip()
    slot_pipelines = body.get("slot_pipelines") or []

    if not doc_id:
        return JSONResponse({"error": "doc_id required"}, status_code=400)
    if not scenario_id:
        return JSONResponse({"error": "scenario_id required"}, status_code=400)
    if not isinstance(slot_pipelines, list) or len(slot_pipelines) != 3:
        return JSONResponse(
            {"error": "slot_pipelines must be a list of exactly 3 pipeline names"},
            status_code=400,
        )
    unknown = [p for p in slot_pipelines if p not in _MULTIHOP_PIPELINE_TO_CACHE_KEY]
    if unknown:
        return JSONResponse(
            {
                "error": f"unknown pipeline(s): {unknown}; allowed: "
                f"{sorted(_MULTIHOP_PIPELINE_TO_CACHE_KEY)}"
            },
            status_code=400,
        )

    try:
        scenario = get_scenario(scenario_id)
    except KeyError:
        return JSONResponse(
            {"error": f"unknown scenario: {scenario_id!r}"}, status_code=404
        )

    ticker = doc_id.upper()
    with _cache_lock:
        cached = dict(_kg_cache.get(ticker, {}))
    if not cached:
        return JSONResponse(
            {"error": f"no cached data for {ticker}; run an extraction first"},
            status_code=404,
        )

    domain_role = "clinical research analyst" if scenario.domain == "clinical" else "financial analyst"

    def _run_one(pipeline_name: str) -> dict:
        cache_key = _MULTIHOP_PIPELINE_TO_CACHE_KEY[pipeline_name]
        kg = cached.get(cache_key)
        if not kg:
            return {
                "pipeline": pipeline_name,
                "answer_text": None,
                "error": f"no cached KG for {pipeline_name}; rerun the slot first",
                "latency_ms": 0,
                "cost_usd": 0.0,
                "tokens_used": 0,
                "topology_health": None,
            }
        kg_context = _build_kg_context_string(kg)
        prompt = (
            f"You are a {domain_role} answering a multi-hop question using a "
            f"structured knowledge graph.\n\n"
            f"## Knowledge Graph Context\n{kg_context}\n\n"
            f"## Question\n{scenario.question}\n\n"
            f"Answer factually using only the knowledge graph above. "
            f"If the graph does not contain enough information to answer, "
            f"state precisely what is missing."
        )
        from kgspin_demo_app.llm_backend import resolve_llm_backend

        try:
            backend = resolve_llm_backend(
                llm_alias=_MULTIHOP_ANSWER_ALIAS, flow="multihop_run"
            )
            t0 = time.perf_counter()
            result = backend.complete(prompt)
            latency_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            return {
                "pipeline": pipeline_name,
                "answer_text": None,
                "error": f"answer call failed: {type(e).__name__}: {e}",
                "latency_ms": 0,
                "cost_usd": 0.0,
                "tokens_used": 0,
                "topology_health": None,
            }
        text = (result.text or "").strip()
        tokens = int(getattr(result, "tokens_used", 0) or 0)
        model_used = getattr(result, "model", "") or ""
        if pipeline_name in _MULTIHOP_LLM_PIPELINES:
            topology_health = health_for_kg(build_micrograph_from_answer(text))
        else:
            topology_health = health_for_kg(kg)
        return {
            "pipeline": pipeline_name,
            "answer_text": text,
            "latency_ms": latency_ms,
            "cost_usd": _multihop_cost_usd(model_used, tokens),
            "tokens_used": tokens,
            "topology_health": topology_health,
        }

    async def _run_one_with_timeout(pipeline_name: str) -> dict:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_one, pipeline_name),
                timeout=_MULTIHOP_PER_CALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return {
                "pipeline": pipeline_name,
                "answer_text": None,
                "error": f"timed out after {_MULTIHOP_PER_CALL_TIMEOUT_S:.0f}s",
                "latency_ms": int(_MULTIHOP_PER_CALL_TIMEOUT_S * 1000),
                "cost_usd": 0.0,
                "tokens_used": 0,
                "topology_health": None,
            }

    answers = await asyncio.gather(
        *(_run_one_with_timeout(p) for p in slot_pipelines)
    )

    answer_texts = [a.get("answer_text") for a in answers]
    valid_count = sum(1 for t in answer_texts if t)
    if valid_count == 3:
        try:
            verdict = await asyncio.to_thread(
                rank_answers, scenario.question, answer_texts
            )
            judge = verdict.to_dict()
        except JudgeParseError as e:
            judge = {"error": f"judge parse failed: {e}"}
        except Exception as e:
            judge = {"error": f"judge call failed: {type(e).__name__}: {e}"}
    else:
        judge = {
            "error": f"only {valid_count}/3 valid answers; judge skipped",
        }

    return JSONResponse({
        "scenario": scenario_to_dict(scenario),
        "answers": answers,
        "judge": judge,
    })


@app.get("/api/topology-health/{doc_id}/{pipeline}")
async def topology_health(doc_id: str, pipeline: str):
    """PRD-055 #1: stateless score over a slot's cached KG.

    Returns the same shape as ``health_for_kg`` (TopologicalHealth dict
    or sentinel). Slot panels call this on render to populate the
    badge; the multi-hop endpoint computes the same score inline.
    """
    from kgspin_demo_app.services.topology_health import health_for_kg

    cache_key = _MULTIHOP_PIPELINE_TO_CACHE_KEY.get(pipeline) or _SLOT_PIPELINE_TO_CACHE_KEY.get(pipeline)
    if not cache_key:
        return JSONResponse(
            {"error": f"unknown pipeline: {pipeline}"}, status_code=400
        )
    ticker = doc_id.upper()
    with _cache_lock:
        cached = dict(_kg_cache.get(ticker, {}))
    kg = cached.get(cache_key) if cached else None
    return JSONResponse(health_for_kg(kg))


@app.get("/api/impact/{doc_id}")
async def impact(
    doc_id: str, request: Request,
    llm_alias: str = "",
    model: str = "",
):
    """Impact analysis SSE endpoint.

    Stage 0.5.4 (ADR-002): ``llm_alias`` query param selects an
    admin-registered alias; ``model`` is deprecated compat. Passing both
    returns 400.
    """
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    alias = llm_alias.strip() or None
    legacy_model = model.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=alias,
            model_supplied=legacy_model is not None,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return StreamingResponse(
        run_impact(ticker.upper(), request, llm_alias=alias, legacy_model=legacy_model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/why-this-matters/{doc_id}")
async def why_this_matters(
    doc_id: str,
    domain: str = "financial",
    question: str = "",
    pipeline: str = "",
    llm_alias: str = "",
    model: str = "",
):
    """Sprint 155: 'Why This Matters' — user-editable question comparing KG vs raw text.

    Uses the best available cached KG for this ticker. The question can be
    provided via the `question` query parameter (from the UI text input).
    Falls back to a domain-appropriate default if empty.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias. ``model`` is the deprecated Gemini-only compat path; passing
    both returns 400.
    """
    from kgspin_demo_app.llm_backend import LLMParamsError, check_endpoint_llm_params

    llm_alias = llm_alias.strip() or None
    legacy_model = model.strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=llm_alias,
            model_supplied=legacy_model is not None,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    ticker = ticker.upper()
    is_clinical = domain == "clinical" or ticker.startswith("NCT")
    question_domain = "clinical" if is_clinical else "financial"
    if not question.strip():
        question = WTM_QUESTIONS.get(question_domain, WTM_QUESTIONS["financial"])

    with _cache_lock:
        cached = _kg_cache.get(ticker)

    if not cached or "text" not in cached:
        return {"error": "No cached data for doc_id. Load a graph first.", "doc_id": ticker}

    # Sprint 05 HITL-round-2 fix: when the modal asks for a specific pipeline,
    # use that slot's KG instead of the best-available fallback. Also fixes a
    # pre-existing bug where the fallback list referenced nonexistent cache
    # keys ("fullshot_kg", "multistage_kg") instead of the actual storage
    # keys ("gem_kg", "mod_kg"), meaning the LLM fallback was effectively dead code.
    _pipeline_to_cache_key = {
        "kgenskills": "kgs_kg",
        "gemini": "gem_kg",
        "modular": "mod_kg",
    }
    _pipeline_to_label = {
        "kgenskills": "kgspin",
        "gemini": "agentic_flash",
        "modular": "agentic_analyst",
    }

    best_kg = None
    best_label = None
    if pipeline and pipeline in _pipeline_to_cache_key:
        key = _pipeline_to_cache_key[pipeline]
        if key in cached and cached[key]:
            best_kg = cached[key]
            best_label = _pipeline_to_label[pipeline]
    if not best_kg:
        # Fallback: best available (kgspin → agentic_flash → agentic_analyst)
        for key in ("kgs_kg", "gem_kg", "mod_kg"):
            if key in cached and cached[key]:
                best_kg = cached[key]
                best_label = {
                    "kgs_kg": "kgspin",
                    "gem_kg": "agentic_flash",
                    "mod_kg": "agentic_analyst",
                }[key]
                break

    if not best_kg:
        return {"error": "No graph available yet. Run at least one pipeline first.", "doc_id": ticker}

    # Check Gemini availability
    gemini_available = bool(
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY")
    )
    if not gemini_available:
        return {"error": "GEMINI_API_KEY not set.", "doc_id": ticker}

    kg_context = _build_kg_context_string(best_kg)
    raw_context = cached["text"][:30000]

    def _ask_wtm():
        from kgspin_demo_app.llm_backend import resolve_llm_backend
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="wtm",
        )

        prompt_with = (
            f"You are a financial analyst answering questions using a structured knowledge graph "
            f"extracted from an SEC filing.\n\n"
            f"## Context (Knowledge Graph)\n{kg_context}\n\n"
            f"## Context (Document Text)\n{raw_context[:10000]}\n\n"
            f"{_DUAL_RESPONSE_FORMAT}\n"
            f"## Question\n{question}"
        )
        t0 = time.time()
        result_with = backend.complete(prompt_with)
        time_with_ms = int((time.time() - t0) * 1000)

        time.sleep(1.5)

        prompt_without = (
            f"You are a financial analyst answering questions using only a raw text document.\n\n"
            f"## Context (Document Text)\n{raw_context}\n\n"
            f"{_DUAL_RESPONSE_FORMAT}\n"
            f"## Question\n{question}"
        )
        t1 = time.time()
        result_without = backend.complete(prompt_without)
        time_without_ms = int((time.time() - t1) * 1000)

        parsed_with = _parse_dual_response(result_with.text)
        parsed_without = _parse_dual_response(result_without.text)

        return {
            "question": question,
            "with_graph": parsed_with["text"],
            "with_graph_json": parsed_with["json"],
            "without_graph": parsed_without["text"],
            "without_graph_json": parsed_without["json"],
            "tokens_with": result_with.tokens_used,
            "tokens_without": result_without.tokens_used,
            "time_with_ms": time_with_ms,
            "time_without_ms": time_without_ms,
            "graph_source": best_label,
        }

    import asyncio
    result = await asyncio.to_thread(_ask_wtm)
    return result


@app.get("/api/impact/lineage/{doc_id}")
async def lineage_data(doc_id: str, domain: str = "financial", pipeline: str = "kgenskills"):
    """Return KG vis data + source text + full evidence index for lineage exploration.

    Sprint 05 HITL-round-2 fix: accept a ``pipeline`` query param so the
    full-screen modal can show lineage for the slot's ACTUAL pipeline
    (kgenskills / gemini / modular), not the hardcoded KGSpin graph.
    Defaults to ``kgenskills`` to preserve the Impact tab's old behavior.
    """
    ticker = doc_id  # Wave A wire-format shim
    ticker = ticker.upper()
    pipeline_to_cache_key = {
        "kgenskills": "kgs_kg",
        "gemini": "gem_kg",
        "modular": "mod_kg",
    }
    cache_key = pipeline_to_cache_key.get(pipeline, "kgs_kg")
    with _cache_lock:
        cached = _kg_cache.get(ticker)
    if not cached or cache_key not in cached:
        cached = await _warm_cache_from_disk(ticker, domain=domain)
    if not cached or cache_key not in cached:
        return JSONResponse(
            {"error": f"No {pipeline} extraction data found for {ticker}. Run the pipeline first."},
            status_code=404,
        )

    # If cache entry exists but has no source text, warm from disk to get it
    source_text = cached.get("text", "")
    if not source_text:
        warmed = await _warm_cache_from_disk(ticker, domain=domain)
        if warmed:
            source_text = warmed.get("text", "")
            # Merge text back into the live cache so future calls don't re-warm
            with _cache_lock:
                if ticker in _kg_cache:
                    _kg_cache[ticker]["text"] = source_text
                    if warmed.get("raw_html"):
                        _kg_cache[ticker]["raw_html"] = warmed["raw_html"]
                    if warmed.get("info"):
                        _kg_cache[ticker].setdefault("info", warmed["info"])

    kg = cached[cache_key]
    vis_data = build_vis_data(kg)

    # Build evidence index with FULL sentence text (not truncated like build_vis_data)
    evidence_index = []
    for rel in kg.get("relationships", []):
        ev = rel.get("evidence", {})
        if not isinstance(ev, dict):
            continue
        evidence_index.append({
            "subject": rel.get("subject", {}).get("text", ""),
            "predicate": rel.get("predicate", ""),
            "object": rel.get("object", {}).get("text", ""),
            "confidence": rel.get("confidence", 0),
            "extraction_method": rel.get("extraction_method", ""),
            "rationale_code": rel.get("rationale_code", ""),
            "fingerprint_similarity": rel.get("fingerprint_similarity"),
            "sentence_text": ev.get("sentence_text", ""),
            "chunk_id": ev.get("chunk_id", ""),
            "sentence_index": ev.get("sentence_index", -1),
        })

    total = len(kg.get("relationships", []))
    traced = sum(1 for e in evidence_index if e["sentence_text"])
    methods = sorted({e["extraction_method"] for e in evidence_index if e["extraction_method"]})

    return JSONResponse({
        "vis": vis_data,
        "source_text": source_text,
        "evidence_index": evidence_index,
        "auditability_index": round(traced / max(total, 1) * 100, 1),
        "total_edges": total,
        "traced_edges": traced,
        "extraction_methods": methods,
    })


@app.get("/api/impact/reproducibility/{doc_id}")
async def reproducibility_benchmark(
    doc_id: str, corpus_kb: int = DEFAULT_CORPUS_KB,
    model: str = DEFAULT_GEMINI_MODEL, chunk_size: int = DEFAULT_CHUNK_SIZE,
):
    """Compute reproducibility variance from logged runs (no LLM calls)."""
    ticker = ticker.upper()

    # Compute config keys matching run_comparison() — must use same imports and
    # hash computation as lines 1679-1701 to produce identical config keys.
    bundle = _get_bundle()
    _bv = _bundle_id()
    kgen_cfg = _kgen_run_log.config_key("kgen", corpus_kb=corpus_kb, bid=_bv)

    # INIT-001 Sprint 03: prompt-version hashes previously derived from
    # demo-owned GeminiKGExtractor / GeminiAlignedExtractor `_build_system_prompt`.
    # Post-INIT-004 those live in kgspin_core.strategies.* with no prompt-preview
    # API. Fall back to a strategy-name tag; cache hits will be coarser (prompt
    # changes in core won't invalidate until the cache version bumps) but the
    # function still produces deterministic keys for the compare UI.
    _gem_pv = "llm_full_shot"
    gem_cfg = _run_log.config_key(
        "gemini", corpus_kb=corpus_kb, model=model, pv=_gem_pv, cv=DEMO_CACHE_VERSION,
    )

    _mod_pv = "llm_multi_stage"
    mod_cfg = _modular_run_log.config_key(
        "modular", corpus_kb=corpus_kb,
        model=model, pv=_mod_pv, cv=DEMO_CACHE_VERSION,
    )

    # Company name for normalization
    company_name = ticker
    with _cache_lock:
        cached = _kg_cache.get(ticker)
    if cached and "info" in cached:
        company_name = cached["info"].get("name", ticker)

    # Load up to 5 runs from each log
    max_runs = 5
    kgen_runs = []
    for i in range(min(max_runs, _kgen_run_log.count(ticker, kgen_cfg))):
        run = _kgen_run_log.load_run(ticker, kgen_cfg, i)
        if run:
            kgen_runs.append(run)

    gem_runs = []
    for i in range(min(max_runs, _run_log.count(ticker, gem_cfg))):
        run = _run_log.load_run(ticker, gem_cfg, i)
        if run:
            gem_runs.append(run)

    mod_runs = []
    for i in range(min(max_runs, _modular_run_log.count(ticker, mod_cfg))):
        run = _modular_run_log.load_run(ticker, mod_cfg, i)
        if run:
            mod_runs.append(run)

    kgen_result = _compute_run_variance(kgen_runs, company_name)
    kgen_result["deterministic"] = True  # KGSpin is deterministic by design
    gem_result = _compute_run_variance(gem_runs, company_name)
    mod_result = _compute_run_variance(mod_runs, company_name)

    needs_more = (gem_result.get("insufficient", True) and mod_result.get("insufficient", True))

    return JSONResponse({
        "kgen": kgen_result,
        "fullshot": gem_result,
        "modular": mod_result,
        "needs_more_runs": needs_more,
    })


# --- Slot Cache Check (Sprint 91) ---


@app.get("/api/slot-cache-check/{doc_id}")
async def slot_cache_check(doc_id: str, pipeline: str = "", bundle: str = "", strategy: str = "", pipeline_config_ref: str = ""):
    """Check if a cached run exists for a pipeline+bundle combo and return it.

    Sprint 12 Task 5: accepts ``pipeline_config_ref`` (admin resource
    id) alongside the legacy ``strategy`` arg.

    Returns {cached: true, vis, stats, total_runs, run_index} or {cached: false}.
    """
    ticker = doc_id  # Wave A wire-format shim
    ticker = ticker.upper()
    if not pipeline:
        return JSONResponse({"cached": False})

    # Map frontend pipeline names to cache pipeline keys
    pipeline_map = {
        "kgenskills": "kgen",
        "gemini": "gemini",
        "modular": "modular",
    }
    cache_pipeline = pipeline_map.get(pipeline)
    if not cache_pipeline:
        return JSONResponse({"cached": False})

    # W3-D: canonical 5-pipeline whitelist; anything else → 400.
    try:
        pipeline_id = _pipeline_id_from_compare_args(strategy, pipeline_config_ref)
    except InvalidPipelineStrategyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # Build the cache config key
    # Sprint 121: Require pipeline_id to consider this a "split" bundle — matches
    # run_comparison() logic.  Without pipeline_id (e.g. LLM Full Shot), the run
    # was saved WITHOUT dom= in the key, so the lookup must also omit dom=.
    _slot_is_split = bundle and pipeline_id and DOMAIN_BUNDLES_DIR.is_dir() and (DOMAIN_BUNDLES_DIR / bundle).is_dir()
    if _slot_is_split and pipeline_id:
        bundle_name = _split_bundle_id(bundle, pipeline_id)
    else:
        bundle_name = bundle if bundle else BUNDLE_PATH.name
    bundle_path = BUNDLE_PATH
    _patterns = PATTERNS_PATH
    # Sprint 118: Use domain-specific paths for split bundles
    if _slot_is_split:
        try:
            bundle_path = resolve_domain_bundle_path(bundle)
            _patterns = resolve_domain_yaml_path(bundle)
        except FileNotFoundError:
            pass
    elif bundle:
        bundle_path = resolve_bundle_path(bundle)
    # For clinical domain, use clinical patterns
    if ticker.startswith("NCT"):
        _patterns = resolve_domain_yaml_path("clinical")
        if not bundle:
            bundle_name = "clinical-v2"
            bundle_path = resolve_bundle_path(bundle_name)

    corpus_kb = DEFAULT_CORPUS_KB
    # Check if we have a cached corpus size from the in-memory cache
    with _cache_lock:
        cached_entry = _kg_cache.get(ticker)
    if cached_entry:
        corpus_kb = cached_entry.get("corpus_kb", DEFAULT_CORPUS_KB)

    cfg_keys = _build_pipeline_cache_keys(
        bundle_path, _patterns, corpus_kb, DEFAULT_GEMINI_MODEL,
        bundle_name=bundle_name, domain_id=bundle if _slot_is_split else "",
        pipeline_id=pipeline_id,
    )
    cfg_key = cfg_keys.get(cache_pipeline, "")
    if not cfg_key:
        return JSONResponse({"cached": False})

    is_cached, logged_run = _cache_lookup(cache_pipeline, ticker, cfg_key)
    if not is_cached or not logged_run:
        return JSONResponse({"cached": False})

    kg = logged_run.get("kg", {})
    vis = build_vis_data(kg)
    run_log = _PIPELINE_LOGS[cache_pipeline]
    total_runs = run_log.count(ticker, cfg_key)

    # Populate in-memory cache so Q&A and analysis can find this KG
    kg_field = {"kgen": "kgs_kg", "gemini": "gem_kg", "modular": "mod_kg"}.get(cache_pipeline)
    if kg_field:
        with _cache_lock:
            if ticker not in _kg_cache:
                _kg_cache[ticker] = {}
            _kg_cache[ticker][kg_field] = kg

    elapsed_s = logged_run.get("elapsed_seconds", 0)
    tokens = logged_run.get("total_tokens", 0)
    # corpus_kb may be missing from older KG provenance — fall back to in-memory cache
    text_kb = kg.get("provenance", {}).get("corpus_kb", 0)
    if not text_kb and cached_entry:
        text_kb = cached_entry.get("actual_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None

    # Chunk size for KGSpin estimates
    chunk_size = (cached_entry or {}).get("chunk_size", DEFAULT_CHUNK_SIZE)

    stats = {
        "entities": len(vis["nodes"]),
        "relationships": len(vis["edges"]),
        "tokens": tokens,
        "duration_ms": int(elapsed_s * 1000),
        "throughput_kb_sec": round(throughput, 1) if throughput else None,
        "actual_kb": round(text_kb, 1) if text_kb else None,
    }

    # KGSpin-specific stats
    if cache_pipeline == "kgen":
        cpu_cost = (elapsed_s / 3600) * _CPU_COST_PER_HOUR
        est_chunks = max(1, round(text_kb * 1024 / chunk_size)) if text_kb > 0 else 0
        stats["cpu_cost"] = round(cpu_cost, 6)
        stats["num_chunks"] = est_chunks

    # LLM-specific: carry forward error count and chunk info
    if cache_pipeline in ("gemini", "modular"):
        stats["errors"] = logged_run.get("error_count", 0)
        if cache_pipeline == "modular":
            stats["chunks_total"] = logged_run.get("chunks_total", 0)

    return JSONResponse({
        "cached": True,
        "vis": vis,
        "kg": kg,
        "stats": stats,
        "total_runs": total_runs,
        "run_index": 0,
        "created_at": logged_run.get("created_at", ""),
        "bundle_version": logged_run.get("bundle_version", ""),
    })


# --- Intelligence Run History (Sprint 33.17) ---


# --- Cache Management (Sprint 33.9) ---


@app.post("/api/purge-cache")
async def purge_cache(request: Request):
    """Sprint 33.17: Selective cache purging with per-layer checkboxes."""
    import shutil
    try:
        body = await request.json()
    except Exception:
        body = {}
    layers = body.get("layers", ["gemini", "modular", "kgen", "intel", "impact_qa", "memory"])

    layer_map = {
        "gemini": GeminiRunLog.LOG_ROOT,
        "modular": ModularRunLog.LOG_ROOT,
        "kgen": KGenRunLog.LOG_ROOT,
        "intel": IntelRunLog.LOG_ROOT,
        "impact_qa": ImpactQARunLog.LOG_ROOT,
    }
    purged = []
    for layer in layers:
        if layer == "memory":
            global _bundle_predicates_cache
            _kg_cache.clear()
            _bundle_resolve.purge_caches()
            _bundle_predicates_cache = None
            purged.append("in-memory cache cleared (including bundle)")
        elif layer in layer_map:
            root = layer_map[layer]
            if root.exists():
                count = sum(1 for _ in root.rglob("*.json"))
                shutil.rmtree(root)
                purged.append(f"{layer}: {count} files")
    return {"purged": purged}


# --- Impact Q&A Run History ---


# --- HITL Feedback System (Sprint 39, PRD-042) ---

_feedback_store = None


def _get_feedback_store():
    global _feedback_store
    if _feedback_store is None:
        # Canonical home post-carve is ``kgspin_tuner.feedback.store``.
        # The old ``kgenskills.feedback.store`` import survived the
        # rename and crashed on first /api/feedback/* call.
        from kgspin_tuner.feedback.store import create_feedback_store
        _feedback_store = create_feedback_store()
    return _feedback_store


_bundle_predicates_cache = None


def _get_bundle_predicates():
    """Load predicate names + definitions from the patterns YAML."""
    global _bundle_predicates_cache
    if _bundle_predicates_cache is not None:
        return _bundle_predicates_cache
    import yaml as _yaml
    with open(PATTERNS_PATH) as f:
        patterns = _yaml.safe_load(f)
    predicates = []
    for rp in patterns.get("relationship_patterns", []):
        predicates.append({
            "name": rp["name"],
            "definition": rp.get("semantic_definition", ""),
        })
    _bundle_predicates_cache = predicates
    return predicates


@app.get("/api/bundle/predicates")
async def bundle_predicates():
    """Return valid predicates from the loaded bundle (VP Eng guardrail #2)."""
    preds = _get_bundle_predicates()
    bundle = _get_bundle()
    return JSONResponse({
        "bundle_version": _bundle_id(),
        "predicates": preds,
    })


@app.get("/api/document/text/{doc_id}")
async def get_document_text(doc_id: str):
    """Return cached truncated 10-K text for evidence review (Sprint 39.3)."""
    ticker = doc_id  # Wave A wire-format shim
    ticker = ticker.upper()
    with _cache_lock:
        cached = _kg_cache.get(ticker)
    if not cached or not cached.get("text"):
        return JSONResponse({"error": "No cached text. Run extraction first."}, status_code=404)
    text = cached["text"]
    return JSONResponse({"doc_id": ticker, "text": text, "length": len(text)})


def _extract_resolve_target(
    reason_detail: str,
    entity_text: str,
    seen_entities: dict[str, set[str]],
    entity_original: dict[str, str],
) -> str:
    """Extract resolve_to_entity from reason_detail when the LLM omits the field.

    Uses multiple strategies in order:
    1. Quoted targets: resolve to 'iPhone', variant of 'Young LLP'
    2. Pattern extraction: "duplicate of X", "abbreviation for X"
    3. Self-match: entity text itself exists in graph under a different type
    4. Longest known entity mentioned in reason_detail
    """
    detail = reason_detail.strip()
    if not detail:
        return ""

    # Helper: validate a candidate against the entity list
    def _validate(candidate: str) -> str:
        """Return canonical entity text if candidate matches, else empty."""
        c = candidate.lower().strip()
        if c in seen_entities:
            return entity_original.get(c, candidate)
        return ""

    # Strategy 1: Quoted target — 'X' or "X" anywhere in detail
    # Handle apostrophes in names like O'Brien by allowing them inside quotes
    for m in re.finditer(r"['\"]([^'\"]{2,})['\"]", detail):
        candidate = m.group(1).strip()
        validated = _validate(candidate)
        if validated:
            return validated
    # If we found a quoted string but it didn't validate, still use it
    m = re.search(r"['\"]([^'\"]{2,})['\"]", detail)
    if m:
        candidate = m.group(1).strip()
        # Skip generic words the LLM uses
        if candidate.lower() not in ("entity", "existing", "the"):
            return candidate

    # Strategy 2: Pattern extraction from common LLM phrasings
    patterns = [
        # "garbled duplicate of Mac." — stop at period/comma/end
        r"(?:garbled\s+)?duplicate\s+of\s+([A-Z]\S+(?:\s+[A-Z]\S+){0,3})[\s.,]",
        # "abbreviation for Internal Revenue Service, which..."
        r"abbreviation\s+(?:for|of)\s+(.+?)(?:\s*[,.]|\s+which|\s+and\s)",
        # "variant of an existing entity" — skip if target is "an existing entity"
        r"variant\s+of\s+(?!an\s)(.+?)(?:\s*[,.]|\s+which|\s+and\s|\s+with\s)",
        # "entity for Africa as LOCATION" — extract between "for" and "as"
        r"(?:An\s+)?entity\s+for\s+(.+?)\s+as\s+",
    ]
    for pat in patterns:
        pm = re.search(pat, detail, re.IGNORECASE)
        if pm:
            candidate = pm.group(1).strip().rstrip(".,;")
            if candidate and len(candidate) >= 2:
                validated = _validate(candidate)
                if validated:
                    return validated
                # Accept unvalidated if it looks like an entity name
                if candidate[0].isupper() and candidate.lower() not in (
                    "entity", "existing", "an existing entity",
                ):
                    return candidate

    # Strategy 3: Self-match — the entity text itself exists under another type
    # e.g., "California" (COMPANY) should resolve to "California" (LOCATION)
    if entity_text:
        validated = _validate(entity_text)
        if validated:
            return validated
        # Try base form for garbled duplicates like "Mac Mac" → "Mac"
        words = entity_text.split()
        if len(words) >= 2 and words[0].lower() == words[1].lower():
            validated = _validate(words[0])
            if validated:
                return validated

    # Strategy 4: Longest known entity mentioned in reason_detail
    detail_lower = detail.lower()
    best_match = ""
    for key, orig_text in entity_original.items():
        if (
            len(orig_text) > len(best_match)
            and orig_text.lower() in detail_lower
            and orig_text.lower() not in ("entity", "existing")
        ):
            best_match = orig_text
    if best_match and len(best_match) >= 2:
        return best_match

    return ""


@app.post("/api/feedback/auto_flag")
async def auto_flag_graph(request: Request):
    """AI auto-detection of blatant FPs in the KGS graph (Sprint 39.2, Item 5).

    Two-pass approach: (1) flag bad entities, (2) flag bad edges with
    flagged-entity edges already removed from context.

    VP Eng guardrail G1: Results are ephemeral — NOT stored to FeedbackStore.
    The frontend must display these for user confirmation before persisting.

    Stage 0.5.4 (ADR-002): request body accepts ``llm_alias`` (preferred)
    or the deprecated ``model`` string; passing both returns 400.
    """
    import asyncio
    from kgspin_demo_app.llm_backend import (
        LLMParamsError,
        check_endpoint_llm_params,
        resolve_llm_backend,
    )

    body = await request.json()
    nodes = body.get("nodes", [])
    edges = body.get("edges", [])

    if not nodes and not edges:
        return JSONResponse({"flags": [], "bundle_entity_types": [], "bundle_type_hierarchy": {}, "bundle_type_definitions": {}})

    llm_alias = (body.get("llm_alias") or "").strip() or None
    legacy_model = (body.get("model") or "").strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=llm_alias,
            model_supplied="model" in body,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e), "flags": []}, status_code=400)

    # Build compact node descriptions and a lookup by id
    node_lines = []
    node_type_by_id: dict[str, str] = {}
    node_text_by_id: dict[str, str] = {}
    for n in nodes:
        node_lines.append(
            f"  Node id={n.get('id')}: \"{n.get('text', '?')}\" "
            f"type={n.get('entity_type', '?')} conf={n.get('confidence', 0):.2f}"
        )
        nid_str = str(n.get("id"))
        node_type_by_id[nid_str] = n.get("entity_type", "?")
        node_text_by_id[nid_str] = n.get("text", "?")

    document_id = body.get("document_id") or body.get("ticker", "unknown")
    entity_types = body.get("entity_types")  # Optional: [{name, semantic_definition}, ...]
    try:
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="auto_flag",
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"LLM backend unavailable: {e}", "flags": []},
            status_code=503,
        )

    # ID lookup sets for validation (handle int/str coercion)
    valid_node_ids = {n.get("id") for n in nodes}
    valid_node_ids_str = {str(n.get("id")) for n in nodes}
    node_id_map = {str(n.get("id")): n.get("id") for n in nodes}
    valid_edge_ids = {e.get("id") for e in edges}
    valid_edge_ids_str = {str(e.get("id")) for e in edges}
    edge_id_map = {str(e.get("id")): e.get("id") for e in edges}

    # Sprint 48: Build node→evidence lookup from edges for flag context
    node_evidence: dict[str, str] = {}  # node_id_str → first evidence sentence
    for e in edges:
        ev = e.get("evidence_text", "") or e.get("full_evidence_text", "")
        if not ev:
            continue
        for nid_key in (str(e.get("subject_id", "")), str(e.get("object_id", ""))):
            if nid_key and nid_key not in node_evidence:
                node_evidence[nid_key] = ev

    # ── Pass 1: Audit entities (chunked to avoid hallucinations) ───────
    ENTITY_CHUNK_SIZE = 25
    node_flags = []
    entity_system_prompt = (
        "You are an expert knowledge graph quality auditor. "
        "Each entity below has a 'type=' label showing its CURRENT type in the graph. "
        "Read each entity's type CAREFULLY before deciding if it is wrong. "
        "Respond ONLY with valid JSON, no markdown."
    )

    # Build entity type hierarchy from bundle (Sprint 47: v4.2.0 hierarchical types)
    bundle_for_types = _get_bundle(body.get("bundle_name"))
    _parent_types = getattr(bundle_for_types, "entity_parent_types", {})
    _sem_defs = getattr(bundle_for_types, "type_semantic_definitions", {})

    # Build hierarchy: {parent: [subtypes]}
    _subtypes_by_parent = {}
    for subtype, parent in _parent_types.items():
        _subtypes_by_parent.setdefault(parent, []).append(subtype)
    for parent in _subtypes_by_parent:
        _subtypes_by_parent[parent].sort()

    all_parent_names = sorted(set(_parent_types.values()))
    all_subtype_names = sorted(set(_parent_types.keys()))

    # graph_type_names = subtypes (what appears as entity_type on graph nodes)
    if _parent_types:
        graph_type_names = all_subtype_names
    elif entity_types:
        graph_type_names = sorted(t["name"] for t in entity_types)
    else:
        seen = {n.get("entity_type", "UNKNOWN") for n in nodes}
        seen.discard("UNKNOWN")
        seen.discard("?")
        graph_type_names = sorted(seen)

    # Build hierarchical type_lines for prompt
    if _subtypes_by_parent and _sem_defs:
        type_line_parts = []
        for parent in all_parent_names:
            subs = _subtypes_by_parent.get(parent, [])
            parent_def = _sem_defs.get(parent, "")
            sub_names_str = ", ".join(subs) if subs else "none"
            line = f"- {parent} (subtypes: {sub_names_str}): {parent_def}" if parent_def else f"- {parent} (subtypes: {sub_names_str})"
            type_line_parts.append(line)
            for s in subs:
                s_def = _sem_defs.get(s, "")
                type_line_parts.append(f"    - {s}: {s_def}" if s_def else f"    - {s}")
        type_lines = "\n".join(type_line_parts)
    elif entity_types:
        type_lines = "\n".join(
            f"- {t['name']}: {t['semantic_definition']}" if t.get("semantic_definition")
            else f"- {t['name']}"
            for t in entity_types
        )
    else:
        type_lines = "\n".join(f"- {t}" for t in graph_type_names)

    corrected_type_list = ", ".join(graph_type_names) + ", DO_NOT_EXTRACT"

    # Build existing entity list for should_resolve_to reason (deduplicated)
    _seen_entities: dict[str, set[str]] = {}  # text_lower → {types}
    _entity_original: dict[str, str] = {}  # text_lower → original_text
    for n in nodes:
        text = n.get("text", "?")
        etype = n.get("entity_type", "?")
        key = text.lower().strip()
        if key not in _entity_original:
            _entity_original[key] = text
        _seen_entities.setdefault(key, set()).add(etype)
    existing_entity_list_parts = []
    for key in sorted(_seen_entities.keys()):
        text = _entity_original[key]
        types = sorted(_seen_entities[key])
        existing_entity_list_parts.append(f"  - \"{text}\" (types: {', '.join(types)})")
    existing_entity_list = "\n".join(existing_entity_list_parts) if existing_entity_list_parts else "  (no entities)"

    entity_prompt_template = """Audit these entities extracted from document "{document_id}".

Each entity shows its CURRENT type after "type=". Read it carefully.

Entities:
{entity_lines}

ENTITY TYPE HIERARCHY (base types and their subtypes):
{type_lines}

RULES — CHOOSE EXACTLY ONE REASON per flagged entity:

1. ONLY flag entities that are CLEARLY wrong. When in doubt, do NOT flag.

2. "not_a_proper_noun" — The text is NOT a named entity AND is pure noise that
   should never appear as a node in a knowledge graph. It is a generic common
   noun, adjective, or ordinary word with no relationship to any real entity.
   Examples: "Form", "Annual Report", "Growth", "However", "Period", "Number",
   "Innovative Medicines", "Corporate Functions".
   Use this reason even if the text is capitalized — capitalization alone does
   not make something a named entity.
   Do NOT include "corrected_type" for this reason.

   IMPORTANT: Do NOT use "not_a_proper_noun" for role titles, job titles, or
   descriptors that refer to a specific person (see "resolvable_descriptor" below).

   IMPORTANT: Possessive phrases like "the Company's xxx" (e.g., "the Company's
   financial condition", "the Company's products") are NOT pure noise. They
   contain a possessive reference ("the Company's") that can be resolved to the
   actual company entity via coreference. Flag these with BOTH reasons:
   ["not_a_proper_noun", "resolvable_descriptor"]. See rule 2b.

2b. "resolvable_descriptor" — The text is a role title, job title, descriptor,
   or possessive phrase that is NOT a proper noun but REFERS TO a specific
   real person or entity. These should be resolved to the proper noun they
   describe, not deleted.
   Examples: "Chief Information Officer", "Worldwide Vice President",
   "Finance Manager", "the CEO", "the Chairman", "the Plaintiff",
   "the Company's financial condition", "the Company's products".
   Use this reason when the descriptor clearly refers to a specific individual
   or entity that could be identified from context.
   For possessive phrases ("the Company's xxx"), use BOTH reasons:
   ["not_a_proper_noun", "resolvable_descriptor"] — the phrase itself is not
   a proper noun, but the possessive portion resolves to a real entity.
   Do NOT include "corrected_type" for this reason.

3. "wrong_entity_type" — The text IS a legitimate named entity (a specific
   company, person, product, law, place, etc.) but it has the WRONG type label.
   You MUST include a "corrected_type" field. Valid values:
   {corrected_type_list}
   - Use a SUBTYPE name if the entity belongs to one (e.g., corrected_type: "EXECUTIVE")
   - Use a type ONLY if the entity truly belongs to one of the types above
     (e.g., "PREZCOBIX" type=PERSON → corrected_type: "BRANDED_PRODUCT")
   - Use "DO_NOT_EXTRACT" for any entity whose true type is NOT in the list
     above.
     Mention the actual type in reason_detail for context.
     (e.g., corrected_type: "DO_NOT_EXTRACT", reason_detail: "the United States Private Securities Litigation Reform Act is a
      law, not an organization")
   NEVER write "should be not_a_proper_noun" — if it is not a proper noun, use
   reason "not_a_proper_noun" or "resolvable_descriptor" instead.

4. "should_resolve_to" — The entity IS a valid named entity but is a variant,
   abbreviation, garbled extraction, or duplicate of ANOTHER entity that already
   exists in the graph. The entity should have been merged/resolved into the
   existing entity rather than creating a separate node.

   REQUIRED FIELD: "resolve_to_entity" — you MUST set this to the EXACT text of
   the target entity copied from the EXISTING ENTITIES list below.
   If no matching entity exists in the list, do NOT use should_resolve_to.
   Optionally include "corrected_type" if the type also needs fixing.

   EXISTING ENTITIES IN THE GRAPH (copy text exactly for resolve_to_entity):
   {existing_entity_list}

5. Do NOT flag the same entity with multiple reasons — EXCEPT for possessive
   phrases ("the Company's xxx") which get BOTH ["not_a_proper_noun", "resolvable_descriptor"].

Return JSON. The schema depends on the reason:

For not_a_proper_noun:
  {{"type": "node", "id": <ID>, "reasons": ["not_a_proper_noun"], "reason_detail": "..."}}

For resolvable_descriptor:
  {{"type": "node", "id": <ID>, "reasons": ["resolvable_descriptor"], "reason_detail": "..."}}

For both (possessive phrases only):
  {{"type": "node", "id": <ID>, "reasons": ["not_a_proper_noun", "resolvable_descriptor"], "reason_detail": "..."}}

For wrong_entity_type:
  {{"type": "node", "id": <ID>, "reasons": ["wrong_entity_type"], "corrected_type": "<TYPE>", "reason_detail": "..."}}

For should_resolve_to (resolve_to_entity is MANDATORY):
  {{"type": "node", "id": <ID>, "reasons": ["should_resolve_to"], "resolve_to_entity": "<EXACT entity text from list>", "reason_detail": "..."}}
  {{"type": "node", "id": <ID>, "reasons": ["should_resolve_to"], "resolve_to_entity": "<EXACT entity text from list>", "corrected_type": "<TYPE>", "reason_detail": "..."}}

Examples:
  {{"type": "node", "id": 42, "reasons": ["should_resolve_to"], "resolve_to_entity": "IRS", "corrected_type": "REGULATOR", "reason_detail": "I.R.S. is a variant of IRS"}}
  {{"type": "node", "id": 17, "reasons": ["should_resolve_to"], "resolve_to_entity": "Mac", "reason_detail": "Mac Mac is a garbled duplicate of Mac"}}
  {{"type": "node", "id": 5, "reasons": ["should_resolve_to"], "resolve_to_entity": "California", "reason_detail": "California is a LOCATION not a COMPANY"}}
  {{"type": "node", "id": 99, "reasons": ["wrong_entity_type"], "corrected_type": "PRODUCT", "reason_detail": "currently PERSON, should be PRODUCT"}}
  {{"type": "node", "id": 33, "reasons": ["not_a_proper_noun"], "reason_detail": "generic common noun"}}

Wrap all flags in: {{"flags": [...]}}
Use the exact integer Node IDs. If nothing is wrong, return {{"flags": []}}."""

    # Chunk node_lines into batches
    node_line_chunks = [
        node_lines[i:i + ENTITY_CHUNK_SIZE]
        for i in range(0, len(node_lines), ENTITY_CHUNK_SIZE)
    ]
    logger.info(
        f"Auto-flag Pass 1: {len(node_lines)} entities in "
        f"{len(node_line_chunks)} chunk(s) of {ENTITY_CHUNK_SIZE}"
    )

    for chunk_idx, chunk_lines in enumerate(node_line_chunks):
        try:
            prompt = entity_prompt_template.format(
                document_id=document_id,
                entity_lines=chr(10).join(chunk_lines),
                type_lines=type_lines,
                corrected_type_list=corrected_type_list,
                existing_entity_list=existing_entity_list,
            )
            result = await asyncio.to_thread(
                backend.complete,
                prompt,
                system_prompt=entity_system_prompt,
            )
            logger.info(
                f"Auto-flag Pass 1 chunk {chunk_idx + 1}/{len(node_line_chunks)}: "
                f"{len(result.text)} chars response"
            )
            parsed = _parse_llm_json(result.text)
            # Debug: log any should_resolve_to flags with their raw fields
            for _dbg_f in parsed.get("flags", []):
                if "should_resolve_to" in _dbg_f.get("reasons", []):
                    logger.info(
                        f"Auto-flag Pass 1 RAW should_resolve_to: "
                        f"id={_dbg_f.get('id')} "
                        f"resolve_to_entity='{_dbg_f.get('resolve_to_entity', '<MISSING>')}' "
                        f"reason_detail='{str(_dbg_f.get('reason_detail', ''))[:100]}'"
                    )
            for f in parsed.get("flags", []):
                fid = f.get("id")
                fid_str = str(fid) if fid is not None else ""
                if f.get("type") != "node" or (
                    fid not in valid_node_ids and fid_str not in valid_node_ids_str
                ):
                    logger.warning(
                        f"Auto-flag Pass 1: dropped flag type={f.get('type')} id={fid}"
                    )
                    continue
                f["id"] = node_id_map.get(fid_str, fid)
                # Server-side validation: reject hallucinated type mismatches
                reasons = f.get("reasons", [])
                detail = f.get("reason_detail", "")
                actual_type = node_type_by_id.get(fid_str, "")
                if "wrong_entity_type" in reasons and actual_type:
                    detail_lower = detail.lower()
                    actual_lower = actual_type.lower()
                    # Fix LLM confusion: wrong_entity_type with "should be not_a_proper_noun"
                    if "not_a_proper_noun" in detail_lower or "not a proper noun" in detail_lower:
                        # Check if detail suggests resolvable descriptor
                        if any(kw in detail_lower for kw in ("resolvable", "descriptor", "role", "title", "refers to")):
                            f["reasons"] = ["resolvable_descriptor"]
                        elif any(kw in detail_lower for kw in ("possessive", "company's", "the company")):
                            # Sprint 52c: possessive phrases get both reasons
                            f["reasons"] = ["not_a_proper_noun", "resolvable_descriptor"]
                        else:
                            f["reasons"] = ["not_a_proper_noun"]
                        f.pop("corrected_type", None)
                        logger.info(
                            f"Auto-flag Pass 1: converted wrong_entity_type → "
                            f"{f['reasons'][0]} for node {fid}: {detail}"
                        )
                    # Reject if the suggested correct type equals the current type
                    elif f"should be {actual_lower}" in detail_lower:
                        logger.warning(
                            f"Auto-flag Pass 1: rejected hallucinated flag on "
                            f"node {fid} — suggested type matches current "
                            f"type {actual_type}: {detail}"
                        )
                        continue
                    else:
                        # Validate corrected_type is present for wrong_entity_type
                        corrected = f.get("corrected_type", "")
                        if not corrected:
                            logger.warning(
                                f"Auto-flag Pass 1: wrong_entity_type missing "
                                f"corrected_type for node {fid}, defaulting to "
                                f"DO_NOT_EXTRACT"
                            )
                            f["corrected_type"] = "DO_NOT_EXTRACT"
                        elif corrected != "DO_NOT_EXTRACT" and corrected not in graph_type_names:
                            # LLM hallucinated a non-bundle type → convert
                            logger.info(
                                f"Auto-flag Pass 1: non-bundle corrected_type "
                                f"'{corrected}' for node {fid} → DO_NOT_EXTRACT"
                            )
                            f["corrected_type"] = "DO_NOT_EXTRACT"
                # Validate should_resolve_to: must have resolve_to_entity
                if "should_resolve_to" in reasons:
                    resolve_to = f.get("resolve_to_entity", "").strip()
                    if not resolve_to:
                        # Fallback: extract resolve_to_entity from reason_detail
                        detail = f.get("reason_detail", "")
                        entity_text = node_text_by_id.get(fid_str, "")
                        resolve_to = _extract_resolve_target(
                            detail, entity_text, _seen_entities, _entity_original
                        )
                        if resolve_to:
                            f["resolve_to_entity"] = resolve_to
                            logger.info(
                                f"Auto-flag Pass 1: extracted resolve_to_entity "
                                f"'{resolve_to}' from reason_detail for node {fid}"
                            )
                        else:
                            logger.warning(
                                f"Auto-flag Pass 1: should_resolve_to missing "
                                f"resolve_to_entity for node {fid}, dropping flag. "
                                f"reason_detail: {detail[:100]}"
                            )
                            continue
                    # Strip no-op corrected_type (e.g., MARKET → MARKET)
                    corrected = f.get("corrected_type", "")
                    if corrected and actual_type and corrected.upper() == actual_type.upper():
                        logger.info(
                            f"Auto-flag Pass 1: stripped no-op corrected_type "
                            f"'{corrected}' for should_resolve_to on node {fid}"
                        )
                        f.pop("corrected_type", None)
                # Strip corrected_type from flags that don't use it
                if "wrong_entity_type" not in f.get("reasons", []) and "should_resolve_to" not in f.get("reasons", []):
                    f.pop("corrected_type", None)
                # Sprint 48: Attach evidence sentence for context
                f["evidence_sentence"] = node_evidence.get(fid_str, "")
                # Final gate: reject should_resolve_to with empty resolve_to_entity
                final_reasons = f.get("reasons", [])
                if "should_resolve_to" in final_reasons:
                    rte = f.get("resolve_to_entity", "").strip()
                    if not rte:
                        logger.warning(
                            f"Auto-flag Pass 1 FINAL GATE: dropping should_resolve_to "
                            f"with empty resolve_to_entity for node {f.get('id')}"
                        )
                        continue
                node_flags.append(f)
        except json.JSONDecodeError as exc:
            logger.warning(
                f"Auto-flag Pass 1 chunk {chunk_idx + 1}: JSONDecodeError — {exc}"
            )
        except Exception as exc:
            logger.warning(f"Auto-flag Pass 1 chunk {chunk_idx + 1} failed: {exc}")

    logger.info(f"Auto-flag Pass 1: {len(node_flags)} valid entity flags total")

    # ── Pass 2: Audit edges (excluding those connected to flagged nodes) ─
    edge_flags = []
    flagged_node_ids = {str(f["id"]) for f in node_flags}

    # Filter out edges connected to flagged nodes
    clean_edges = []
    for e in edges:
        subj_id = str(e.get("subject_id", ""))
        obj_id = str(e.get("object_id", ""))
        if subj_id not in flagged_node_ids and obj_id not in flagged_node_ids:
            clean_edges.append(e)
    logger.info(
        f"Auto-flag Pass 2: {len(edges)} total edges, "
        f"{len(edges) - len(clean_edges)} removed (connected to flagged nodes), "
        f"{len(clean_edges)} remaining"
    )

    if clean_edges:
        edge_lines = []
        for e in clean_edges:
            edge_lines.append(
                f"  Edge id={e.get('id')}: \"{e.get('subject_text', '?')}\" "
                f"({e.get('subject_type', '?')}) "
                f"--[{e.get('predicate', '?')}]--> \"{e.get('object_text', '?')}\" "
                f"({e.get('object_type', '?')}) "
                f"conf={e.get('confidence', 0):.2f}"
            )
        try:
            edge_prompt = f"""Audit the relationships extracted from document "{document_id}".
Both entities in each relationship below have already been validated as correct.

Relationships:
{chr(10).join(edge_lines)}

Flag relationships that are CLEARLY wrong. Available reasons:

- "wrong_subject": The subject entity is incorrect for this relationship.
- "wrong_object": The object entity is incorrect for this relationship.
- "wrong_direction": The subject and object are SWAPPED — the entity that should
  be the subject is in the object position, or vice versa.
  Read the predicate as a verb: "Subject [predicate] Object" means the subject
  PERFORMS the action on the object.

  Example of CORRECT direction (do NOT flag):
    "Acme Corp" --[acquired]--> "Beta Inc" means Acme acquired Beta. ✓ Correct.

  Example of WRONG direction (DO flag):
    "Beta Inc" --[acquired]--> "Acme Corp" means Beta acquired Acme,
    but actually Acme acquired Beta. ✗ Flag this.

  Do NOT flag as wrong_direction if:
    - The predicate uses a passive or "_by" suffix (e.g., "acquired_by", "regulated_by")
      and the direction matches the passive meaning
    - You merely prefer different phrasing but the semantic direction is correct
    - The description of the relationship matches what "Subject [predicate] Object" says

- "invalid_relationship": The relationship type doesn't make sense between these entities.

Return JSON:
{{"flags": [
  {{"type": "edge", "id": "<edge_id_string>", "reasons": ["wrong_subject"], "reason_detail": "brief explanation"}}
]}}

IMPORTANT: Use the exact Edge ID strings from the list above.
Only flag clear errors. If nothing is wrong, return {{"flags": []}}."""

            result = await asyncio.to_thread(
                backend.complete,
                edge_prompt,
                system_prompt=(
                    "You are an expert knowledge graph quality auditor. "
                    "Respond ONLY with valid JSON, no markdown."
                ),
            )
            logger.info(f"Auto-flag Pass 2 (edges): {len(result.text)} chars response")
            parsed = _parse_llm_json(result.text)
            for f in parsed.get("flags", []):
                fid = f.get("id")
                fid_str = str(fid) if fid is not None else ""
                if f.get("type") == "edge" and (fid in valid_edge_ids or fid_str in valid_edge_ids_str):
                    f["id"] = edge_id_map.get(fid_str, fid)
                    edge_flags.append(f)
                else:
                    logger.warning(f"Auto-flag Pass 2: dropped flag type={f.get('type')} id={fid}")
            logger.info(f"Auto-flag Pass 2: {len(edge_flags)} valid edge flags")
        except json.JSONDecodeError as exc:
            logger.warning(f"Auto-flag Pass 2: JSONDecodeError — {exc}")
        except Exception as exc:
            logger.warning(f"Auto-flag Pass 2 failed: {exc}")

    all_flags = node_flags + edge_flags
    logger.info(f"Auto-flag: returning {len(all_flags)} total flags ({len(node_flags)} nodes, {len(edge_flags)} edges)")
    return JSONResponse({
        "flags": all_flags,
        "bundle_entity_types": graph_type_names,
        "bundle_type_hierarchy": _subtypes_by_parent,
        "bundle_type_definitions": _sem_defs,
    })


@app.post("/api/feedback/auto_discover_tp")
async def auto_discover_tp(request: Request):
    """AI gold-data selector: surface highest-quality extractions for confirmation.

    Evaluates the EXISTING graph and identifies entities and relationships
    that are most likely correct and valuable as gold data. Does NOT do
    a new extraction from the source document.

    VP Eng guardrail G1: Results are ephemeral — NOT stored to FeedbackStore.
    The frontend must display these for user confirmation before persisting.

    Stage 0.5.4 (ADR-002): request body accepts ``llm_alias`` (preferred)
    or the deprecated ``model`` string; passing both returns 400.
    """
    import asyncio
    from kgspin_demo_app.llm_backend import (
        LLMParamsError,
        check_endpoint_llm_params,
        resolve_llm_backend,
    )

    body = await request.json()
    ticker = (body.get("ticker") or "").upper()
    nodes = body.get("nodes", [])
    edges = body.get("edges", [])

    if not nodes and not edges:
        return JSONResponse({"discoveries": [], "error": "No graph data provided."})

    llm_alias = (body.get("llm_alias") or "").strip() or None
    legacy_model = (body.get("model") or "").strip() or None
    try:
        check_endpoint_llm_params(
            llm_alias=llm_alias,
            model_supplied="model" in body,
        )
    except LLMParamsError as e:
        return JSONResponse({"error": str(e), "discoveries": []}, status_code=400)

    try:
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="auto_discover_tp",
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"LLM backend unavailable: {e}", "discoveries": []},
            status_code=503,
        )

    # Build entity lines with IDs for reference
    entity_lines = []
    for n in nodes:
        entity_lines.append(
            f"  Node id={n.get('id')}: \"{n.get('text', '?')}\" "
            f"type={n.get('entity_type', '?')} conf={n.get('confidence', 0):.2f}"
        )

    # Build relationship lines with IDs and evidence
    edge_lines = []
    for e in edges:
        ev = e.get("metadata", {}).get("evidence_text", "") or e.get("evidence_text", "") or ""
        ev_snippet = ev[:200] if ev else ""
        line = (
            f"  Edge id={e.get('id')}: \"{e.get('subject_text', '?')}\" "
            f"--[{e.get('predicate', '?')}]--> \"{e.get('object_text', '?')}\" "
            f"conf={e.get('confidence', 0):.2f}"
        )
        if ev_snippet:
            line += f" evidence=\"{ev_snippet}\""
        edge_lines.append(line)

    # Get entity types from bundle for context
    bundle_for_types = _get_bundle(body.get("bundle_name"))
    _parent_types = getattr(bundle_for_types, "entity_parent_types", {})
    _sem_defs = getattr(bundle_for_types, "type_semantic_definitions", {})

    _subtypes_by_parent = {}
    for subtype, parent in _parent_types.items():
        _subtypes_by_parent.setdefault(parent, []).append(subtype)

    all_parent_names = sorted(set(_parent_types.values()))
    if _subtypes_by_parent and _sem_defs:
        type_line_parts = []
        for parent in all_parent_names:
            subs = _subtypes_by_parent.get(parent, [])
            parent_def = _sem_defs.get(parent, "")
            sub_names_str = ", ".join(sorted(subs)) if subs else "none"
            line = f"- {parent} (subtypes: {sub_names_str}): {parent_def}" if parent_def else f"- {parent} (subtypes: {sub_names_str})"
            type_line_parts.append(line)
        type_lines = "\n".join(type_line_parts)
    else:
        seen = {n.get("entity_type", "UNKNOWN") for n in nodes}
        seen.discard("UNKNOWN")
        type_lines = "\n".join(f"- {t}" for t in sorted(seen))

    preds = _get_bundle_predicates()
    pred_lines = "\n".join(
        f"- {p['name']}: {p['definition']}" if p.get("definition") else f"- {p['name']}"
        for p in preds
    )

    system_prompt = (
        "You are an expert knowledge graph quality auditor. Your job is to review "
        "an EXISTING extracted graph and identify the highest-quality, most correct "
        "entities and relationships that should be saved as gold data. "
        "Respond ONLY with valid JSON, no markdown."
    )

    # Sprint 12 Task 7: prefer admin-registered prompt template when
    # available. Admin template uses .format() placeholders (not
    # f-string interpolation) so the text lives in admin's
    # prompt_template registry without being Python code. Falls back
    # to the hardcoded f-string below when admin is empty or the
    # template is missing placeholders.
    _admin_prompt = _admin_prompt_or_none("kg-quality-comparison")
    _prompt_context = {
        "doc_id": ticker,
        "num_entities": len(nodes),
        "num_relationships": len(edges),
        "entity_lines_body": chr(10).join(entity_lines[:500]),
        "entity_lines_more": "... and more" if len(entity_lines) > 500 else "",
        "edge_lines_body": chr(10).join(edge_lines[:500]),
        "edge_lines_more": "... and more" if len(edge_lines) > 500 else "",
        "type_lines": type_lines,
        "pred_lines": pred_lines,
    }
    prompt: str | None = None
    if _admin_prompt is not None:
        try:
            prompt = _admin_prompt.format(**_prompt_context)
        except (KeyError, IndexError) as _e:
            logger.warning(
                "[PROMPT_TEMPLATE] kg-quality-comparison admin template "
                "missing placeholders %s; falling back to hardcoded prompt",
                _e,
            )
            prompt = None

    if prompt is None:
        prompt = f"""You are reviewing a knowledge graph extracted from a {ticker} SEC 10-K filing.
Your task is to evaluate the EXISTING extractions and select the ones that are
most likely CORRECT and most VALUABLE as gold (ground truth) data.

=== EXTRACTED ENTITIES ({len(nodes)} total) ===
{chr(10).join(entity_lines[:500])}
{"... and more" if len(entity_lines) > 500 else ""}

=== EXTRACTED RELATIONSHIPS ({len(edges)} total) ===
{chr(10).join(edge_lines[:500])}
{"... and more" if len(edge_lines) > 500 else ""}

=== BUNDLE SCHEMA (valid entity types) ===
{type_lines}

=== BUNDLE SCHEMA (valid predicates) ===
{pred_lines}

TASK: Select the best extractions from the graph above as gold data candidates.
Be COMPREHENSIVE — include ALL correct instances of each type, not just one example.

Select up to 30 entities and up to 50 relationships. Include ALL instances of each
predicate type that appear correct, not just one representative example. For example,
if the graph shows 10 has_subsidiary relationships that appear correct, include all 10.

For ENTITIES, prioritize:
- Named people (executives, board members) with correct type
- Named companies, subsidiaries, joint ventures with correct type
- Named regulators, products, locations with correct type
- Entities with HIGH confidence scores
- Entities whose type clearly matches the bundle schema definitions

For RELATIONSHIPS, prioritize:
- Executive-to-company relationships (is_executive, is_board_member)
- Subsidiary/ownership relationships (has_subsidiary, acquired)
- Regulatory relationships (regulated_by, reports_to)
- Relationships where BOTH entities are correctly typed
- Relationships with HIGH confidence and a valid predicate from the bundle

DO NOT select:
- Entities that look like noise, generic nouns, or garbled text
- Entities with suspiciously low confidence (< 0.3)
- Relationships that seem wrong or hallucinated
- Duplicate or near-duplicate entities

EVIDENCE REQUIREMENTS:
For each relationship, you MUST provide an "evidence_sentence" field containing a
real, readable natural language sentence from the 10-K filing that proves this
relationship. The evidence should be the kind of sentence a human analyst would
highlight as proof.

Good evidence contains:
1. The relationship keyword or a clear synonym (anchor signal)
2. The entity name or a recognizable proper noun (entity slug)

Examples of GOOD evidence:
- "Kenvue Inc. was incorporated as a wholly-owned subsidiary of Johnson & Johnson."
- "The Company completed its acquisition of Abiomed, Inc. in December 2022."
- "Joaquin Duato has served as Chairman and Chief Executive Officer since January 2022."

Examples of BAD evidence (do NOT use these patterns):
- "jnj:KenvueIncMember jnj:JohnsonJohnsonMember 2023-05-08" (structured metadata, not a sentence)
- "See Note 18 to the Consolidated Financial Statements" (reference, not evidence)
- "Subsidiary" (single word, not a sentence)

If the edge already has an evidence field shown above, you may use that sentence
directly if it meets the quality criteria.

Return JSON:
{{
  "discoveries": [
    {{
      "discovery_type": "entity",
      "node_id": <integer_node_id>,
      "entity_text": "exact entity text from the graph",
      "entity_type": "SUBTYPE",
      "confidence": <float>,
      "reason_detail": "why this is high-quality gold data"
    }},
    {{
      "discovery_type": "relationship",
      "edge_id": <integer_edge_id>,
      "subject_text": "exact subject text",
      "predicate": "predicate_name",
      "object_text": "exact object text",
      "confidence": <float>,
      "evidence_sentence": "exact natural language sentence from the 10-K that proves this relationship",
      "reason_detail": "why this relationship is correct and valuable"
    }}
  ]
}}

If the graph is too noisy to select gold data, return {{"discoveries": []}}."""

    try:
        result = await asyncio.to_thread(
            backend.complete,
            prompt,
            system_prompt=system_prompt,
        )
        logger.info(f"Auto-discover TP: {len(result.text)} chars response")

        parsed = _parse_llm_json(result.text)

        # Build lookup maps for validation
        node_by_id = {str(n.get("id")): n for n in nodes}
        edge_by_id = {str(e.get("id")): e for e in edges}

        discoveries = []
        for d in parsed.get("discoveries", []):
            discovery_type = d.get("discovery_type", "entity")

            if discovery_type == "entity":
                node_id = str(d.get("node_id", ""))
                entity_text = d.get("entity_text", "").strip()
                # Validate node exists in graph
                node = node_by_id.get(node_id)
                if node:
                    # Use actual graph data, not LLM's copy
                    discoveries.append({
                        "discovery_type": "entity",
                        "node_id": node.get("id"),
                        "entity_text": node.get("text", entity_text),
                        "entity_type": node.get("entity_type", d.get("entity_type", "")),
                        "confidence": node.get("confidence", 0),
                        "reason_detail": d.get("reason_detail", ""),
                    })
                elif entity_text:
                    # Node ID didn't match but text was provided — keep with warning
                    logger.warning(f"Auto-discover TP: node_id {node_id} not found, using text match")
                    discoveries.append({
                        "discovery_type": "entity",
                        "entity_text": entity_text,
                        "entity_type": d.get("entity_type", ""),
                        "confidence": d.get("confidence", 0),
                        "reason_detail": d.get("reason_detail", ""),
                    })
            else:
                edge_id = str(d.get("edge_id", ""))
                edge = edge_by_id.get(edge_id)
                evidence = d.get("evidence_sentence", "")
                if edge:
                    discoveries.append({
                        "discovery_type": "relationship",
                        "edge_id": edge.get("id"),
                        "subject_text": edge.get("subject_text", d.get("subject_text", "")),
                        "predicate": edge.get("predicate", d.get("predicate", "")),
                        "object_text": edge.get("object_text", d.get("object_text", "")),
                        "confidence": edge.get("confidence", 0),
                        "evidence_sentence": evidence,
                        "reason_detail": d.get("reason_detail", ""),
                    })
                else:
                    # Edge ID didn't match — keep with text data
                    subj = d.get("subject_text", "").strip()
                    pred = d.get("predicate", "").strip()
                    obj = d.get("object_text", "").strip()
                    if subj and pred and obj:
                        logger.warning(f"Auto-discover TP: edge_id {edge_id} not found, using text match")
                        discoveries.append({
                            "discovery_type": "relationship",
                            "subject_text": subj,
                            "predicate": pred,
                            "object_text": obj,
                            "confidence": d.get("confidence", 0),
                            "evidence_sentence": evidence,
                            "reason_detail": d.get("reason_detail", ""),
                        })

        logger.info(f"Auto-discover TP: returning {len(discoveries)} gold candidates")
        return JSONResponse({"discoveries": discoveries})

    except json.JSONDecodeError as exc:
        logger.warning(f"Auto-discover TP: JSONDecodeError — {exc}")
        return JSONResponse(
            {"error": "LLM response was not valid JSON", "discoveries": []},
            status_code=500,
        )
    except Exception as exc:
        logger.warning(f"Auto-discover TP failed: {exc}")
        return JSONResponse(
            {"error": str(exc), "discoveries": []},
            status_code=500,
        )


# --- SSE Helpers ---


from sse.events import sse_event  # noqa: E402  (re-export for legacy call sites)


# resolve_ticker, html_to_text, strip_ixbrl, select_content_chunks
# are imported from pipeline_common above.


# --- Visualization Data Builder ---

# Sprint 33.15 (WI-1): Only actor types become graph nodes.
# VALUE_TYPES (MONEY, DATE, QUANTITY, EVENT) and domain-specific types
# (FINANCIAL_METRIC, RISK_FACTOR, etc.) are filtered out.
def _load_valid_schema_types() -> set:
    """Load all valid entity types (parents + subtypes) from pattern YAMLs
    AND from compiled type registries.

    Sprint 06: also scan ``.bundles/domains/*/type_registry.json`` so
    types defined by domain bundles installed via plugin repos
    (e.g., clinical-mvp-v1 from kgspin-plugin-clinical) are recognized
    by the demo's vis renderer. Without this, build_vis_data would
    drop COMPANY/DRUG/CONDITION entities from clinical extractions
    even though the bundle produces them — same shape as the
    Sprint 04 spaCy-types display bug.
    """
    import yaml as _yaml
    import json as _json
    valid = set()
    # Sprint 118: Domain YAMLs in bundles/ + bundles/domains/
    patterns_dirs = [Path("bundles"), Path("bundles") / "domains"]
    for patterns_dir in patterns_dirs:
        if not patterns_dir.is_dir():
            continue
        for pf in patterns_dir.glob("*.yaml"):
            try:
                with open(pf) as f:
                    p = _yaml.safe_load(f)
                for parent, info in (p.get("types", {}) or {}).items():
                    valid.add(parent)
                    for sub in (info.get("subtypes", {}) or {}).keys():
                        valid.add(sub)
            except Exception:
                continue
    # Sprint 06: union compiled type registries from .bundles/domains/*/
    compiled_root = Path(".bundles") / "domains"
    if compiled_root.is_dir():
        for type_reg in compiled_root.glob("*/type_registry.json"):
            try:
                with open(type_reg) as f:
                    reg = _json.load(f)
                for tname in (reg.get("types", {}) or {}).keys():
                    valid.add(tname)
            except Exception:
                continue
    # INIT-001 Sprint 04 (HITL round 1): always include the generic spaCy
    # NER base types. Without this, discovery_rapid runs emit entities of
    # type ORGANIZATION / LOCATION / PERSON / PRODUCT (the spaCy defaults)
    # which are filtered out by the domain schema's narrower types
    # (COMPANY, CORPORATE_LEADER, etc.), causing every relationship to be
    # dropped and the UI to display "0 rels" for otherwise-healthy runs.
    # The core team flagged this as a demo-side rendering bug during the
    # Sprint 02 rename verification — see the 2026-04-14 core→demo memo.
    valid |= {"PERSON", "ORGANIZATION", "LOCATION", "PRODUCT"}
    return valid

_VIS_ACTOR_TYPES = _load_valid_schema_types()

_FALLBACK_TYPE_COLORS = {
    "PERSON": "#5B9FE6",
    "ORGANIZATION": "#5ED68A",
    "MONEY": "#FFE066",
    "PERCENTAGE": "#FFB347",
    "DATE": "#E088E5",
    "LOCATION": "#4DD4C0",
    "PRODUCT": "#FF7F6B",
    "REGULATION": "#C49A6C",
    "RISK_FACTOR": "#FF6B8A",
    "FINANCIAL_METRIC": "#6B8FFF",
    "BUSINESS_SEGMENT": "#4BC88A",
    "EXECUTIVE": "#5B9FE6",
    "COMPANY": "#5ED68A",
    "REGULATOR": "#FF7F6B",
    "EMPLOYEE": "#5B9FE6",
    "BRANDED_PRODUCT": "#FF7F6B",
    "MARKET": "#4DD4C0",
    "OFFICE": "#4DD4C0",
    "EVENT": "#E088E5",
    # Clinical domain
    "DRUG": "#E57373",
    "CONDITION": "#FFB74D",
    "ENDPOINT": "#81C784",
    "BIOMARKER": "#64B5F6",
    "INVESTIGATOR": "#BA68C8",
    "PROCEDURE": "#4DD0E1",
    "UNKNOWN": "#AAAAAA",
}
DEFAULT_COLOR = "#AAAAAA"


def _load_type_colors_from_bundle(bundle_dir: Path | None) -> dict:
    """Load entity type colors from bundle's type_registry.json.

    Colors defined in the YAML bundle propagate through the compiler into
    type_registry.json. This function reads them and builds a type→color map,
    falling back to _FALLBACK_TYPE_COLORS for types without a color field.
    """
    # INIT-001 Sprint 01: allow None (no bundle compiled yet) — fall back to defaults
    if bundle_dir is None:
        return dict(_FALLBACK_TYPE_COLORS)
    registry_path = bundle_dir / "type_registry.json"
    if not registry_path.exists():
        return dict(_FALLBACK_TYPE_COLORS)
    try:
        import json as _json
        with open(registry_path) as f:
            registry = _json.load(f)
        colors = dict(_FALLBACK_TYPE_COLORS)
        for type_name, type_info in registry.get("types", {}).items():
            color = type_info.get("color")
            if color:
                colors[type_name] = color
        return colors
    except Exception:
        return dict(_FALLBACK_TYPE_COLORS)


TYPE_COLORS = _load_type_colors_from_bundle(BUNDLE_PATH)

# Deterministic relationship color palette — any predicate gets a unique color
_REL_COLOR_PALETTE = [
    "#5B9FE6", "#FF7F6B", "#FFE066", "#FF6B8A", "#E088E5",
    "#B8E986", "#6B8FFF", "#FFB347", "#4DD4C0", "#C49AFF",
    "#E6855B", "#81C784", "#BA68C8", "#64B5F6", "#F06292",
]


def _rel_color(predicate: str) -> str:
    """Deterministic color for a relationship predicate based on name hash."""
    idx = hash(predicate) % len(_REL_COLOR_PALETTE)
    return _REL_COLOR_PALETTE[idx]
# Noise/value types get a muted red-grey to visually flag them as non-actionable
_NOISE_COLOR = "#6B3A3A"
_NOISE_BORDER = "#FF4444"


# --- Wave J (PRD-056 v2): provenance-preserving merge + hub-registry client ---
#
# `_merge_kgs_with_provenance` replaces the old first-wins dedup. Every entity
# and relationship carries a `sources: list[dict]` where each dict is a
# SourceRef-as-JSON. Legacy KGs without `sources` get a synthetic default
# injected at merge time (kind="filing", origin="legacy").
#
# `_fetch_hub_registry` calls admin's GET /registry/hubs for the current
# domain, caches the response for the intel run's lifetime, and deserializes
# each row via `HubEntry.from_json`. On admin unreachable, returns an empty
# registry with a WARNING log — merge still runs, just without bridge
# creation (commit 2 wires that in).

_LEGACY_SOURCE_REF: dict = {
    "kind": "filing",
    "origin": "legacy",
    "article_id": None,
    "fetched_at": None,
}


def _source_ref_to_dict(source_ref: Any) -> dict:
    """Serialize a `SourceRef` (or dict) to a plain JSON-ready dict.

    Accepts either the `kgspin_core.execution.graph_aware.SourceRef` frozen
    dataclass or a pre-built dict with the same fields. Other shapes are
    coerced to `_LEGACY_SOURCE_REF` so downstream consumers never see a
    bare None or partial dict.
    """
    if source_ref is None:
        return dict(_LEGACY_SOURCE_REF)
    if isinstance(source_ref, dict):
        return {
            "kind": source_ref.get("kind", "unknown"),
            "origin": source_ref.get("origin", "unknown"),
            "article_id": source_ref.get("article_id"),
            "fetched_at": source_ref.get("fetched_at"),
        }
    # Assume SourceRef dataclass (duck-typed by attribute access).
    return {
        "kind": getattr(source_ref, "kind", "unknown"),
        "origin": getattr(source_ref, "origin", "unknown"),
        "article_id": getattr(source_ref, "article_id", None),
        "fetched_at": getattr(source_ref, "fetched_at", None),
    }


def _sources_with_default(
    item: dict, fallback_source_ref: Any | None
) -> list[dict]:
    """Return a list of source-ref dicts for `item`, injecting a default if absent.

    - If `item["sources"]` exists and is a non-empty list, coerce each entry to dict.
    - Else, use `fallback_source_ref` (coerced to dict); if that is also None,
      use the legacy filing default.
    """
    existing = item.get("sources")
    if isinstance(existing, list) and existing:
        return [_source_ref_to_dict(s) for s in existing]
    return [_source_ref_to_dict(fallback_source_ref)]


def _dedup_source_refs(refs: list[dict]) -> list[dict]:
    """De-duplicate a list of source-ref dicts by (kind, origin, article_id)."""
    seen: set = set()
    out: list[dict] = []
    for ref in refs:
        key = (ref.get("kind"), ref.get("origin"), ref.get("article_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _merge_kgs_with_provenance(
    base_kg: dict,
    overlay_kg: dict,
    *,
    admission_tokens: Optional[List[str]] = None,
    base_source_ref: Any | None = None,
    overlay_source_ref: Any | None = None,
) -> dict:
    """Union-with-provenance merge (PRD-056 v2 MH #1, #10).

    - Same normalized `(entity_type, text)` key → merge: aliases union,
      ``confidence = max``, ``sources`` union (deduped by (kind, origin, article_id)).
    - Same normalized `(subj_text, predicate, obj_text)` key on relationships
      → merge: ``sources`` union, ``confidence = max``.
    - Legacy items without ``sources`` receive a synthetic default
      (``base_source_ref`` or ``overlay_source_ref``, else the filing/legacy
      fallback).
    - ``admission_tokens`` is passed to ``normalize_entity_text`` so
      cross-bundle collisions collapse consistently (MH #10).
    """
    from kgspin_demo_app.services.entity_resolution import normalize_entity_text

    def _norm(t: str) -> str:
        return normalize_entity_text(t, admission_tokens=admission_tokens)

    merged: dict = {"entities": [], "relationships": []}

    # --- Entities (union with provenance) ------------------------------------
    entity_by_key: dict = {}
    for side, ents, sref in (
        ("base", base_kg.get("entities", []) or [], base_source_ref),
        ("overlay", overlay_kg.get("entities", []) or [], overlay_source_ref),
    ):
        for ent in ents:
            key = (ent.get("entity_type", ""), _norm(ent.get("text", "")))
            item_sources = _sources_with_default(ent, sref)
            if key not in entity_by_key:
                merged_ent = dict(ent)
                merged_ent["sources"] = list(item_sources)
                # Ensure aliases field exists as a list for union below.
                aliases = list(merged_ent.get("aliases", []) or [])
                merged_ent["aliases"] = aliases
                entity_by_key[key] = merged_ent
            else:
                existing = entity_by_key[key]
                # Union sources.
                existing["sources"] = _dedup_source_refs(
                    list(existing.get("sources", [])) + list(item_sources)
                )
                # Max confidence.
                prev_conf = existing.get("confidence", 0) or 0
                new_conf = ent.get("confidence", 0) or 0
                existing["confidence"] = max(prev_conf, new_conf)
                # Alias union.
                existing_aliases = set(existing.get("aliases", []) or [])
                existing_aliases.update(ent.get("aliases", []) or [])
                # Overlay text becomes an alias if it differs from the canonical.
                if ent.get("text") and ent.get("text") != existing.get("text"):
                    existing_aliases.add(ent.get("text"))
                existing["aliases"] = sorted(existing_aliases)

    merged["entities"] = list(entity_by_key.values())

    # --- Relationships (union with provenance) -------------------------------
    rel_by_key: dict = {}
    for side, rels, sref in (
        ("base", base_kg.get("relationships", []) or [], base_source_ref),
        ("overlay", overlay_kg.get("relationships", []) or [], overlay_source_ref),
    ):
        for rel in rels:
            subj = rel.get("subject", {}) or {}
            obj = rel.get("object", {}) or {}
            key = (
                _norm(subj.get("text", "")),
                rel.get("predicate", ""),
                _norm(obj.get("text", "")),
            )
            item_sources = _sources_with_default(rel, sref)
            if key not in rel_by_key:
                merged_rel = dict(rel)
                merged_rel["sources"] = list(item_sources)
                rel_by_key[key] = merged_rel
            else:
                existing = rel_by_key[key]
                existing["sources"] = _dedup_source_refs(
                    list(existing.get("sources", [])) + list(item_sources)
                )
                prev_conf = existing.get("confidence", 0) or 0
                new_conf = rel.get("confidence", 0) or 0
                existing["confidence"] = max(prev_conf, new_conf)

    merged["relationships"] = list(rel_by_key.values())

    # Carry forward other top-level keys (metadata, etc.). Prefer overlay values
    # when present; fall back to base otherwise.
    for k in set(list(base_kg.keys()) + list(overlay_kg.keys())):
        if k not in ("entities", "relationships"):
            merged[k] = overlay_kg.get(k, base_kg.get(k))

    return merged


def _merge_kgs(base_kg: dict, overlay_kg: dict) -> dict:
    """Backward-compat wrapper around `_merge_kgs_with_provenance`.

    Retains the historical call sites' shape. Adds ``sources`` to every
    entity/relationship (additive — legacy consumers ignore unknown keys).
    """
    return _merge_kgs_with_provenance(base_kg, overlay_kg)


# --- Hub registry client (Wave J MH #2) ----------------------------------

_hub_registry_cache: dict = {}  # (domain, ticker-scope) → list[HubEntry]
_hub_registry_cache_lock = threading.Lock()


def _fetch_hub_registry_sync(domain: str) -> list:
    """Synchronous GET {ADMIN}/registry/hubs?domain=<domain>.

    Returns a list of `HubEntry` dataclasses (may be empty). On HTTP / network
    failure, logs a WARNING and returns `[]` so callers keep running with
    merge-only behavior.
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    from kgspin_core.execution.graph_aware import HubEntry as _HubEntry

    admin_base = os.environ.get("KGSPIN_ADMIN_URL", "http://127.0.0.1:8750").rstrip("/")
    qs = urllib.parse.urlencode({"domain": domain})
    url = f"{admin_base}/registry/hubs?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        logger.warning(
            "hub registry unreachable at %s, falling back to empty registry: %s",
            url, exc,
        )
        return []

    rows = payload.get("hubs") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(_HubEntry.from_json(row))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("skipping malformed hub-registry row: %s (row=%r)", exc, row)
    return out


async def _fetch_hub_registry(domain: str) -> list:
    """Async wrapper over `_fetch_hub_registry_sync` with per-process caching.

    Caches at `(domain,)` granularity for the lifetime of the process; an
    intel run that completes is short-lived enough that a process-level
    cache is effectively a run-level cache. Admin's own TTL header handles
    freshness across runs.
    """
    with _hub_registry_cache_lock:
        cached = _hub_registry_cache.get(domain)
    if cached is not None:
        return cached
    registry = await asyncio.to_thread(_fetch_hub_registry_sync, domain)
    with _hub_registry_cache_lock:
        _hub_registry_cache[domain] = registry
    return registry


def _bundle_admission_tokens(bundle) -> Optional[List[str]]:
    """Derive the admission-token list from a bundle's TypeRegistry.

    Used by `_merge_kgs_with_provenance` for bundle-consistent normalization
    at the merge site (MH #10). Returns None if the bundle has no gates
    configured (falls back to the legacy hardcoded list inside
    `normalize_entity_text`).
    """
    try:
        type_registry = getattr(bundle, "type_registry", None) or getattr(bundle, "types", None)
        if type_registry is None or not hasattr(type_registry, "get_admission_gate_types"):
            return None
        gates = type_registry.get_admission_gate_types()
        if not gates:
            return None
        tokens: set = set()
        for gate in gates.values():
            for anchor in (gate.get("anchors") or []):
                if anchor:
                    tokens.add(anchor.lower())
        return sorted(tokens) if tokens else None
    except Exception:  # defensive — never block a merge on bundle introspection
        logger.debug("admission-token extraction failed; using normalize_entity_text defaults", exc_info=True)
        return None


def _cross_hub_relations_from_bundle(bundle: Any) -> frozenset:
    """Extract the set of relation predicates marked ``relation_kind: cross_hub``
    in the bundle's source YAML.

    Reads the raw blueprint YAML when available (admin-staged bundles publish
    a ``source_yaml_path`` attribute). Returns a frozenset of predicate names
    (e.g. ``{"partnered_with", "acquired", ...}``).

    Returns an empty frozenset on any failure — at which point
    ``_create_bridges_from_matches`` falls back to "any hub-hub relation is
    a bridge" (matches the graph_aware contract).
    """
    try:
        import yaml as _yaml  # PyYAML; already a transitive dep
    except ImportError:
        return frozenset()
    candidate_paths: list[Path] = []
    for attr in ("source_yaml_path", "source_path", "yaml_path"):
        p = getattr(bundle, attr, None)
        if p:
            candidate_paths.append(Path(p))
    domain = getattr(bundle, "domain", None) or ""
    version = getattr(bundle, "version", None) or ""
    if domain:
        # Last-resort: blueprint repo's canonical location.
        bp_root = Path(__file__).resolve().parents[3] / "kgspin-blueprint" / "references" / "bundles" / "domains" / domain
        if version:
            candidate_paths.append(bp_root / f"{domain}-{version}.yaml")
        candidate_paths.append(bp_root / f"{domain}-v2.yaml")
    for path in candidate_paths:
        if not path or not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
        except (OSError, _yaml.YAMLError):
            continue
        rels = data.get("relationship_patterns") or data.get("relationships") or []
        names: set = set()
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            if rel.get("relation_kind") == "cross_hub" and rel.get("name"):
                names.add(rel["name"])
        if names:
            return frozenset(names)
    return frozenset()


def _make_source_ref_for_article(source_type: str, article: dict, article_idx: int) -> Any:
    """Build a `SourceRef` for a single news article.

    Falls back gracefully when `article` fields are missing — a demo-only
    corpus fetcher may not populate every field.
    """
    from kgspin_core.execution.graph_aware import SourceRef as _SourceRef

    origin = (
        article.get("source_name")
        or article.get("outlet")
        or (source_type.split("_", 1)[0] if source_type else "news")
        or "news"
    )
    article_id = (
        article.get("url")
        or article.get("article_id")
        or f"{source_type}_{article_idx}"
    )
    fetched_at = (
        article.get("fetched_at")
        or article.get("published_at")
        or ""
    )
    return _SourceRef(
        kind="news_article",
        origin=str(origin),
        article_id=str(article_id),
        fetched_at=str(fetched_at),
    )


# --- Wave J (PRD-056 v2 MH #2): bridge-edge creation from hub-registry matches -----
#
# After a per-article merge, `_create_bridges_from_matches` scans the merged KG
# for relationships whose subject + object both resolve to hub-registry rows.
# Any such relationship is reclassified as a bridge edge:
#
#   - `kind: "bridge"` discriminator (vs `"spoke"` default)
#   - `is_cross_hub_bridge: True` — hint for the utility gate
#   - `subject_hub_ref` / `object_hub_ref` — canonical_name of matched hub
#   - `sources` extended with this article's SourceRef (if not already present)
#
# When `cross_hub_relations` is non-empty (populated from the bundle's cross-hub
# catalog), only predicates in that set are treated as bridges. When empty, the
# fallback is "any relation between two distinct hub entities is a bridge"
# (matches the graph_aware contract: "When empty, bridge labeling falls back to
# 'any hub-hub relation is a bridge'").
#
# Non-bridge hub matches (single-sided, or no relation) are left as-is; they
# were already merged with provenance in commit 1 and are considered first-class
# spokes by virtue of the `sources` list attached during merge.


def _build_extraction_context_for_article(
    *,
    base_kg: dict,
    hub_registry: list,
    source_ref: Any,
    intel_linking_prompt: str | None = None,
    cross_hub_relations: frozenset = frozenset(),
) -> Any:
    """Construct a per-article `ExtractionContext` (PRD-060 / Wave I shape).

    Built at merge time so ``base_kg`` reflects the running merged KG
    (i.e. SEC base + all previously-merged articles).
    """
    from kgspin_core.execution.graph_aware import ExtractionContext as _ExtractionContext
    return _ExtractionContext(
        base_kg=base_kg,
        hub_registry=tuple(hub_registry),
        intel_linking_prompt=intel_linking_prompt,
        source_ref=source_ref,
        cross_hub_relations=cross_hub_relations,
    )


def _entity_hub_match(
    entity: dict,
    hub_registry: list,
    *,
    admission_tokens: Optional[List[str]] = None,
) -> Any | None:
    """Return the matching `HubEntry` for an entity, or None.

    Match strategy (first hit wins):
      1. Normalized canonical_name equals normalized entity text.
      2. Normalized entity text appears in the hub's normalized `aliases`.

    `admission_tokens` is threaded through `normalize_entity_text` so that
    e.g. ``"Johnson & Johnson Inc."`` collapses to ``"Johnson & Johnson"`` when
    ``inc`` is an admission token in the bundle.
    """
    if not hub_registry:
        return None
    from kgspin_demo_app.services.entity_resolution import normalize_entity_text

    raw = entity.get("text", "") or ""
    if not raw:
        return None
    needle = normalize_entity_text(raw, admission_tokens=admission_tokens)
    if not needle:
        return None
    entity_aliases = [
        normalize_entity_text(a, admission_tokens=admission_tokens)
        for a in (entity.get("aliases") or [])
    ]
    alias_set = {a for a in entity_aliases if a}
    alias_set.add(needle)

    for hub in hub_registry:
        canonical = normalize_entity_text(
            getattr(hub, "canonical_name", "") or "",
            admission_tokens=admission_tokens,
        )
        if canonical and canonical in alias_set:
            return hub
        for alias in (getattr(hub, "aliases", None) or ()):
            norm_alias = normalize_entity_text(alias, admission_tokens=admission_tokens)
            if norm_alias and norm_alias in alias_set:
                return hub
    return None


def _create_bridges_from_matches(
    merged_kg: dict,
    *,
    current_hub: Optional[str],
    hub_registry: list,
    cross_hub_relations: frozenset = frozenset(),
    admission_tokens: Optional[List[str]] = None,
    gate: Any = None,
    source_ref: Any = None,
) -> dict:
    """Annotate merged-KG relationships as bridge edges when both endpoints are hubs.

    Mutates relationships in place (merged_kg is returned for chaining).
    Returns a dict: ``{"bridges_created": [...], "spokes_promoted": [...]}``
    for the caller to surface via SSE (commit 3).

    A relationship is a bridge iff:
      * subject entity matches some ``HubEntry`` in ``hub_registry``, AND
      * object entity matches some ``HubEntry`` in ``hub_registry``, AND
      * those two hubs are distinct (by ``canonical_name``), AND
      * either ``cross_hub_relations`` is empty (fallback: any hub-hub relation
        is a bridge), or the relationship's predicate is in that set.

    When a hub-match exists on only one side (and the match is not the
    ``current_hub``), the entity is recorded in ``spokes_promoted`` — its
    provenance is already attached by the merge, so no mutation is needed,
    but downstream UI can highlight it.

    The ``gate`` (a ``UtilityGate`` protocol implementation) gets the final
    say. For cross-hub bridges, ``HybridUtilityGate.should_commit`` always
    returns True; the gate hook lets tests inject stricter policies.
    """
    if gate is None:
        from kgspin_core.execution.graph_aware import HybridUtilityGate as _HybridUtilityGate
        gate = _HybridUtilityGate()

    source_ref_dict = _source_ref_to_dict(source_ref) if source_ref is not None else None
    bridges_created: list[dict] = []
    spokes_promoted: list[dict] = []

    # Index entities by normalized (entity_type, text) so we can hub-match fast.
    from kgspin_demo_app.services.entity_resolution import normalize_entity_text
    entities_by_key: dict = {}
    for ent in merged_kg.get("entities", []) or []:
        key = (
            ent.get("entity_type", ""),
            normalize_entity_text(ent.get("text", "") or "", admission_tokens=admission_tokens),
        )
        entities_by_key[key] = ent

    def _lookup_entity(side: dict) -> dict | None:
        if not isinstance(side, dict):
            return None
        raw = side.get("text", "") or ""
        if not raw:
            return None
        # Try any entity_type first (blueprint rel-endpoints sometimes carry types).
        etype = side.get("entity_type", "") or ""
        norm = normalize_entity_text(raw, admission_tokens=admission_tokens)
        for (e_type, e_text), ent in entities_by_key.items():
            if e_text == norm and (not etype or e_type == etype):
                return ent
        return None

    def _hub_name(hub: Any) -> str:
        return getattr(hub, "canonical_name", "") or ""

    current_hub_norm = (
        normalize_entity_text(current_hub or "", admission_tokens=admission_tokens)
        if current_hub else ""
    )

    relationships = merged_kg.get("relationships", []) or []
    emitted_so_far: list[dict] = [r for r in relationships if r.get("kind") == "bridge"]
    seen_spokes: set = set()

    for rel in relationships:
        subj = rel.get("subject", {}) or {}
        obj = rel.get("object", {}) or {}
        subj_ent = _lookup_entity(subj) or subj
        obj_ent = _lookup_entity(obj) or obj

        subj_hub = _entity_hub_match(subj_ent, hub_registry, admission_tokens=admission_tokens)
        obj_hub = _entity_hub_match(obj_ent, hub_registry, admission_tokens=admission_tokens)

        subj_name = _hub_name(subj_hub) if subj_hub else ""
        obj_name = _hub_name(obj_hub) if obj_hub else ""

        if subj_hub and obj_hub and subj_name and obj_name and subj_name != obj_name:
            predicate = rel.get("predicate", "")
            if cross_hub_relations and predicate not in cross_hub_relations:
                # Not in the cross-hub relation catalog → treat as regular edge.
                continue
            if rel.get("kind") == "bridge":
                continue  # already bridged in a prior pass

            candidate = dict(rel)
            candidate["kind"] = "bridge"
            candidate["is_cross_hub_bridge"] = True
            candidate["subject_hub_ref"] = {"canonical_name": subj_name}
            candidate["object_hub_ref"] = {"canonical_name": obj_name}
            if source_ref_dict is not None:
                candidate["sources"] = _dedup_source_refs(
                    list(rel.get("sources", []) or []) + [source_ref_dict]
                )

            if not gate.should_commit(candidate, merged_kg, emitted_so_far):
                continue

            rel.update(candidate)
            emitted_so_far.append(rel)
            bridges_created.append({
                "predicate": rel.get("predicate"),
                "subject": subj_name,
                "object": obj_name,
                "sources": list(rel.get("sources", []) or []),
            })
        else:
            # Single-sided hub match → promote the non-current-hub entity as spoke.
            # Normalize both sides before comparing: `_hub_name()` returns the
            # raw canonical_name from the registry; `current_hub_norm` is
            # already normalized. Without normalizing the registry side too,
            # the current-hub itself would be falsely promoted.
            def _is_current_hub(h: Any) -> bool:
                name = _hub_name(h)
                if not name:
                    return False
                return normalize_entity_text(name, admission_tokens=admission_tokens) == current_hub_norm

            other_hub = None
            if subj_hub and not _is_current_hub(subj_hub):
                other_hub = subj_hub
            elif obj_hub and not _is_current_hub(obj_hub):
                other_hub = obj_hub
            if other_hub is None:
                continue
            key = _hub_name(other_hub)
            if key and key not in seen_spokes:
                seen_spokes.add(key)
                spokes_promoted.append({"canonical_name": key})

    return {"bridges_created": bridges_created, "spokes_promoted": spokes_promoted}


def build_vis_data(kg: dict, confidence_floor: float = 0.55) -> dict:
    """Build vis.js nodes and edges from kg.json data.

    Args:
        kg: Knowledge graph dict with entities and relationships.
        confidence_floor: Minimum entity confidence to include (default: 0.55).
            Entities below this threshold are excluded. Relationships where
            either subject or object is below the floor are also excluded.
    """
    nodes_map = {}  # (type, norm_text) -> node data
    edges = []

    # Collect all entities — only actor types are graph nodes (ADR-001: value types are edge metadata)
    from kgspin_demo_app.services.entity_resolution import normalize_entity_text
    for ent in kg.get("entities", []):
        # Sprint 89: Confidence floor filter
        if ent.get("confidence", 0) < confidence_floor:
            continue
        etype = ent.get("entity_type", "UNKNOWN")
        # Sprint 90: Value types (MONEY, DATE, etc.) are edge metadata, not graph nodes
        if etype not in _VIS_ACTOR_TYPES:
            continue
        norm = normalize_entity_text(ent.get("text", ""))
        key = (etype, norm)
        if key not in nodes_map:
            nodes_map[key] = {
                "text": ent.get("text", norm),
                "entity_type": etype,
                "confidence": ent.get("confidence", 0),
                "mention_count": 1,
                "sources": set(),
            }
        else:
            nodes_map[key]["mention_count"] += 1
            if ent.get("confidence", 0) > nodes_map[key]["confidence"]:
                nodes_map[key]["confidence"] = ent["confidence"]
        # Sprint 33.17 (WI-1): Track source_document provenance for source filtering
        nodes_map[key]["sources"].add(
            ent.get("evidence", {}).get("source_document", "unknown")
        )
        # Sprint 33 (VP R1): Capture canonical_id for Global Identity badge
        if ent.get("canonical_id") and not nodes_map[key].get("canonical_id"):
            nodes_map[key]["canonical_id"] = ent["canonical_id"]

    # Assign numeric IDs
    node_id_map = {}
    vis_nodes = []
    for idx, (key, data) in enumerate(nodes_map.items()):
        node_id_map[key] = idx
        color = TYPE_COLORS.get(data["entity_type"], DEFAULT_COLOR)
        label = data["text"][:30]
        size = max(15, min(50, 10 + data["mention_count"] * 3))
        tooltip = (
            f"{data['text']}\n"
            f"Type: {data['entity_type']}\n"
            f"Mentions: {data['mention_count']}\n"
            f"Confidence: {data['confidence']:.2f}"
        )
        vis_nodes.append(
            {
                "id": idx,
                "label": label,
                "title": tooltip,
                "color": {"background": color, "border": "#FFFFFF"},
                "borderWidth": 2,
                "size": size,
                "font": {"size": 12, "color": "#FFFFFF"},
                "metadata": {
                    "text": data["text"],
                    "entity_type": data["entity_type"],
                    "confidence": data["confidence"],
                    "mention_count": data["mention_count"],
                    "canonical_id": data.get("canonical_id"),
                    "sources": sorted(data.get("sources", set())),
                },
            }
        )

    # Collect relationships
    for rel in kg.get("relationships", []):
        subj = rel.get("subject", {})
        obj = rel.get("object", {})
        pred = rel.get("predicate", "")
        extraction_method = rel.get("extraction_method", "semantic_fingerprint")
        subj_key = (
            subj.get("entity_type", "UNKNOWN"),
            normalize_entity_text(subj.get("text", "")),
        )
        obj_key = (
            obj.get("entity_type", "UNKNOWN"),
            normalize_entity_text(obj.get("text", "")),
        )

        src_id = node_id_map.get(subj_key)
        if src_id is None and subj_key[0] in _BASE_TYPE_MAP:
            src_id = node_id_map.get((_BASE_TYPE_MAP[subj_key[0]], subj_key[1]))
        tgt_id = node_id_map.get(obj_key)
        if tgt_id is None and obj_key[0] in _BASE_TYPE_MAP:
            tgt_id = node_id_map.get((_BASE_TYPE_MAP[obj_key[0]], obj_key[1]))
        if src_id is None or tgt_id is None or src_id == tgt_id:
            continue

        conf = rel.get("confidence", 0)
        evidence = rel.get("evidence", {})
        ev_text_full = evidence.get("sentence_text", "") if isinstance(evidence, dict) else ""
        ev_text = ev_text_full[:200]
        ev_chunk_id = evidence.get("chunk_id", "") if isinstance(evidence, dict) else ""
        # Sprint 33.17 (WI-1): Track source_document for edge filtering
        source_doc = evidence.get("source_document", "unknown") if isinstance(evidence, dict) else "unknown"
        rel_metadata = rel.get("metadata", {})

        # Sprint 39 D3: Collect additional evidence sentences from deduplication
        additional_ev = rel.get("additional_evidence", [])
        additional_ev_texts = []
        for ae in additional_ev:
            ae_text = ae.get("sentence_text", "") if isinstance(ae, dict) else ""
            if ae_text and ae_text != ev_text_full:
                additional_ev_texts.append(ae_text)

        # Build label — include metadata values (e.g., amount, count, penalty, date)
        edge_label = pred
        if rel_metadata.get("amount"):
            edge_label = f"{pred} ({rel_metadata['amount']})"
        elif rel_metadata.get("count"):
            edge_label = f"{pred} ({rel_metadata['count']})"
        elif rel_metadata.get("penalty"):
            edge_label = f"{pred} ({rel_metadata['penalty']})"
        elif rel_metadata.get("date"):
            edge_label = f"{pred} ({rel_metadata['date']})"

        tooltip = f"{pred}\nConfidence: {conf:.2f}"
        if rel_metadata:
            for mk, mv in rel_metadata.items():
                tooltip += f"\n{mk}: {mv}"
        if ev_text:
            tooltip += f"\nEvidence: {ev_text}"
        if additional_ev_texts:
            tooltip += f"\n+{len(additional_ev_texts)} additional evidence sentences"

        # Color by relationship type — deterministic from name hash
        edge_color = _rel_color(pred)

        # Sprint 33 (Item 3): Visual differentiation for structural/table extractions
        is_structural = extraction_method == "table_extraction"
        if is_structural:
            edge_color = "#C49A6C"  # Bronze — distinct from all rel_colors

        edges.append(
            {
                "from": src_id,
                "to": tgt_id,
                "label": edge_label,
                "title": tooltip,
                "arrows": "to",
                "dashes": [6, 3] if is_structural else False,
                "font": {
                    "size": 10,
                    "align": "middle",
                    "color": "#CCCCCC",
                    "strokeWidth": 0,
                },
                "color": {"color": edge_color, "highlight": edge_color, "opacity": 0.9},
                "metadata": {
                    "predicate": pred,
                    "confidence": conf,
                    "evidence_text": ev_text,
                    "source_document": source_doc,
                    "subject_id": src_id,
                    "object_id": tgt_id,
                    "subject_text": subj.get("text", ""),
                    "object_text": obj.get("text", ""),
                    "rel_metadata": rel_metadata,
                    "extraction_method": extraction_method,
                    "full_evidence_text": ev_text_full,
                    "chunk_id": ev_chunk_id,
                    # Sprint 39 D3: Aggregated evidence from merged duplicate triples
                    "additional_evidence_texts": additional_ev_texts,
                },
            }
        )

    # Sprint 33.15 (WI-3): Mark singleton nodes as hidden (no edges).
    # Frontend toggle can unhide them on demand.
    connected_ids = set()
    for e in edges:
        connected_ids.add(e["from"])
        connected_ids.add(e["to"])
    for node in vis_nodes:
        if node["id"] not in connected_ids:
            node["hidden"] = True

    return {"nodes": vis_nodes, "edges": edges}


# --- Diagnostic Scoring Engine ---


_BASE_TYPE_MAP = {
    "EXECUTIVE": "PERSON", "EMPLOYEE": "PERSON",
    "COMPANY": "ORGANIZATION", "REGULATOR": "ORGANIZATION",
    "OFFICE": "LOCATION", "MARKET": "LOCATION",
    "BRANDED_PRODUCT": "PRODUCT",
}


def _entity_key(entity: dict, company_name: str = "") -> tuple:
    """(normalized_text, base_type) for cross-system comparison.

    When company_name is provided, coreference tokens ("we", "the company")
    are normalized to the company name for fair comparison.
    """
    from kgspin_core.models.coreference import normalize_coreference
    raw_text = entity.get("text", "")
    if company_name:
        text = normalize_coreference(raw_text, company_name)
    else:
        text = re.sub(r"\s+", " ", raw_text.lower().strip())
    etype = entity.get("entity_type", "UNKNOWN")
    return (text, _BASE_TYPE_MAP.get(etype, etype))


def _relationship_key(rel: dict, company_name: str = "") -> tuple:
    """(norm_subject, predicate, norm_object) for cross-system comparison.

    Order is preserved — (Pfizer, acquired, Seagen) != (Seagen, acquired, Pfizer).
    This is correct for asymmetric predicates like 'acquired', 'leads', 'regulated_by'.
    When company_name is provided, coreference tokens are normalized.
    """
    from kgspin_core.models.coreference import normalize_coreference
    raw_subj = rel.get("subject", {}).get("text", "")
    raw_obj = rel.get("object", {}).get("text", "")
    if company_name:
        subj = normalize_coreference(raw_subj, company_name)
        obj = normalize_coreference(raw_obj, company_name)
    else:
        subj = re.sub(r"\s+", " ", raw_subj.lower().strip())
        obj = re.sub(r"\s+", " ", raw_obj.lower().strip())
    return (subj, rel.get("predicate", ""), obj)


def _pairwise_scores(ents_a: set, rels_a: set, ents_b: set, rels_b: set) -> dict:
    """Compute pairwise overlap between two entity/rel sets."""
    ent_overlap = len(ents_a & ents_b)
    rel_overlap = len(rels_a & rels_b)
    return {
        "a_entities": len(ents_a),
        "a_relationships": len(rels_a),
        "b_entities": len(ents_b),
        "b_relationships": len(rels_b),
        "entity_overlap": ent_overlap,
        "relationship_overlap": rel_overlap,
        "a_only_entities": len(ents_a - ents_b),
        "a_only_relationships": len(rels_a - rels_b),
        "b_only_entities": len(ents_b - ents_a),
        "b_only_relationships": len(rels_b - rels_a),
        "entity_consensus": round(ent_overlap / len(ents_b), 3) if ents_b else 1.0,
        "rel_consensus": round(rel_overlap / len(rels_b), 3) if rels_b else 1.0,
    }


def compute_diagnostic_scores(
    kgs_kg: dict,
    mod_kg: Optional[dict] = None,
    gem_kg: Optional[dict] = None,
    company_name: str = "",
) -> dict:
    """Compute 3-way pairwise Performance Delta metrics.

    Returns pairwise comparisons between all available pipelines:
    - kgs_vs_multistage, kgs_vs_fullshot, multistage_vs_fullshot
    Each pair includes overlap counts, consensus rates, and exclusive counts.
    """
    kgs_ents = {_entity_key(e, company_name) for e in kgs_kg.get("entities", [])}
    kgs_rels = {_relationship_key(r, company_name) for r in kgs_kg.get("relationships", [])}

    mod_ents = {_entity_key(e, company_name) for e in (mod_kg or {}).get("entities", [])} if mod_kg else set()
    mod_rels = {_relationship_key(r, company_name) for r in (mod_kg or {}).get("relationships", [])} if mod_kg else set()

    gem_ents = {_entity_key(e, company_name) for e in (gem_kg or {}).get("entities", [])} if gem_kg else set()
    gem_rels = {_relationship_key(r, company_name) for r in (gem_kg or {}).get("relationships", [])} if gem_kg else set()

    result = {"pairs": {}}

    if mod_kg:
        result["pairs"]["kgs_vs_multistage"] = _pairwise_scores(kgs_ents, kgs_rels, mod_ents, mod_rels)
    if gem_kg:
        result["pairs"]["kgs_vs_fullshot"] = _pairwise_scores(kgs_ents, kgs_rels, gem_ents, gem_rels)
    if mod_kg and gem_kg:
        result["pairs"]["multistage_vs_fullshot"] = _pairwise_scores(mod_ents, mod_rels, gem_ents, gem_rels)

    return result


# --- Reproducibility Variance ---


def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets. Returns 1.0 if both empty."""
    union = set_a | set_b
    return len(set_a & set_b) / len(union) if union else 1.0


def _compute_run_variance(runs: list, company_name: str = "") -> dict:
    """Compute pairwise Jaccard variance across multiple KG extraction runs."""
    if len(runs) < 2:
        return {"variance_pct": 0.0, "num_runs": len(runs), "insufficient": True}
    entity_sets, rel_sets = [], []
    for run in runs:
        kg = run.get("kg", {})
        ents = {_entity_key(e, company_name) for e in kg.get("entities", [])}
        rels = {_relationship_key(r, company_name) for r in kg.get("relationships", [])}
        entity_sets.append(ents)
        rel_sets.append(rels)
    ej, rj = [], []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            ej.append(_jaccard(entity_sets[i], entity_sets[j]))
            rj.append(_jaccard(rel_sets[i], rel_sets[j]))
    avg_entity_sim = sum(ej) / len(ej) if ej else 1.0
    avg_rel_sim = sum(rj) / len(rj) if rj else 1.0
    avg_sim = (avg_entity_sim + avg_rel_sim) / 2
    return {
        "variance_pct": round((1 - avg_sim) * 100, 1),
        "avg_entity_similarity": round(avg_entity_sim * 100, 1),
        "avg_rel_similarity": round(avg_rel_sim * 100, 1),
        "num_runs": len(runs),
        "insufficient": False,
    }


# --- Quality Analysis ---


def _load_valid_entity_types() -> set:
    """Load valid entity type names from the patterns YAML schema."""
    import yaml as _yaml
    try:
        with open(PATTERNS_PATH) as f:
            _patterns = _yaml.safe_load(f)
        _types = _patterns.get("types", {})
        valid = set(_types.keys())
        for info in _types.values():
            valid.update(info.get("subtypes", {}).keys())
        return valid
    except Exception:
        return set()


from prompts import build_quality_analysis_prompt  # noqa: E402


from analysis import run_quality_analysis  # noqa: E402


# --- Main Comparison Pipeline ---


async def run_comparison(
    ticker: str, request: Request, force_refresh: bool = False,
    corpus_kb: int = DEFAULT_CORPUS_KB, chunk_size: int = DEFAULT_CHUNK_SIZE,
    model: str = DEFAULT_GEMINI_MODEL, bundle_name: str | None = None,
    pipeline_id: str | None = None,
    llm_alias: str | None = None,
) -> AsyncGenerator[str, None]:
    """Main comparison pipeline - yields SSE events."""
    pipeline_start = time.time()

    # Send initial comment to "prime" the SSE connection
    yield ": connected\n\n"
    await asyncio.sleep(0)

    # Step 1: Resolve ticker
    yield sse_event("step_start", {"step": "resolve_ticker", "label": "Resolving ticker..."})
    await asyncio.sleep(0)
    t0 = time.time()
    info = await asyncio.to_thread(resolve_ticker, ticker)
    duration = int((time.time() - t0) * 1000)
    yield sse_event(
        "step_complete",
        {
            "step": "resolve_ticker",
            "label": f"Resolved: {ticker} \u2192 {info['name']}",
            "duration_ms": duration,
            "details": info,
        },
    )
    await asyncio.sleep(0)

    # Step 2: Fetch SEC filing
    yield sse_event("step_start", {"step": "fetch_sec", "label": "Fetching SEC 10-K filing..."})
    await asyncio.sleep(0)
    t0 = time.time()

    def _fetch_sec(t):
        try:
            from kgspin_plugin_financial.data_sources.edgar import EdgarDataSource
            edgar_ds = EdgarDataSource(cache_dir=DATA_LAKE_ROOT / "financial" / "sec_edgar")
            doc = edgar_ds.get_document(t, "10-K")
            if doc:
                return doc  # Return full EdgarDocument for metadata
        except Exception as e:
            logger.warning(f"EDGAR fetch failed: {e}")
        return None

    sec_doc = await asyncio.to_thread(_fetch_sec, ticker)

    if not sec_doc:
        yield sse_event(
            "error",
            {
                "step": "fetch_sec",
                "message": f"Could not fetch 10-K for {ticker}. Set EDGAR_IDENTITY env var.",
                "recoverable": False,
            },
        )
        yield sse_event("done", {"total_duration_ms": int((time.time() - pipeline_start) * 1000)})
        return

    sec_html = sec_doc.raw_html
    size_kb = len(sec_html) // 1024
    duration = int((time.time() - t0) * 1000)
    # Sprint 79: Build financial document context for cache backfill
    from kgspin_plugin_financial.domain.plugin import FinancialCorpusPlugin as _FCP
    # Prefer resolved company name over cached ticker-as-name
    _company = info.get("name", "") or sec_doc.company_name or ticker
    _fin_doc_ctx = _FCP.build_document_context(
        {
            "company": _company,
            "doc_id": ticker,
            "source": "SEC 10-K",
            "filing_date": sec_doc.filing_date,
            "cik": sec_doc.cik or "",
            "accession_number": sec_doc.accession_number or "",
            "fiscal_year_end": sec_doc.fiscal_year_end or "",
            "source_url": sec_doc.source_url or "",
        },
        ticker,
    )
    yield sse_event(
        "step_complete",
        {
            "step": "fetch_sec",
            "label": f"Fetched 10-K ({size_kb}KB)",
            "duration_ms": duration,
            "details": {
                "source_url": sec_doc.source_url,
                "filing_date": sec_doc.filing_date,
                "accession_number": sec_doc.accession_number,
                "company_name": _company,
                "doc_id": ticker,
                "size_kb": size_kb,
            },
        },
    )
    await asyncio.sleep(0)

    # Step 3: Parse HTML to text + byte-based truncation + chunk
    yield sse_event("step_start", {"step": "parse_text", "label": f"Parsing text ({corpus_kb}KB corpus)..."})
    await asyncio.sleep(0)
    t0 = time.time()

    bundle, full_text, demo_text, actual_kb, all_chunks = await asyncio.to_thread(
        _parse_and_chunk, sec_html, ticker, corpus_kb, bundle_name
    )

    duration = int((time.time() - t0) * 1000)
    total_chars = len(demo_text)
    label = f"Corpus: {actual_kb:.0f}KB in {len(all_chunks)} chunks"

    yield sse_event(
        "step_complete",
        {
            "step": "parse_text",
            "label": label,
            "duration_ms": duration,
            "details": {
                "num_chunks": len(all_chunks),
                "total_chars": total_chars,
                "corpus_kb": round(actual_kb, 1),
                "requested_kb": corpus_kb,
            },
        },
    )
    await asyncio.sleep(0)

    if await request.is_disconnected():
        return

    # Steps 4a + 4b: Run KGSpin and Gemini pipelines IN PARALLEL
    gemini_available = bool(
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY")
    )
    gem_tokens = 0

    # Build cache keys using shared helpers (backward-compatible key format)
    # Sprint 118: Use split bundle ID for KGSpin cache when using split bundles
    _is_split = (bundle_name and pipeline_id and DOMAIN_BUNDLES_DIR.is_dir()
                 and (DOMAIN_BUNDLES_DIR / bundle_name).is_dir())
    if _is_split:
        _kgen_bid = _split_bundle_id(bundle_name, pipeline_id)
    else:
        _kgen_bid = bundle_name or BUNDLE_PATH.name
    # Sprint 118: Resolve domain-specific paths for LLM extractors so they
    # use the same entity types and relationship patterns as the selected domain.
    _llm_bundle_path = BUNDLE_PATH
    _llm_patterns_path = PATTERNS_PATH
    if _is_split:
        try:
            _llm_bundle_path = resolve_domain_bundle_path(bundle_name)
            _llm_patterns_path = resolve_domain_yaml_path(bundle_name)
        except FileNotFoundError:
            pass  # fall back to defaults
    _cfg_keys = _build_pipeline_cache_keys(
        _llm_bundle_path, _llm_patterns_path, corpus_kb, model,
        bundle_name=_kgen_bid, domain_id=bundle_name if _is_split else "",
        pipeline_id=pipeline_id,
    )
    _gem_cfg_hash = _cfg_keys["gemini"]
    _cfg_hash = _gem_cfg_hash  # alias for backwards compat in _kg_cache
    _mod_cfg_hash = _cfg_keys["modular"]
    _kgen_cfg_hash = _cfg_keys["kgen"]

    # Check for cached runs using shared helpers
    kgen_from_log, kgen_logged_run = _cache_lookup("kgen", ticker, _kgen_cfg_hash, force_refresh=force_refresh)
    gem_from_log, gem_logged_run = (
        _cache_lookup("gemini", ticker, _gem_cfg_hash, force_refresh=force_refresh)
        if gemini_available else (False, None)
    )
    mod_from_log, mod_logged_run = (
        _cache_lookup("modular", ticker, _mod_cfg_hash, force_refresh=force_refresh)
        if gemini_available else (False, None)
    )

    # -- Start KGSpin --
    kgs_t0 = time.time()

    # Defect 1 (2026-04-24): Build document metadata once so every
    # extractor-dispatch path (zero-LLM + agentic) can pass it as the
    # H-module resolver's ``company_name`` override. Without this, the
    # agentic pipelines fall back to an ALL-CAPS regex that misses
    # mixed-case filers (UnitedHealth Group, Apple Inc., NVIDIA
    # Corporation, etc.) and the coref map becomes we → UNKNOWN.
    _doc_metadata = {
        "company_name": _company,
        "doc_id": ticker,
        "cik": (sec_doc.cik if sec_doc else "") or "",
        "accession_number": (sec_doc.accession_number if sec_doc else "") or "",
        "filing_date": (sec_doc.filing_date if sec_doc else "") or "",
        "fiscal_year_end": (sec_doc.fiscal_year_end if sec_doc else "") or "",
    }

    # Unified progress queue: (pipeline, chunk_idx, total, metric_value)
    progress_queue = asyncio.Queue()

    def on_kgs_chunk_done(chunk_idx, total, entities_so_far):
        progress_queue.put_nowait(("kgenskills", chunk_idx, total, entities_so_far))

    # Full Shot has no chunk callbacks (single prompt)

    def on_mod_chunk_done(chunk_idx, total, tokens_so_far):
        progress_queue.put_nowait(("modular", chunk_idx, total, tokens_so_far))

    # Sprint 28: L-Module progress callback
    def on_l_module_start(num_chunks):
        progress_queue.put_nowait(("kgenskills_l_module", 0, num_chunks, 0))

    # Sprint 30: Table extraction progress callback
    def on_table_extraction_start():
        progress_queue.put_nowait(("table_extraction", 0, 1, 0))

    def on_table_extraction_done():
        progress_queue.put_nowait(("table_extraction", 1, 1, 0))

    # Sprint 39: Post-chunk progress (table extraction + cross-chunk stitching)
    def on_post_chunk_progress(stage, idx, total):
        progress_queue.put_nowait(("table_extraction", idx, total, 0, stage))

    # Sprint 33.10: Serve KGSpin from disk cache if available
    kgs_task = None
    if kgen_from_log:
        kgs_kg = kgen_logged_run["kg"]
        # Sprint 79: Backfill document_context for older cached financial KGs
        if _fin_doc_ctx and not kgs_kg.get("document_context"):
            kgs_kg["document_context"] = _fin_doc_ctx
        kgs_elapsed = kgen_logged_run.get("elapsed_seconds", 0)
        kgs_vis = build_vis_data(kgs_kg)
        kgs_entities = len(kgs_vis["nodes"])
        kgs_rels = len(kgs_vis["edges"])
        # Sprint 101: Quarantine count from precision pass.
        kgs_quarantine_count = kgs_kg.get("_quarantine_count", 0)
        kgs_throughput = actual_kb / kgs_elapsed if kgs_elapsed > 0 else 0
        kgs_cpu_cost = (kgs_elapsed / 3600) * _CPU_COST_PER_HOUR
        kgs_est_chunks = max(1, round(actual_kb * 1024 / chunk_size))
        # Sprint 33.17: KGSpin run count for history bar
        kgen_run_count = _kgen_run_log.count(ticker, _kgen_cfg_hash)
        kgen_run_timestamp = kgen_logged_run.get("created_at", "")

        yield sse_event(
            "step_complete",
            {
                "step": "kgenskills",
                "pipeline": "kgenskills",
                "label": f"KGSpin: Loaded from cache ({kgs_entities} entities, {kgs_rels} relationships)",
                "duration_ms": 0,
                "tokens": 0,
            },
        )
        await asyncio.sleep(0)
        yield sse_event(
            "kg_ready",
            {
                "pipeline": "kgenskills",
                "bundle_version": kgen_logged_run.get("bundle_version", _kgen_bid),
                "stats": {
                    "entities": kgs_entities,
                    "relationships": kgs_rels,
                    "tokens": 0,
                    "duration_ms": int(kgs_elapsed * 1000),
                    "throughput_kb_sec": round(kgs_throughput, 1),
                    "cpu_cost": round(kgs_cpu_cost, 6),
                    "num_chunks": kgs_est_chunks,
                    "actual_kb": round(actual_kb, 1),
                    "quarantine_count": kgs_quarantine_count,
                },
                "vis": kgs_vis,
                "from_log": True,
                "run_index": 0,
                "total_runs": kgen_run_count,
                "run_timestamp": kgen_run_timestamp,
                **({"document_context": kgs_kg["document_context"]} if kgs_kg.get("document_context") else {}),
            },
        )
        await asyncio.sleep(0)
        # Cache for impact tab reuse
        with _cache_lock:
            _kg_cache[ticker] = {
                "kgs_kg": kgs_kg,
                "text": demo_text,
                "raw_html": sec_html,
                "info": info,
                "corpus_kb": corpus_kb,
                "actual_kb": actual_kb,
                "cfg_hash": _cfg_hash,
                "chunk_size": chunk_size,
                "bundle_version": kgen_logged_run.get("bundle_version", _kgen_bid),
                "kgs_stats": {
                    "entities": kgs_entities, "relationships": kgs_rels,
                    "duration_ms": int(kgs_elapsed * 1000), "cpu_cost": round(kgs_cpu_cost, 6),
                    "num_chunks": kgs_est_chunks, "throughput_kb_sec": round(kgs_throughput, 1),
                },
            }
    else:
        # No cached run — auto-run KGSpin (zero-token, always fast)
        yield sse_event(
            "step_start",
            {"step": "kgenskills", "pipeline": "kgenskills", "label": "KGSpin: Extracting..."},
        )
        await asyncio.sleep(0)
        # Sprint 102: Build document metadata dict for seed fact resolution.
        # Defect 1 (2026-04-24): `_doc_metadata` is now built unconditionally
        # above so every pipeline dispatch path (kgen + agentic) can pass it.
        kgs_task = asyncio.create_task(
            asyncio.to_thread(
                _run_kgenskills, demo_text, info["name"], ticker, bundle,
                _pipeline_ref_from_pipeline_id(pipeline_id),
                _get_registry_client(),
                on_kgs_chunk_done, sec_html, on_l_module_start,
                on_table_extraction_start, on_table_extraction_done,
                on_post_chunk_progress, _doc_metadata,
            )
        )

    # -- Start Gemini (if available) --
    gemini_task = None
    gem_t0 = time.time()
    if gem_from_log:
        # Serve from logged run instantly
        gem_kg = gem_logged_run["kg"]
        gem_tokens = gem_logged_run.get("total_tokens", 0)
        gem_elapsed = gem_logged_run.get("elapsed_seconds", 0)
        gem_truncated = gem_kg.get("provenance", {}).get("truncated", False)
        run_count = _run_log.count(ticker, _cfg_hash)
        run_timestamp = gem_logged_run.get("created_at", "")

        yield sse_event(
            "step_complete",
            {
                "step": "gemini",
                "pipeline": "gemini",
                "label": f"LLM Full Shot: Loaded from log ({run_count} runs available)",
                "duration_ms": 0,
                "tokens": gem_tokens,
            },
        )
        await asyncio.sleep(0)
        gem_vis = build_vis_data(gem_kg)
        gem_entities = len(gem_vis["nodes"])
        gem_rels = len(gem_vis["edges"])
        gem_throughput = actual_kb / gem_elapsed if gem_elapsed > 0 else 0
        yield sse_event(
            "kg_ready",
            {
                "pipeline": "gemini",
                "stats": {
                    "entities": gem_entities,
                    "relationships": gem_rels,
                    "tokens": gem_tokens,
                    "duration_ms": int(gem_elapsed * 1000),
                    "throughput_kb_sec": round(gem_throughput, 1),
                    "actual_kb": round(actual_kb, 1),
                },
                "vis": gem_vis,
                "from_log": True,
                "run_index": 0,
                "total_runs": run_count,
                "run_timestamp": run_timestamp,
                "cache_version": gem_logged_run.get("demo_cache_version", ""),
                "truncated": gem_truncated,
                "model": model,
                "model_pricing": GEMINI_MODEL_PRICING[model],
            },
        )
        await asyncio.sleep(0)

        # Sprint 33.11: Cache from-log Gemini KG for analytics refresh
        with _cache_lock:
            if ticker in _kg_cache:
                _kg_cache[ticker]["gem_kg"] = gem_kg
                _kg_cache[ticker]["gem_tokens"] = gem_tokens
                _kg_cache[ticker]["gem_stats"] = {
                    "entities": gem_entities, "relationships": gem_rels,
                    "tokens": gem_tokens, "duration_ms": int(gem_elapsed * 1000),
                    "throughput_kb_sec": round(gem_throughput, 1),
                }

    elif not gemini_available:
        yield sse_event(
            "error",
            {
                "step": "gemini",
                "pipeline": "gemini",
                "message": "GEMINI_API_KEY not set. Set the environment variable to run LLM comparison.",
                "recoverable": False,
            },
        )
        await asyncio.sleep(0)
    else:
        yield sse_event(
            "step_start",
            {"step": "gemini", "pipeline": "gemini", "label": "LLM Full Shot: Extracting..."},
        )
        await asyncio.sleep(0)
        gemini_task = asyncio.create_task(
            asyncio.to_thread(
                _run_agentic_flash,
                demo_text,
                info["name"],
                f"{ticker}_10K",
                None if llm_alias else model,
                _llm_bundle_path,
                _llm_patterns_path,
                llm_alias=llm_alias,
                document_metadata=_doc_metadata,
            )
        )

    # -- Start LLM Multi-Stage (System B, if available) --
    modular_task = None
    mod_t0 = time.time()
    mod_tokens = 0
    if mod_from_log:
        mod_kg = mod_logged_run["kg"]
        mod_tokens = mod_logged_run.get("total_tokens", 0)
        mod_elapsed = mod_logged_run.get("elapsed_seconds", 0)
        mod_run_count = _modular_run_log.count(ticker, _mod_cfg_hash)
        mod_run_timestamp = mod_logged_run.get("created_at", "")

        yield sse_event(
            "step_complete",
            {
                "step": "modular",
                "pipeline": "modular",
                "label": f"LLM Multi-Stage: Loaded from log ({mod_run_count} runs available)",
                "duration_ms": 0,
                "tokens": mod_tokens,
            },
        )
        await asyncio.sleep(0)
        mod_vis = build_vis_data(mod_kg)
        mod_entities = len(mod_vis["nodes"])
        mod_rels = len(mod_vis["edges"])
        mod_throughput = actual_kb / mod_elapsed if mod_elapsed > 0 else 0
        yield sse_event(
            "kg_ready",
            {
                "pipeline": "modular",
                "stats": {
                    "entities": mod_entities,
                    "relationships": mod_rels,
                    "tokens": mod_tokens,
                    "h_tokens": mod_logged_run.get("kg", {}).get("provenance", {}).get("h_tokens", 0),
                    "l_tokens": mod_logged_run.get("kg", {}).get("provenance", {}).get("l_tokens", 0),
                    "duration_ms": int(mod_elapsed * 1000),
                    "throughput_kb_sec": round(mod_throughput, 1),
                    "chunks_completed": mod_kg.get("provenance", {}).get("chunks_completed", 0),
                    "chunks_total": mod_kg.get("provenance", {}).get("chunks_total", 0),
                    "actual_kb": round(actual_kb, 1),
                },
                "vis": mod_vis,
                "from_log": True,
                "run_index": 0,
                "total_runs": mod_run_count,
                "run_timestamp": mod_run_timestamp,
                "cache_version": mod_logged_run.get("demo_cache_version", ""),
                "model": model,
                "model_pricing": GEMINI_MODEL_PRICING[model],
            },
        )
        await asyncio.sleep(0)

        # Sprint 33.11: Cache from-log Modular KG for analytics refresh
        with _cache_lock:
            if ticker in _kg_cache:
                _kg_cache[ticker]["mod_kg"] = mod_kg
                _kg_cache[ticker]["mod_tokens"] = mod_tokens
                _kg_cache[ticker]["mod_stats"] = {
                    "entities": mod_entities, "relationships": mod_rels,
                    "tokens": mod_tokens, "duration_ms": int(mod_elapsed * 1000),
                    "throughput_kb_sec": round(mod_throughput, 1),
                    "chunks_total": mod_kg.get("provenance", {}).get("chunks_total", 0),
                }

    elif not gemini_available:
        pass  # No API key — modular also unavailable
    else:
        yield sse_event(
            "step_start",
            {"step": "modular", "pipeline": "modular", "label": "LLM Multi-Stage: Extracting..."},
        )
        await asyncio.sleep(0)
        # Sprint 33.6: Create cancel event for Multi-Stage
        _cancel_event = threading.Event()
        _modular_cancel_events[ticker] = _cancel_event
        modular_task = asyncio.create_task(
            asyncio.to_thread(
                _run_agentic_analyst,
                demo_text,
                info["name"],
                f"{ticker}_10K",
                on_mod_chunk_done,
                _cancel_event,
                chunk_size,
                None if llm_alias else model,
                _llm_bundle_path,
                _llm_patterns_path,
                llm_alias=llm_alias,
                document_metadata=_doc_metadata,
            )
        )

    # -- Unified progress polling loop (all pipelines) --
    kgs_done = kgs_task is None  # already "done" if served from cache
    gem_done = gemini_task is None  # already "done" if not available or from log
    mod_done = modular_task is None  # already "done" if not available or from log
    kgen_num_chunks = 0  # captured from progress callbacks for optimal latency

    while not (kgs_done and gem_done and mod_done):
        # Check task completion — KGSpin
        if not kgs_done and kgs_task and kgs_task.done():
            kgs_done = True
            try:
                kgs_kg = kgs_task.result()
            except Exception as e:
                # INIT-001 Sprint 02 / BUG-010 response: log full traceback at
                # every slot entry point in the unified compare endpoint, same
                # discipline as the per-slot refresh endpoints.
                logger.exception("KGSpin compare slot failed")
                yield sse_event("error", {
                    "step": "kgenskills", "pipeline": "kgenskills",
                    "message": f"KGSpin failed: {e}",
                    "recoverable": True,
                })
                await asyncio.sleep(0)
                continue  # skip the rest of this slot's display block
            kgs_elapsed = time.time() - kgs_t0
            kgs_duration = int(kgs_elapsed * 1000)
            kgs_throughput = actual_kb / kgs_elapsed if kgs_elapsed > 0 else 0
            # Fallback: estimate num_chunks if progress events weren't polled in time
            if kgen_num_chunks == 0 and actual_kb > 0:
                kgen_num_chunks = max(1, round(actual_kb * 1024 / chunk_size))
            kgs_vis = build_vis_data(kgs_kg)
            # Sprint 33.15b: Report post-filter vis counts (Bug 3)
            kgs_entities = len(kgs_vis["nodes"])
            kgs_rels = len(kgs_vis["edges"])
            yield sse_event(
                "step_complete",
                {
                    "step": "kgenskills",
                    "pipeline": "kgenskills",
                    "label": f"KGSpin: {kgs_entities} entities, {kgs_rels} relationships",
                    "duration_ms": kgs_duration,
                    "tokens": 0,
                },
            )
            await asyncio.sleep(0)
            # Sprint 33.6: KGSpin CPU cost estimation
            kgs_cpu_cost = (kgs_elapsed / 3600) * _CPU_COST_PER_HOUR
            # Cache KG + context for impact tab reuse and per-column refresh (thread-safe)
            with _cache_lock:
                _kg_cache[ticker] = {
                    "kgs_kg": kgs_kg,
                    "text": demo_text,
                    "raw_html": sec_html,
                    "info": info,
                    "corpus_kb": corpus_kb,
                    "actual_kb": actual_kb,
                    "cfg_hash": _cfg_hash,
                    "chunk_size": chunk_size,
                    "bundle_version": _kgen_bid,
                    "kgs_stats": {
                        "entities": kgs_entities, "relationships": kgs_rels,
                        "duration_ms": kgs_duration, "cpu_cost": round(kgs_cpu_cost, 6),
                        "num_chunks": kgen_num_chunks, "throughput_kb_sec": round(kgs_throughput, 1),
                    },
                }
            # Sprint 79: Inject financial document_context
            if _fin_doc_ctx and "document_context" not in kgs_kg:
                kgs_kg["document_context"] = _fin_doc_ctx
            # Sprint 33.10: Log KGSpin result to disk cache
            _cache_save("kgen", ticker, _kgen_cfg_hash, kgs_kg,
                         tokens=0, elapsed=kgs_elapsed,
                         model_fallback="kgen_deterministic",
                         bundle_version=_kgen_bid,
                         document_context=_fin_doc_ctx,
                         actual_kb=actual_kb)
            # Sprint 33.17: Include total_runs for history bar
            _kgen_total = _kgen_run_log.count(ticker, _kgen_cfg_hash)
            yield sse_event(
                "kg_ready",
                {
                    "pipeline": "kgenskills",
                    "bundle_version": _kgen_bid,
                    "stats": {
                        "entities": kgs_entities,
                        "relationships": kgs_rels,
                        "tokens": 0,
                        "duration_ms": kgs_duration,
                        "throughput_kb_sec": round(kgs_throughput, 1),
                        "cpu_cost": round(kgs_cpu_cost, 6),
                        "num_chunks": kgen_num_chunks,
                        "actual_kb": round(actual_kb, 1),
                    },
                    "vis": kgs_vis,
                    "total_runs": _kgen_total,
                },
            )
            await asyncio.sleep(0)

        # Check task completion — LLM Full Shot
        if not gem_done and gemini_task and gemini_task.done():
            gem_done = True
            try:
                gem_kg, gem_tokens, gem_elapsed, gem_errors, gem_truncated = gemini_task.result()
            except Exception as e:
                # Wave 3 follow-up: cache an explicit failure for this
                # slot so the UI's replay path + analysis agent both see
                # a structured "failed" state instead of an empty slot.
                logger.exception("LLM Full Shot compare slot failed")
                _flash_reason, _flash_error_type = _classify_llm_error(e)
                _flash_elapsed = time.time() - gem_t0
                failure_kg = {
                    "entities": [], "relationships": [], "derived_facts": [],
                    "status": "failed",
                    "error": {
                        "type": _flash_error_type, "message": str(e),
                        "exception_class": type(e).__name__,
                        "reason": _flash_reason,
                    },
                    "provenance": {
                        "model": model,
                        "corpus_kb": round(actual_kb, 1),
                        "tokens_used": 0, "llm_calls": 0,
                        "status": "failed",
                    },
                }
                try:
                    _run_log.log_run(
                        ticker, _cfg_hash, failure_kg,
                        0, _flash_elapsed, model,
                        cache_version=DEMO_CACHE_VERSION,
                    )
                except Exception as log_exc:
                    logger.warning(f"Failed to cache Flash failure: {log_exc}")
                yield sse_event("error", {
                    "step": "gemini", "pipeline": "gemini",
                    "message": f"LLM Full Shot failed: {e}",
                    "reason": _flash_reason,
                    "error_type": _flash_error_type,
                    "exception_class": type(e).__name__,
                    "recoverable": False,
                })
                await asyncio.sleep(0)
                continue
            gem_duration = int((time.time() - gem_t0) * 1000)

            # Log this live Gemini run to disk (Sprint 33.5: skip empty/truncated results)
            gem_kg.setdefault("provenance", {})["corpus_kb"] = round(actual_kb, 1)
            _gem_raw_entities = len(gem_kg.get("entities", []))
            if (_gem_raw_entities > 0 or gem_errors == 0) and not gem_truncated:
                try:
                    _gem_model = gem_kg.get("provenance", {}).get("model", "gemini")
                    _run_log.log_run(
                        ticker, _cfg_hash, gem_kg,
                        gem_tokens, gem_elapsed, _gem_model,
                        cache_version=DEMO_CACHE_VERSION,
                    )
                except Exception as e:
                    logger.warning(f"Failed to log Gemini run: {e}")
            else:
                logger.warning(f"Skipping Gemini cache: {_gem_raw_entities} entities, {gem_errors} errors, truncated={gem_truncated}")
            run_count = _run_log.count(ticker, _cfg_hash)

            gem_vis = build_vis_data(gem_kg)
            gem_entities = len(gem_vis["nodes"])
            gem_rels = len(gem_vis["edges"])
            gem_throughput = actual_kb / gem_elapsed if gem_elapsed > 0 else 0
            yield sse_event(
                "step_complete",
                {
                    "step": "gemini",
                    "pipeline": "gemini",
                    "label": f"LLM Full Shot: {gem_entities} entities, {gem_rels} relationships",
                    "duration_ms": gem_duration,
                    "tokens": gem_tokens,
                },
            )
            await asyncio.sleep(0)
            yield sse_event(
                "kg_ready",
                {
                    "pipeline": "gemini",
                    "stats": {
                        "entities": gem_entities,
                        "relationships": gem_rels,
                        "tokens": gem_tokens,
                        "duration_ms": gem_duration,
                        "throughput_kb_sec": round(gem_throughput, 1),
                        "actual_kb": round(actual_kb, 1),
                    },
                    "vis": gem_vis,
                    "from_log": False,
                    "run_index": 0,
                    "total_runs": run_count,
                    "run_timestamp": datetime.now(timezone.utc).isoformat(),
                    "cache_version": DEMO_CACHE_VERSION,
                    "errors": gem_errors,
                    "truncated": gem_truncated,
                    "model": model,
                    "model_pricing": GEMINI_MODEL_PRICING[model],
                },
            )
            await asyncio.sleep(0)

            # Sprint 33.11: Cache Gemini KG for analytics refresh
            with _cache_lock:
                if ticker in _kg_cache:
                    _kg_cache[ticker]["gem_kg"] = gem_kg
                    _kg_cache[ticker]["gem_tokens"] = gem_tokens
                    _kg_cache[ticker]["gem_stats"] = {
                        "entities": gem_entities, "relationships": gem_rels,
                        "tokens": gem_tokens, "duration_ms": gem_duration,
                        "throughput_kb_sec": round(gem_throughput, 1) if gem_elapsed > 0 else 0,
                    }

        # Check task completion — Modular (System B)
        if not mod_done and modular_task and modular_task.done():
            mod_done = True
            # Sprint 33.6: Cleanup cancel event
            _modular_cancel_events.pop(ticker, None)
            try:
                mod_kg, h_tokens, l_tokens, mod_elapsed, mod_errors = modular_task.result()
            except Exception as e:
                # Analyst mirror of the Flash failure-cache pattern above.
                logger.exception("LLM Multi-Stage compare slot failed")
                _analyst_reason, _analyst_error_type = _classify_llm_error(e)
                _analyst_elapsed = time.time() - mod_t0
                failure_kg = {
                    "entities": [], "relationships": [], "derived_facts": [],
                    "status": "failed",
                    "error": {
                        "type": _analyst_error_type, "message": str(e),
                        "exception_class": type(e).__name__,
                        "reason": _analyst_reason,
                    },
                    "provenance": {
                        "model": model,
                        "corpus_kb": round(actual_kb, 1),
                        "tokens_used": 0, "llm_calls": 0,
                        "status": "failed",
                    },
                }
                try:
                    _modular_run_log.log_run(
                        ticker, _cfg_hash, failure_kg,
                        0, _analyst_elapsed, model,
                        cache_version=DEMO_CACHE_VERSION,
                    )
                except Exception as log_exc:
                    logger.warning(f"Failed to cache Analyst failure: {log_exc}")
                yield sse_event("error", {
                    "step": "modular", "pipeline": "modular",
                    "message": f"LLM Multi-Stage failed: {e}",
                    "reason": _analyst_reason,
                    "error_type": _analyst_error_type,
                    "exception_class": type(e).__name__,
                    "recoverable": False,
                })
                await asyncio.sleep(0)
                continue
            mod_tokens = h_tokens + l_tokens
            mod_duration = int((time.time() - mod_t0) * 1000)

            # Log this live modular run to disk (Sprint 33.5: skip empty error results)
            mod_kg.setdefault("provenance", {})["corpus_kb"] = round(actual_kb, 1)
            _mod_raw_entities = len(mod_kg.get("entities", []))
            if _mod_raw_entities > 0 or mod_errors == 0:
                try:
                    _mod_model = mod_kg.get("provenance", {}).get("model", "gemini")
                    _modular_run_log.log_run(
                        ticker, _mod_cfg_hash, mod_kg,
                        mod_tokens, mod_elapsed, _mod_model,
                        cache_version=DEMO_CACHE_VERSION,
                    )
                except Exception as e:
                    logger.warning(f"Failed to log modular run: {e}")
            else:
                logger.warning(f"Skipping modular cache: 0 entities with {mod_errors} errors")
            mod_run_count = _modular_run_log.count(ticker, _mod_cfg_hash)

            mod_vis = build_vis_data(mod_kg)
            mod_entities = len(mod_vis["nodes"])
            mod_rels = len(mod_vis["edges"])
            mod_throughput = actual_kb / mod_elapsed if mod_elapsed > 0 else 0
            yield sse_event(
                "step_complete",
                {
                    "step": "modular",
                    "pipeline": "modular",
                    "label": f"LLM Multi-Stage: {mod_entities} entities, {mod_rels} relationships",
                    "duration_ms": mod_duration,
                    "tokens": mod_tokens,
                },
            )
            await asyncio.sleep(0)
            # Sprint 33.6: Include partial result provenance
            _mod_provenance = mod_kg.get("provenance", {})
            yield sse_event(
                "kg_ready",
                {
                    "pipeline": "modular",
                    "stats": {
                        "entities": mod_entities,
                        "relationships": mod_rels,
                        "tokens": mod_tokens,
                        "h_tokens": h_tokens,
                        "l_tokens": l_tokens,
                        "duration_ms": mod_duration,
                        "throughput_kb_sec": round(mod_throughput, 1),
                        "chunks_completed": _mod_provenance.get("chunks_completed", 0),
                        "chunks_total": _mod_provenance.get("chunks_total", 0),
                        "actual_kb": round(actual_kb, 1),
                    },
                    "vis": mod_vis,
                    "from_log": False,
                    "run_index": 0,
                    "total_runs": mod_run_count,
                    "run_timestamp": datetime.now(timezone.utc).isoformat(),
                    "cache_version": DEMO_CACHE_VERSION,
                    "errors": mod_errors,
                    "model": model,
                    "model_pricing": GEMINI_MODEL_PRICING[model],
                },
            )
            await asyncio.sleep(0)

            # Sprint 33.11: Cache Modular KG for analytics refresh
            with _cache_lock:
                if ticker in _kg_cache:
                    _kg_cache[ticker]["mod_kg"] = mod_kg
                    _kg_cache[ticker]["mod_tokens"] = mod_tokens
                    _kg_cache[ticker]["mod_stats"] = {
                        "entities": mod_entities, "relationships": mod_rels,
                        "tokens": mod_tokens, "duration_ms": mod_duration,
                        "throughput_kb_sec": round(mod_throughput, 1) if mod_elapsed > 0 else 0,
                        "chunks_total": _mod_provenance.get("chunks_total", 0),
                    }

        if kgs_done and gem_done and mod_done:
            break

        # Poll progress queue
        try:
            item = await asyncio.wait_for(
                progress_queue.get(), timeout=0.5
            )
            pipeline, chunk_idx, total, metric = item[0], item[1], item[2], item[3]
            stage = item[4] if len(item) > 4 else None
            if pipeline == "kgenskills":
                kgen_num_chunks = total
                yield sse_event(
                    "step_progress",
                    {
                        "step": "kgenskills",
                        "pipeline": "kgenskills",
                        "progress": chunk_idx,
                        "total": total,
                        "tokens_so_far": 0,
                        "label": f"KGSpin: Chunk {chunk_idx}/{total} ({metric} entities)",
                    },
                )
            elif pipeline == "kgenskills_l_module":
                yield sse_event(
                    "step_progress",
                    {
                        "step": "kgenskills",
                        "pipeline": "kgenskills",
                        "progress": chunk_idx,
                        "total": total,
                        "tokens_so_far": 0,
                        "label": "KGSpin: Semantic matching (full document)...",
                    },
                )
            elif pipeline == "table_extraction":
                # Sprint 39: Stage-specific labels for post-chunk progress
                stage_labels = {
                    "table_extraction": "Structural table extraction",
                    "cross_chunk_stitch": f"Cross-chunk entity stitching ({chunk_idx}/{total})",
                }
                stage_label = stage_labels.get(stage, "Structural table extraction") if stage else "Structural table extraction"
                yield sse_event(
                    "step_progress",
                    {
                        "step": "kgenskills",
                        "pipeline": "kgenskills",
                        "progress": chunk_idx,
                        "total": total,
                        "tokens_so_far": 0,
                        "label": f"KGSpin: {stage_label}...",
                    },
                )
            elif pipeline == "modular":
                yield sse_event(
                    "step_progress",
                    {
                        "step": "modular",
                        "pipeline": "modular",
                        "progress": chunk_idx,
                        "total": total,
                        "tokens_so_far": metric,
                        "label": f"LLM Multi-Stage: Chunk {chunk_idx}/{total} ({metric:,} tokens)",
                    },
                )
            await asyncio.sleep(0)
        except asyncio.TimeoutError:
            # Send a heartbeat comment to keep the connection alive
            yield ": heartbeat\n\n"
            await asyncio.sleep(0)

        if await request.is_disconnected():
            kgs_task.cancel()
            if gemini_task:
                gemini_task.cancel()
            if modular_task:
                modular_task.cancel()
            return

    # Drain remaining progress events
    while not progress_queue.empty():
        progress_queue.get_nowait()

    await asyncio.sleep(0)
    if await request.is_disconnected():
        return

    # Ensure variables exist even if a pipeline wasn't available / errored
    if not gemini_available and not gem_from_log:
        gem_kg = None
        gem_tokens = 0
        gem_errors = 0
        gem_truncated = False
    elif gem_from_log:
        gem_errors = 0
    if not gemini_available and not mod_from_log:
        mod_kg = None
        mod_tokens = 0
        mod_errors = 0
    elif mod_from_log:
        mod_errors = 0

    # Sprint 90: Filter KGs so scores/analysis match graph visualization
    f_kgs_kg = filter_kg_for_display(kgs_kg)
    f_mod_kg = filter_kg_for_display(mod_kg) if mod_kg is not None else None
    f_gem_kg = filter_kg_for_display(gem_kg) if gem_kg is not None else None

    # Emit diagnostic scores (deterministic, instant) — 3-way pairwise
    if f_mod_kg is not None or f_gem_kg is not None:
        scores = compute_diagnostic_scores(f_kgs_kg, mod_kg=f_mod_kg, gem_kg=f_gem_kg, company_name=info["name"])
        yield sse_event("scores_ready", scores)
        await asyncio.sleep(0)

    if gemini_available or gem_from_log:
        # Step 5: Quality analysis
        # Check if we have a cached analysis from the logged run
        cached_analysis = None
        if gem_from_log and gem_logged_run:
            cached_analysis = gem_logged_run.get("analysis")

        if cached_analysis:
            yield sse_event(
                "step_complete",
                {
                    "step": "quality_analysis",
                    "label": "Analysis loaded from log",
                    "duration_ms": 0,
                    "tokens": cached_analysis.get("tokens", 0),
                },
            )
            await asyncio.sleep(0)
            yield sse_event(
                "analysis_ready",
                {
                    "analysis": cached_analysis.get("analysis", cached_analysis),
                    "tokens": cached_analysis.get("tokens", 0),
                },
            )
            await asyncio.sleep(0)
        else:
            yield sse_event(
                "step_start",
                {"step": "quality_analysis", "label": "Analyzing quality comparison..."},
            )
            await asyncio.sleep(0)
            t0 = time.time()

            # Gather stats from cache for enriched prompt
            with _cache_lock:
                _a_cache = dict(_kg_cache.get(ticker, {}))
            # Sprint 90: Use filtered KGs for analysis (matches graph visualization)
            # Stage 0.5.4: thread the ambient alias/legacy-model so quality
            # analysis runs on the same LLM the compare request selected.
            analysis_result = await asyncio.to_thread(
                functools.partial(
                    run_quality_analysis,
                    f_kgs_kg, f_gem_kg or {}, gem_tokens, f_mod_kg, mod_tokens,
                    _a_cache.get("kgs_stats"), _a_cache.get("gem_stats"), _a_cache.get("mod_stats"),
                    llm_alias=llm_alias,
                    legacy_model=None if llm_alias else model,
                )
            )

            analysis_duration = int((time.time() - t0) * 1000)
            yield sse_event(
                "step_complete",
                {
                    "step": "quality_analysis",
                    "label": f"Analysis complete ({analysis_result['tokens']} tokens)",
                    "duration_ms": analysis_duration,
                    "tokens": analysis_result["tokens"],
                },
            )
            await asyncio.sleep(0)

            yield sse_event(
                "analysis_ready",
                {
                    "analysis": analysis_result["analysis"],
                    "tokens": analysis_result["tokens"],
                },
            )
            await asyncio.sleep(0)

            # Store analysis in the most recent run log entry
            try:
                _run_log.update_run_analysis(ticker, _cfg_hash, 0, analysis_result)
            except Exception as e:
                logger.warning(f"Failed to update run analysis: {e}")

    # Done
    total_ms = int((time.time() - pipeline_start) * 1000)
    yield sse_event(
        "done",
        {
            "total_duration_ms": total_ms,
            "kgenskills_tokens": 0,
            "gemini_tokens": gem_tokens,
            "modular_tokens": mod_tokens,
            "cache_version": DEMO_CACHE_VERSION,
            "gemini_errors": gem_errors,
            "modular_errors": mod_errors,
            "corpus_kb": round(actual_kb, 1),
        },
    )


# --- KGSpin Refresh (Sprint 33.17) ---


async def _run_kgen_refresh(
    ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB,
    bundle_name: str | None = None, pipeline_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Re-run KGSpin extraction to prove determinism."""
    yield ": connected\n\n"
    await asyncio.sleep(0)

    with _cache_lock:
        cached = _kg_cache.get(ticker)

    # Log + surface any failure during early bundle resolution,
    # before _run_kgenskills is ever scheduled.
    try:
        bundle = _get_bundle(bundle_name)
    except Exception as e:
        logger.exception(
            "kgspin bundle resolution failed (bundle=%s pipeline=%s)",
            bundle_name, pipeline_id,
        )
        yield sse_event("error", {
            "step": "kgenskills", "pipeline": "kgenskills",
            "message": f"Bundle resolution failed: {e}",
            "recoverable": False,
        })
        yield sse_event("done", {"total_duration_ms": 0})
        return
    if bundle_name and pipeline_id and DOMAIN_BUNDLES_DIR.is_dir() and (DOMAIN_BUNDLES_DIR / bundle_name).is_dir():
        _bid = _split_bundle_id(bundle_name, pipeline_id)
    else:
        _bid = bundle_name or BUNDLE_PATH.name
        if pipeline_id and f"_p={pipeline_id}" not in _bid:
            _bid = f"{_bid}_p={pipeline_id}"
    _kgen_cfg_hash = _kgen_run_log.config_key(
        "kgen", corpus_kb=corpus_kb, bid=_bid,
    )

    # Sprint 110: Initialize sec_doc before if/else to prevent UnboundLocalError
    # when cached text exists but sec_doc was never fetched.
    sec_doc = None

    if not cached or "text" not in cached:
        # Sprint 33.17b: Reconstruct using _parse_and_chunk (not byte-slicing)
        # Sprint 92: Also re-fetch if cache entry exists but is missing "text"
        # (happens when cache was populated from disk cache with KG only).
        info = await asyncio.to_thread(resolve_ticker, ticker)

        # Sprint 05 Task 5: _try_corpus_fetch is the single funnel; all
        # provider failures raise CorpusFetchError which we catch here and
        # transform into a structured SSE error event. No silent-None path.
        try:
            sec_doc = await asyncio.to_thread(_try_corpus_fetch, ticker)
        except CorpusFetchError as cfe:
            logger.exception("Corpus fetch failed for %s", ticker)
            yield sse_event("error", {
                "step": "kgenskills", "pipeline": "kgenskills",
                "doc_id": cfe.doc_id,
                "reason": cfe.reason,
                "message": cfe.actionable_hint,
                "attempted": cfe.attempted,
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": 0})
            return
        _bundle_tmp, _full, demo_text, actual_kb, _ = await asyncio.to_thread(
            _parse_and_chunk, sec_doc.raw_html, ticker, corpus_kb, bundle_name
        )
        new_entry = {"text": demo_text, "raw_html": sec_doc.raw_html, "info": info,
                  "corpus_kb": corpus_kb, "actual_kb": actual_kb, "cfg_hash": "", "chunk_size": DEFAULT_CHUNK_SIZE}
        with _cache_lock:
            if cached:
                # Preserve existing KG data from partial cache
                cached.update(new_entry)
            else:
                _kg_cache[ticker] = new_entry
                cached = new_entry
    else:
        info = cached.get("info", {})
        # Ensure info has a name key (may be missing if cache was partially populated)
        if "name" not in info:
            resolved = await asyncio.to_thread(resolve_ticker, ticker)
            info = resolved
            with _cache_lock:
                if ticker in _kg_cache:
                    _kg_cache[ticker]["info"] = info

    # Sprint 42.6: Refresh always re-runs — that's its purpose.
    # Disk cache is only used by run_comparison() for initial loads.

    demo_text = cached["text"]
    raw_html = cached.get("raw_html")
    actual_kb = cached.get("actual_kb", len(demo_text.encode("utf-8")) / 1024)

    yield sse_event("step_start", {
        "step": "kgenskills", "pipeline": "kgenskills",
        "label": "KGSpin: Re-extracting...",
    })
    await asyncio.sleep(0)

    bundle = _get_bundle(bundle_name)
    t0 = time.time()
    kgen_num_chunks = 0  # captured from progress callbacks for optimal latency

    progress_queue = asyncio.Queue()

    def on_kgs_chunk_done(chunk_idx, total, entities_so_far):
        progress_queue.put_nowait(("kgenskills", chunk_idx, total, entities_so_far))

    def on_l_module_start(num_chunks):
        progress_queue.put_nowait(("kgenskills_l_module", 0, num_chunks, 0))

    def on_table_extraction_start():
        progress_queue.put_nowait(("table_extraction", 0, 1, 0))

    def on_table_extraction_done():
        progress_queue.put_nowait(("table_extraction", 1, 1, 0))

    # Sprint 39: Post-chunk progress (table extraction + cross-chunk stitching)
    def on_post_chunk_progress(stage, idx, total):
        progress_queue.put_nowait(("table_extraction", idx, total, 0, stage))

    # Sprint 102: Build document metadata for seed fact resolution.
    _refresh_doc_metadata = {
        "company_name": info.get("name", ticker),
        "doc_id": ticker,
        "cik": sec_doc.cik or "",
        "accession_number": sec_doc.accession_number or "",
        "filing_date": sec_doc.filing_date or "",
        "fiscal_year_end": sec_doc.fiscal_year_end or "",
    } if sec_doc else None

    kgs_task = asyncio.create_task(
        asyncio.to_thread(
            _run_kgenskills, demo_text, info["name"], ticker, bundle,
            _pipeline_ref_from_pipeline_id(pipeline_id),
            _get_registry_client(),
            on_kgs_chunk_done, raw_html, on_l_module_start,
            on_table_extraction_start, on_table_extraction_done,
            on_post_chunk_progress, _refresh_doc_metadata,
        )
    )

    while not kgs_task.done():
        try:
            item = await asyncio.wait_for(
                progress_queue.get(), timeout=0.5
            )
            pipeline, chunk_idx, total, metric = item[0], item[1], item[2], item[3]
            stage = item[4] if len(item) > 4 else None
            if pipeline == "kgenskills":
                kgen_num_chunks = total
                yield sse_event("step_progress", {
                    "step": "kgenskills", "pipeline": "kgenskills",
                    "progress": chunk_idx, "total": total,
                    "label": f"KGSpin: Chunk {chunk_idx}/{total} ({metric} entities)",
                })
            elif pipeline == "kgenskills_l_module":
                yield sse_event("step_progress", {
                    "step": "kgenskills", "pipeline": "kgenskills",
                    "progress": chunk_idx, "total": total,
                    "label": "KGSpin: Semantic matching (full document)...",
                })
            elif pipeline == "table_extraction":
                # Sprint 39: Stage-specific labels for post-chunk progress
                stage_labels = {
                    "table_extraction": "Structural table extraction",
                    "cross_chunk_stitch": f"Cross-chunk entity stitching ({chunk_idx}/{total})",
                }
                stage_label = stage_labels.get(stage, "Structural table extraction") if stage else "Structural table extraction"
                yield sse_event("step_progress", {
                    "step": "kgenskills", "pipeline": "kgenskills",
                    "progress": chunk_idx, "total": total,
                    "label": f"KGSpin: {stage_label}...",
                })
            await asyncio.sleep(0)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
            await asyncio.sleep(0)
        except Exception:
            # Task raised a non-timeout exception. Break out so the
            # structured handler on task.result() below catches and
            # emits a proper SSE error event — otherwise the exception
            # escapes the async-gen and severs the stream.
            break
        if await request.is_disconnected():
            kgs_task.cancel()
            return

    try:
        kgs_kg = kgs_task.result()
    except Exception as e:
        # INIT-001 Sprint 02: preserve the traceback in the server log so
        # upstream bugs (e.g., kgspin-core TypeError in emergent scoring,
        # or strategy-specific failures on structural/base) can be diagnosed
        # instead of lost behind the SSE error wrapper. Strategy-agnostic
        # label — same catch handles emergent, structural, and base.
        logger.exception(
            "kgspin kgenskills pipeline failed (bundle=%s pipeline=%s)",
            bundle_name, pipeline_id,
        )
        yield sse_event("error", {
            "step": "kgenskills", "pipeline": "kgenskills",
            "message": str(e), "recoverable": False,
        })
        yield sse_event("done", {"total_duration_ms": 0})
        return

    elapsed = time.time() - t0
    kgs_vis = build_vis_data(kgs_kg)
    vis_entities = len(kgs_vis["nodes"])
    vis_rels = len(kgs_vis["edges"])
    kgs_throughput = actual_kb / elapsed if elapsed > 0 else 0
    kgs_cpu_cost = (elapsed / 3600) * _CPU_COST_PER_HOUR
    # Fallback: estimate num_chunks if progress events weren't polled in time
    if kgen_num_chunks == 0 and actual_kb > 0:
        _cs = cached.get("chunk_size", DEFAULT_CHUNK_SIZE)
        kgen_num_chunks = max(1, round(actual_kb * 1024 / _cs))

    # Sprint 33.17c: Emit step_complete to stop the timeline spinner
    yield sse_event("step_complete", {
        "step": "kgenskills", "pipeline": "kgenskills",
        "label": f"KGSpin: {vis_entities} entities, {vis_rels} relationships",
        "duration_ms": int(elapsed * 1000),
        "tokens": 0,
    })
    await asyncio.sleep(0)

    # Log to disk cache
    if bundle_name and pipeline_id and DOMAIN_BUNDLES_DIR.is_dir() and (DOMAIN_BUNDLES_DIR / bundle_name).is_dir():
        _log_bid = _split_bundle_id(bundle_name, pipeline_id)
    else:
        _log_bid = bundle_name or BUNDLE_PATH.name
        if pipeline_id and f"_p={pipeline_id}" not in _log_bid:
            _log_bid = f"{_log_bid}_p={pipeline_id}"
    _kgen_cfg_hash = _kgen_run_log.config_key(
        "kgen", corpus_kb=cached.get('corpus_kb', corpus_kb), bid=_log_bid,
    )
    _cache_save("kgen", ticker, _kgen_cfg_hash, kgs_kg,
                 tokens=0, elapsed=elapsed,
                 model_fallback="kgen_deterministic",
                 bundle_version=_log_bid,
                 actual_kb=actual_kb)

    # Update in-memory cache so Q&A and analysis can access this KG
    with _cache_lock:
        if ticker not in _kg_cache:
            _kg_cache[ticker] = {}
        _kg_cache[ticker]["kgs_kg"] = kgs_kg

    total_runs = _kgen_run_log.count(ticker, _kgen_cfg_hash)

    yield sse_event("kg_ready", {
        "step": "kgenskills", "pipeline": "kgenskills",
        "bundle_version": _log_bid,
        "vis": kgs_vis,
        "stats": {
            "entities": vis_entities,
            "relationships": vis_rels,
            "tokens": 0,
            "duration_ms": int(elapsed * 1000),
            "throughput_kb_sec": round(kgs_throughput, 1),
            "cpu_cost": round(kgs_cpu_cost, 6),
            "num_chunks": kgen_num_chunks,
            "actual_kb": round(actual_kb, 1),
        },
        "total_runs": total_runs,
    })
    yield sse_event("done", {"total_duration_ms": int(elapsed * 1000)})


# --- Per-Column Refresh (Sprint 33.4) ---


async def run_single_refresh(
    ticker: str, request: Request, pipeline: str, corpus_kb: int = DEFAULT_CORPUS_KB,
    chunk_size: int = DEFAULT_CHUNK_SIZE, model: str = DEFAULT_GEMINI_MODEL,
    bundle_name: str | None = None,
    llm_alias: str | None = None,
) -> AsyncGenerator[str, None]:
    """Re-run a single LLM pipeline without restarting KGSpin or the other LLM.

    Uses cached text from _kg_cache (populated by run_comparison).
    Sprint 118: Accepts optional bundle_name to resolve domain-specific paths
    for LLM prompts (entity types, relationship patterns).
    """
    yield ": connected\n\n"
    await asyncio.sleep(0)

    with _cache_lock:
        cached = _kg_cache.get(ticker)

    if not cached or "text" not in cached:
        # Sprint 33.17b: Reconstruct using _parse_and_chunk (not byte-slicing)
        # Sprint 92: Also re-fetch if cache entry is missing "text" (partial cache from disk load).
        info = await asyncio.to_thread(resolve_ticker, ticker)

        # Sprint 05 Task 5: single-funnel corpus fetch with CorpusFetchError.
        try:
            sec_doc = await asyncio.to_thread(_try_corpus_fetch, ticker)
        except CorpusFetchError as cfe:
            logger.exception("Corpus fetch failed for %s", ticker)
            yield sse_event("error", {
                "step": pipeline, "pipeline": pipeline,
                "doc_id": cfe.doc_id,
                "reason": cfe.reason,
                "message": cfe.actionable_hint,
                "attempted": cfe.attempted,
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": 0})
            return

        _bundle_tmp, _full, demo_text, actual_kb, _ = await asyncio.to_thread(
            _parse_and_chunk, sec_doc.raw_html, ticker, corpus_kb
        )
        new_entry = {
            "text": demo_text, "raw_html": sec_doc.raw_html, "info": info,
            "corpus_kb": corpus_kb, "actual_kb": actual_kb, "cfg_hash": "", "chunk_size": chunk_size,
        }
        # Warm the cache so subsequent refreshes are instant
        with _cache_lock:
            if cached:
                cached.update(new_entry)
            else:
                _kg_cache[ticker] = new_entry
                cached = new_entry

    demo_text = cached["text"]
    info = cached.get("info", {})
    # Ensure info has a name key (may be missing if cache was partially populated)
    if "name" not in info:
        resolved = await asyncio.to_thread(resolve_ticker, ticker)
        info = resolved
        with _cache_lock:
            if ticker in _kg_cache:
                _kg_cache[ticker]["info"] = info
    actual_kb = cached.get("actual_kb", len(demo_text.encode("utf-8")) / 1024)
    _cached_corpus_kb = cached.get("corpus_kb", corpus_kb)

    # Compute cache keys using shared helpers
    _cached_chunk_size = cached.get("chunk_size", DEFAULT_CHUNK_SIZE)
    _refresh_chunk_size = chunk_size if chunk_size != DEFAULT_CHUNK_SIZE else _cached_chunk_size
    _bundle = _get_bundle()

    # Sprint 118: Resolve domain-specific bundle/patterns paths for LLM prompts
    _llm_bundle_path = BUNDLE_PATH
    _llm_patterns_path = PATTERNS_PATH
    _bid = bundle_name or BUNDLE_PATH.name
    if bundle_name:
        try:
            _llm_bundle_path = resolve_domain_bundle_path(bundle_name)
            _llm_patterns_path = resolve_domain_yaml_path(bundle_name)
        except Exception as e:
            logger.warning(f"Could not resolve domain bundle '{bundle_name}': {e}")

    _refresh_cfg_keys = _build_pipeline_cache_keys(
        _llm_bundle_path, _llm_patterns_path, _cached_corpus_kb, model,
        bundle_name=_bid,
    )
    _cfg_hash = _refresh_cfg_keys[pipeline]

    # Defect 1 (2026-04-24, restored 2026-04-27): the call sites at
    # ``document_metadata=_refresh_doc_metadata`` below were added without
    # the variable definition, so each refresh raised NameError. Build it
    # here from the cached info — the refresh path doesn't re-fetch
    # ``sec_doc``, so cik/accession/filing_date/fiscal_year_end are blank
    # on cache-hit. Only ``company_name`` + ``doc_id`` are required for
    # the H-module resolver override that this metadata exists to drive.
    _refresh_doc_metadata = {
        "company_name": info.get("name", ticker),
        "doc_id": ticker,
        "cik": "",
        "accession_number": "",
        "filing_date": "",
        "fiscal_year_end": "",
    }

    if pipeline == "gemini":
        yield sse_event("step_start", {
            "step": "gemini", "pipeline": "gemini",
            "label": "LLM Full Shot: Re-extracting...",
        })
        await asyncio.sleep(0)
        t0 = time.time()

        # Sprint 118: Pass domain-specific bundle/patterns paths to LLM extractor
        gemini_task = asyncio.create_task(
            asyncio.to_thread(
                _run_agentic_flash, demo_text, info["name"], f"{ticker}_10K",
                None if llm_alias else model,
                bundle_path=_llm_bundle_path, patterns_path=_llm_patterns_path,
                llm_alias=llm_alias,
                document_metadata=_refresh_doc_metadata,
            )
        )

        while not gemini_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(gemini_task), timeout=1.0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0)
            except Exception:
                # The task completed with an exception. If we don't catch it
                # here, the bare exception escapes the async generator,
                # starlette severs the SSE stream mid-response
                # (ERR_INCOMPLETE_CHUNKED_ENCODING in the browser), and our
                # structured failure handling below — which CACHES the
                # failure and YIELDS a proper SSE error event so the UI can
                # render the red "Failed to generate" overlay — never runs.
                # Swallow here and fall through to ``gemini_task.result()``
                # below, which re-raises inside an explicit try/except that
                # handles the failure end-to-end.
                break
            if await request.is_disconnected():
                gemini_task.cancel()
                return

        try:
            gem_kg, gem_tokens, gem_elapsed, gem_errors, gem_truncated = gemini_task.result()
        except Exception as e:
            # Flash is designed to be a failure-mode demonstration when the
            # document exceeds the model's context window (that's the whole
            # point of the "just throw it at the LLM" baseline). Cache the
            # failure with a structured marker so the replay path + analysis
            # agent know the run didn't silently skip — it actively failed.
            logger.exception("LLM Full Shot refresh failed")
            _failure_reason, _error_type = _classify_llm_error(e)
            _gem_elapsed = time.time() - t0
            failure_kg = {
                "entities": [],
                "relationships": [],
                "derived_facts": [],
                "status": "failed",
                "error": {
                    "type": _error_type,
                    "message": str(e),
                    "exception_class": type(e).__name__,
                    "reason": _failure_reason,
                },
                "provenance": {
                    "model": model,
                    "corpus_kb": round(actual_kb, 1),
                    "tokens_used": 0,
                    "llm_calls": 0,
                    "status": "failed",
                },
            }
            try:
                _run_log.log_run(
                    ticker, _cfg_hash, failure_kg,
                    0, _gem_elapsed, model,
                    cache_version=DEMO_CACHE_VERSION,
                )
            except Exception as log_exc:
                logger.warning(f"Failed to cache Gemini failure: {log_exc}")
            yield sse_event("error", {
                "step": "gemini", "pipeline": "gemini",
                "message": f"LLM Full Shot failed: {e}",
                "reason": _failure_reason,
                "error_type": _error_type,
                "exception_class": type(e).__name__,
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": int((time.time() - t0) * 1000)})
            return

        # Log this live run (Sprint 33.5: skip empty/truncated results)
        gem_kg.setdefault("provenance", {})["corpus_kb"] = round(actual_kb, 1)
        gem_entities_count = len(gem_kg.get("entities", []))
        if (gem_entities_count > 0 or gem_errors == 0) and not gem_truncated:
            try:
                _gem_model = gem_kg.get("provenance", {}).get("model", "gemini")
                _run_log.log_run(
                    ticker, _cfg_hash, gem_kg,
                    gem_tokens, gem_elapsed, _gem_model,
                    cache_version=DEMO_CACHE_VERSION,
                )
            except Exception as e:
                logger.warning(f"Failed to log Gemini refresh run: {e}")
        else:
            logger.warning(f"Skipping Gemini refresh cache: {gem_entities_count} entities, {gem_errors} errors, truncated={gem_truncated}")
        run_count = _run_log.count(ticker, _cfg_hash)

        # Update in-memory cache so Q&A and analysis can access this KG
        with _cache_lock:
            if ticker not in _kg_cache:
                _kg_cache[ticker] = {}
            _kg_cache[ticker]["gem_kg"] = gem_kg
            _kg_cache[ticker]["gem_tokens"] = gem_tokens

        gem_throughput = actual_kb / gem_elapsed if gem_elapsed > 0 else 0
        gem_vis = build_vis_data(gem_kg)
        gem_entities = len(gem_vis["nodes"])
        gem_rels = len(gem_vis["edges"])
        gem_duration = int((time.time() - t0) * 1000)

        yield sse_event("step_complete", {
            "step": "gemini", "pipeline": "gemini",
            "label": f"LLM Full Shot: {gem_entities} entities, {gem_rels} relationships",
            "duration_ms": gem_duration, "tokens": gem_tokens,
        })
        await asyncio.sleep(0)
        yield sse_event("kg_ready", {
            "pipeline": "gemini",
            "stats": {
                "entities": gem_entities, "relationships": gem_rels,
                "tokens": gem_tokens, "duration_ms": gem_duration,
                "throughput_kb_sec": round(gem_throughput, 1),
                "actual_kb": round(actual_kb, 1),
            },
            "vis": gem_vis,
            "from_log": False, "run_index": 0, "total_runs": run_count,
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
            "cache_version": DEMO_CACHE_VERSION,
            "errors": gem_errors, "truncated": gem_truncated,
            "model": model,
            "model_pricing": GEMINI_MODEL_PRICING[model],
        })
        await asyncio.sleep(0)

        yield sse_event("done", {
            "total_duration_ms": gem_duration,
            "gemini_tokens": gem_tokens,
            "gemini_errors": gem_errors,
        })

    elif pipeline == "modular":
        progress_queue = asyncio.Queue()

        def on_mod_chunk_done(chunk_idx, total, tokens_so_far):
            progress_queue.put_nowait(("modular", chunk_idx, total, tokens_so_far))

        yield sse_event("step_start", {
            "step": "modular", "pipeline": "modular",
            "label": "LLM Multi-Stage: Re-extracting...",
        })
        await asyncio.sleep(0)
        t0 = time.time()

        # Sprint 33.6: Create cancel event for refresh
        _cancel_event = threading.Event()
        _modular_cancel_events[ticker] = _cancel_event
        # Sprint 33.10: Use chunk_size from param, fallback to cached value
        _chunk_size = chunk_size if chunk_size != DEFAULT_CHUNK_SIZE else cached.get("chunk_size", DEFAULT_CHUNK_SIZE)
        modular_task = asyncio.create_task(
            asyncio.to_thread(
                _run_agentic_analyst, demo_text, info["name"], f"{ticker}_10K", on_mod_chunk_done,
                _cancel_event, _chunk_size, None if llm_alias else model,
                bundle_path=_llm_bundle_path, patterns_path=_llm_patterns_path,
                llm_alias=llm_alias,
                document_metadata=_refresh_doc_metadata,
            )
        )

        while not modular_task.done():
            try:
                _, chunk_idx, total, metric = await asyncio.wait_for(
                    progress_queue.get(), timeout=0.5
                )
                yield sse_event("step_progress", {
                    "step": "modular", "pipeline": "modular",
                    "progress": chunk_idx, "total": total,
                    "tokens_so_far": metric,
                    "label": f"LLM Multi-Stage: Chunk {chunk_idx}/{total} ({metric:,} tokens)",
                })
                await asyncio.sleep(0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0)
            except Exception:
                break
            if await request.is_disconnected():
                modular_task.cancel()
                return

        # Sprint 33.6: Cleanup cancel event
        _modular_cancel_events.pop(ticker, None)
        try:
            mod_kg, h_tokens, l_tokens, mod_elapsed, mod_errors = modular_task.result()
        except Exception as e:
            # Analyst mirror of the Flash failure-caching pattern above:
            # persist a structured failure so the replay path + analysis
            # agent see an explicit "failed" state instead of a silent
            # skip. Same classification helper, same SSE shape.
            logger.exception("LLM Multi-Stage refresh failed")
            _analyst_reason, _analyst_error_type = _classify_llm_error(e)
            _mod_elapsed = time.time() - t0
            failure_kg = {
                "entities": [],
                "relationships": [],
                "derived_facts": [],
                "status": "failed",
                "error": {
                    "type": _analyst_error_type,
                    "message": str(e),
                    "exception_class": type(e).__name__,
                    "reason": _analyst_reason,
                },
                "provenance": {
                    "model": model,
                    "corpus_kb": round(actual_kb, 1),
                    "tokens_used": 0,
                    "llm_calls": 0,
                    "status": "failed",
                },
            }
            try:
                _modular_run_log.log_run(
                    ticker, _cfg_hash, failure_kg,
                    0, _mod_elapsed, model,
                    cache_version=DEMO_CACHE_VERSION,
                )
            except Exception as log_exc:
                logger.warning(f"Failed to cache Analyst failure: {log_exc}")
            yield sse_event("error", {
                "step": "modular", "pipeline": "modular",
                "message": f"LLM Multi-Stage failed: {e}",
                "reason": _analyst_reason,
                "error_type": _analyst_error_type,
                "exception_class": type(e).__name__,
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": int((time.time() - t0) * 1000)})
            return
        mod_tokens = h_tokens + l_tokens

        # Log this live run (Sprint 33.5: skip empty error results)
        mod_kg.setdefault("provenance", {})["corpus_kb"] = round(actual_kb, 1)
        mod_entities_count = len(mod_kg.get("entities", []))
        if mod_entities_count > 0 or mod_errors == 0:
            try:
                _mod_model = mod_kg.get("provenance", {}).get("model", "gemini")
                _modular_run_log.log_run(
                    ticker, _cfg_hash, mod_kg,
                    mod_tokens, mod_elapsed, _mod_model,
                    cache_version=DEMO_CACHE_VERSION,
                )
            except Exception as e:
                logger.warning(f"Failed to log modular refresh run: {e}")
        else:
            logger.warning(f"Skipping modular refresh cache: 0 entities with {mod_errors} errors")
        mod_run_count = _modular_run_log.count(ticker, _cfg_hash)

        # Update in-memory cache so Q&A and analysis can access this KG
        with _cache_lock:
            if ticker not in _kg_cache:
                _kg_cache[ticker] = {}
            _kg_cache[ticker]["mod_kg"] = mod_kg
            _kg_cache[ticker]["mod_tokens"] = mod_tokens

        mod_throughput = actual_kb / mod_elapsed if mod_elapsed > 0 else 0
        mod_vis = build_vis_data(mod_kg)
        mod_entities = len(mod_vis["nodes"])
        mod_rels = len(mod_vis["edges"])
        mod_duration = int((time.time() - t0) * 1000)

        # Sprint 33.6: Partial result provenance
        _mod_prov = mod_kg.get("provenance", {})

        yield sse_event("step_complete", {
            "step": "modular", "pipeline": "modular",
            "label": f"LLM Multi-Stage: {mod_entities} entities, {mod_rels} relationships",
            "duration_ms": mod_duration, "tokens": mod_tokens,
        })
        await asyncio.sleep(0)
        yield sse_event("kg_ready", {
            "pipeline": "modular",
            "stats": {
                "entities": mod_entities, "relationships": mod_rels,
                "tokens": mod_tokens, "h_tokens": h_tokens, "l_tokens": l_tokens,
                "duration_ms": mod_duration,
                "throughput_kb_sec": round(mod_throughput, 1),
                "chunks_completed": _mod_prov.get("chunks_completed", 0),
                "chunks_total": _mod_prov.get("chunks_total", 0),
                "actual_kb": round(actual_kb, 1),
            },
            "vis": mod_vis,
            "from_log": False, "run_index": 0, "total_runs": mod_run_count,
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
            "cache_version": DEMO_CACHE_VERSION,
            "errors": mod_errors,
            "model": model,
            "model_pricing": GEMINI_MODEL_PRICING[model],
        })
        await asyncio.sleep(0)

        yield sse_event("done", {
            "total_duration_ms": mod_duration,
            "modular_tokens": mod_tokens,
            "modular_errors": mod_errors,
        })


# --- Pipeline Runners (synchronous, called from threads) ---


def _parse_and_chunk(html_content: str, ticker: str, corpus_kb: int = DEFAULT_CORPUS_KB,
                     bundle_name: str | None = None):
    """Parse HTML to text, truncate to corpus_kb at paragraph boundary, then chunk.

    Sprint 33.3: Byte-based corpus sizing. All pipelines receive the same
    truncated text. KGSpin and Multi-Stage share the same chunk boundaries
    (VP Parity Guard). Full Shot gets the raw truncated text.

    Returns:
        (bundle, full_text, truncated_text, actual_kb, all_chunks)
    """
    from kgspin_core.execution.extractor import DocumentChunker

    from kgspin_core.execution.preprocessors import resolve_preprocessors

    bundle = _get_bundle(bundle_name)
    # Sprint 39: Run bundle's pre-phase preprocessors (ixbrl_strip + xbrl_taxonomy_strip)
    # instead of hardcoded strip_ixbrl() — domain-scoped cleaning per ADR-010
    pre_procs = resolve_preprocessors(
        getattr(bundle, "preprocessors", []), phase="pre",
    )
    cleaned_html = html_content
    for proc in pre_procs:
        cleaned_html = proc.process(cleaned_html, Path(f"{ticker}_10K.html"), {})
    full_text = html_to_text(cleaned_html)
    # corpus_kb=0 means "full document" — no truncation
    if corpus_kb == 0:
        truncated_text = full_text
        actual_kb = len(full_text.encode("utf-8")) / 1024
        chunker = DocumentChunker(max_chunk_size=bundle.max_chunk_size)
        all_chunks = chunker.chunk(truncated_text, doc_id=f"{ticker}_10K")
        return bundle, full_text, truncated_text, actual_kb, all_chunks

    max_bytes = corpus_kb * 1024
    text_bytes = full_text.encode("utf-8")

    if len(text_bytes) <= max_bytes:
        truncated_text = full_text
    else:
        candidate = text_bytes[:max_bytes].decode("utf-8", errors="ignore")
        # Walk back to nearest paragraph break, then line break, then space
        para = candidate.rfind("\n\n")
        if para > max_bytes * 0.90:
            truncated_text = candidate[:para].rstrip()
        else:
            line = candidate.rfind("\n")
            if line > max_bytes * 0.90:
                truncated_text = candidate[:line].rstrip()
            else:
                sp = candidate.rfind(" ")
                truncated_text = candidate[:sp].rstrip() if sp > 0 else candidate

    actual_kb = len(truncated_text.encode("utf-8")) / 1024

    # VP Parity Guard: chunk the truncated text — shared by KGSpin + Multi-Stage
    chunker = DocumentChunker(max_chunk_size=bundle.max_chunk_size)
    all_chunks = chunker.chunk(truncated_text, doc_id=f"{ticker}_10K")

    return bundle, full_text, truncated_text, actual_kb, all_chunks


async def _run_clinical_comparison(
    nct_id: str, request: Request, bundle_name: str = "clinical-v2",
    model: str = DEFAULT_GEMINI_MODEL, chunk_size: int = DEFAULT_CHUNK_SIZE,
    force_refresh: set | None = None,
    corpus_source: str = "live",
    llm_alias: str | None = None,
) -> AsyncGenerator[str, None]:
    """Sprint 06: Clinical comparison pipeline with explicit corpus source.

    ``corpus_source`` is either ``"live"`` (canonical clinical_trials provider →
    ClinicalTrials.gov) or ``"gold"`` (legacy gold-fixture reader). No
    silent fallback. The chosen source is surfaced in the SSE
    ``kg_ready`` payload as ``corpus_source`` for the frontend badge.
    """
    pipeline_start = time.time()
    yield ": connected\n\n"
    await asyncio.sleep(0)

    # Sprint 06: legacy paths used these — keep as None defaults so
    # downstream gold-only code paths still compile. Only set them in
    # the gold branch below. The clinical-mvp-v1 bundle is self-contained
    # and doesn't need a separate patterns file or ClinicalCorpusPlugin context.
    _clinical_patterns: Path | None = None
    _clinical_doc_ctx = None
    gold_data: dict | None = None

    # Step 1: Load clinical trial data from the chosen source
    source_label = "live (ClinicalTrials.gov)" if corpus_source == "live" else "gold fixture"
    yield sse_event("step_start", {
        "step": "resolve_ticker",
        "label": f"Loading clinical trial {nct_id} from {source_label}...",
        "corpus_source": corpus_source,
    })
    await asyncio.sleep(0)
    t0 = time.time()

    trial_title = nct_id
    corpus_text = ""
    nct_metadata = {}

    if corpus_source == "live":
        # Sprint 06: route through the canonical _try_corpus_fetch funnel
        # which goes to the kgspin-plugin-clinical clinical_trials provider.
        try:
            doc_adapter = await asyncio.to_thread(_try_corpus_fetch, nct_id)
            corpus_text = doc_adapter.raw_html or ""
            trial_title = doc_adapter.company_name or nct_id
            # The clinical_trials provider stuffs sponsor / phase / status
            # into provider_specific via the original CorpusMetadata.
            # Our _corpus_doc_to_sec_shape adapter doesn't preserve those —
            # re-fetch the raw CorpusDocument to get the metadata for the
            # slot header (VP Prod requirement).
            try:
                provider = _corpus_provider_cache.get("clinical_trials")
                if provider is not None:
                    raw_doc = provider.fetch(nct_id)
                    nct_metadata = {
                        "sponsor": (raw_doc.metadata.provider_specific or {}).get("sponsor", ""),
                        "phase": ", ".join((raw_doc.metadata.provider_specific or {}).get("phase", []) or []),
                        "overall_status": (raw_doc.metadata.provider_specific or {}).get("overallStatus", ""),
                    }
            except Exception:
                logger.warning("Could not extract NCT metadata for %s", nct_id)
        except CorpusFetchError as cfe:
            logger.exception("Live clinical fetch failed for %s", nct_id)
            yield sse_event("error", {
                "step": "resolve_ticker",
                "doc_id": cfe.doc_id,
                "reason": cfe.reason,
                "message": cfe.actionable_hint,
                "attempted": cfe.attempted,
                "corpus_source": "live",
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": int((time.time() - pipeline_start) * 1000)})
            return
    else:
        # gold path — explicit, no fallback
        gold_dir = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "gold" / "clinical"
        gold_path = gold_dir / f"{nct_id}.json"
        if not gold_path.exists():
            yield sse_event("error", {
                "step": "resolve_ticker",
                "message": f"No gold fixture for {nct_id} at {gold_path}. "
                           f"Use ?source=live to fetch from ClinicalTrials.gov instead.",
                "reason": "gold_fixture_missing",
                "corpus_source": "gold",
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": int((time.time() - pipeline_start) * 1000)})
            return
        import json as _json
        gold_data = _json.loads(gold_path.read_text())
        trial_title = gold_data.get("trial_title", nct_id)
        # Build text from gold data publications
        sections = []
        for doc in gold_data.get("input_documents", []):
            title = doc.get("title", "Untitled")
            text = doc.get("text", doc.get("abstract", ""))
            if text:
                sections.append(f"--- Publication: {title} ---\n{text}")
        corpus_text = "\n\n".join(sections)
        # Legacy gold path needs the clinical patterns YAML + ClinicalCorpusPlugin
        # document context for cache backfill. Tolerate import failure
        # gracefully — the kgenskills package may be gone post-split.
        _clinical_patterns = resolve_domain_yaml_path("clinical")
        try:
            from kgenskills.domains.clinical.plugin import ClinicalCorpusPlugin as _CCP
            _clinical_doc_ctx = _CCP.build_document_context(gold_data, nct_id)
        except ImportError:
            logger.warning("kgenskills.domains.clinical removed in core split — gold path runs without document_context backfill")

    duration = int((time.time() - t0) * 1000)

    yield sse_event("step_complete", {
        "step": "resolve_ticker",
        "label": f"Resolved: {nct_id} \u2192 {trial_title[:60]}",
        "duration_ms": duration,
        "details": {"name": trial_title, "entity_id": -999999999},
    })
    await asyncio.sleep(0)

    # Step 2: corpus already loaded above (live or gold). Just emit
    # the fetch_sec step_complete for UI continuity.
    if not corpus_text:
        yield sse_event("error", {
            "step": "fetch_sec",
            "message": f"Empty corpus for {nct_id} (source={corpus_source})",
            "corpus_source": corpus_source,
            "recoverable": False,
        })
        yield sse_event("done", {"total_duration_ms": int((time.time() - pipeline_start) * 1000)})
        return

    size_kb = len(corpus_text.encode("utf-8")) / 1024
    yield sse_event("step_complete", {
        "step": "fetch_sec",
        "label": f"Corpus: {size_kb:.0f}KB from {source_label}",
        "duration_ms": 0,
        "corpus_source": corpus_source,
        "details": {
            "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
            "filing_date": "",
            "accession_number": nct_id,
            "company_name": trial_title,
            "doc_id": nct_id,
            "size_kb": round(size_kb, 1),
            "sponsor": nct_metadata.get("sponsor", ""),
            "phase": nct_metadata.get("phase", ""),
            "overall_status": nct_metadata.get("overall_status", ""),
        },
    })
    await asyncio.sleep(0)

    # Step 3: Parse and chunk
    yield sse_event("step_start", {"step": "parse_text", "label": "Chunking corpus..."})
    await asyncio.sleep(0)
    t0 = time.time()

    bundle = _get_bundle(bundle_name)
    from kgspin_core.execution.extractor import DocumentChunker
    chunker = DocumentChunker(max_chunk_size=bundle.max_chunk_size)
    all_chunks = chunker.chunk(corpus_text, doc_id=f"{nct_id}_clinical")

    duration = int((time.time() - t0) * 1000)
    yield sse_event("step_complete", {
        "step": "parse_text",
        "label": f"Corpus: {size_kb:.0f}KB in {len(all_chunks)} chunks",
        "duration_ms": duration,
        "details": {
            "num_chunks": len(all_chunks),
            "total_chars": len(corpus_text),
            "corpus_kb": round(size_kb, 1),
            "requested_kb": round(size_kb, 1),
        },
    })
    await asyncio.sleep(0)

    if await request.is_disconnected():
        return

    actual_kb = size_kb
    corpus_kb = round(size_kb, 1)

    # Resolve clinical bundle path for cache keys and LLM extraction
    clinical_bundle_path = resolve_bundle_path(bundle_name)

    # Build cache keys using shared helpers (same key format as financial → old caches accessible)
    _cfg_keys = _build_pipeline_cache_keys(
        clinical_bundle_path, _clinical_patterns, corpus_kb, model,
        bundle_name=bundle_name,
    )

    # Check for cached runs using shared helpers
    _fr = force_refresh or set()
    gemini_available = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY"))
    kgen_from_log, kgen_logged_run = _cache_lookup("kgen", nct_id, _cfg_keys["kgen"], force_refresh="kgen" in _fr)
    gem_from_log, gem_logged_run = (
        _cache_lookup("gemini", nct_id, _cfg_keys["gemini"], force_refresh="gemini" in _fr)
        if gemini_available else (False, None)
    )
    mod_from_log, mod_logged_run = (
        _cache_lookup("modular", nct_id, _cfg_keys["modular"], force_refresh="modular" in _fr)
        if gemini_available else (False, None)
    )

    # Step 4: Run KGSpin extraction (or serve from cache)
    kgs_t0 = time.time()
    kgen_task = None

    # Defect 1 (2026-04-24): clinical doc_metadata for self-reference
    # resolution. Trial name doubles as the main_entity override.
    _clinical_doc_metadata = {
        "company_name": trial_title,
        "doc_id": nct_id,
    }

    if kgen_from_log:
        yield sse_event("step_complete", _cached_step_event("kgenskills", "kgenskills", "kgen", nct_id, _cfg_keys["kgen"]))
        await asyncio.sleep(0)
        # Sprint 79: Backfill document_context for older cached clinical KGs
        if _clinical_doc_ctx and not kgen_logged_run["kg"].get("document_context"):
            kgen_logged_run["kg"]["document_context"] = _clinical_doc_ctx
        _kgen_kg_event = _cached_kg_event(
            "kgenskills", kgen_logged_run, actual_kb, "kgen", nct_id, _cfg_keys["kgen"],
            extra_stats={"chunks_completed": len(all_chunks), "chunks_total": len(all_chunks)},
        )
        yield sse_event("kg_ready", _kgen_kg_event)
        await asyncio.sleep(0)
    else:
        yield sse_event("step_start", {"step": "kgenskills", "label": "Running KGSpin clinical extraction..."})
        await asyncio.sleep(0)

        progress_queue = asyncio.Queue()

        def kgen_chunk_cb(idx, total, _entities=None):
            try:
                progress_queue.put_nowait(("kgenskills", idx, total, 0))
            except Exception:
                pass

        def _do_kgen():
            return _run_kgenskills(
                text=corpus_text,
                company_name="",
                ticker=nct_id,
                bundle=bundle,
                pipeline_config_ref=_pipeline_ref_from_strategy("fan_out"),
                registry_client=_get_registry_client(),
                on_chunk_complete=kgen_chunk_cb,
            )

        kgen_task = asyncio.get_event_loop().run_in_executor(None, _do_kgen)

    # Step 5: Start LLM Full Shot in parallel (using clinical bundle + patterns)
    gemini_task = None

    if gem_from_log:
        yield sse_event("step_complete", _cached_step_event(
            "gemini", "gemini", "gemini", nct_id, _cfg_keys["gemini"],
            tokens=gem_logged_run.get("total_tokens", 0),
        ))
        await asyncio.sleep(0)
    elif gemini_available:
        yield sse_event("step_start", {
            "step": "gemini", "pipeline": "gemini",
            "label": "LLM Full Shot: Extracting (clinical)...",
        })
        await asyncio.sleep(0)
        gemini_task = asyncio.create_task(
            asyncio.to_thread(
                _run_clinical_gemini_full_shot,
                corpus_text, trial_title, f"{nct_id}_clinical",
                None if llm_alias else model, clinical_bundle_path, _clinical_patterns,
                llm_alias=llm_alias,
                document_metadata=_clinical_doc_metadata,
            )
        )
    else:
        yield sse_event("error", {
            "step": "gemini", "pipeline": "gemini",
            "message": "GEMINI_API_KEY not set. Set the environment variable to run LLM comparison.",
            "recoverable": False,
        })
        await asyncio.sleep(0)

    # Step 6: Start LLM Multi-Stage in parallel (using clinical bundle + patterns)
    modular_task = None
    if mod_from_log:
        yield sse_event("step_complete", _cached_step_event(
            "modular", "modular", "modular", nct_id, _cfg_keys["modular"],
            tokens=mod_logged_run.get("total_tokens", 0),
        ))
        await asyncio.sleep(0)
    elif gemini_available:
        yield sse_event("step_start", {
            "step": "modular", "pipeline": "modular",
            "label": "LLM Multi-Stage: Extracting (clinical)...",
        })
        await asyncio.sleep(0)
        modular_task = asyncio.create_task(
            asyncio.to_thread(
                _run_clinical_modular,
                corpus_text, trial_title, f"{nct_id}_clinical",
                chunk_size, None if llm_alias else model,
                clinical_bundle_path, _clinical_patterns,
                llm_alias=llm_alias,
                document_metadata=_clinical_doc_metadata,
            )
        )

    # Drain KGSpin progress events and emit result (only if not served from cache)
    if kgen_task:
        kgen_done = False
        while not kgen_done:
            try:
                pipeline, idx, total, _ = await asyncio.wait_for(progress_queue.get(), timeout=0.3)
                yield sse_event("chunk_progress", {
                    "pipeline": pipeline,
                    "chunk_index": idx,
                    "total_chunks": total,
                })
                await asyncio.sleep(0)
            except asyncio.TimeoutError:
                if kgen_task.done():
                    kgen_done = True
            except Exception:
                # Non-timeout task exception — break so the structured
                # handler below surfaces a proper SSE error event.
                kgen_done = True

        try:
            kgen_result = await kgen_task
        except Exception as e:
            logger.exception("Clinical KGSpin pipeline failed")
            yield sse_event("error", {
                "step": "kgenskills", "pipeline": "kgenskills",
                "message": f"KGSpin extraction failed: {e}",
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": int((time.time() - kgs_t0) * 1000)})
            return
        kgs_elapsed = (time.time() - kgs_t0)
        # Sprint 79: Inject clinical document_context
        if _clinical_doc_ctx and "document_context" not in kgen_result:
            kgen_result["document_context"] = _clinical_doc_ctx
        kgen_vis = build_vis_data(kgen_result)
        kgen_entities = len(kgen_vis["nodes"])
        kgen_rels = len(kgen_vis["edges"])

        yield sse_event("step_complete", {
            "step": "kgenskills",
            "label": f"KGSpin: {kgen_entities} entities, {kgen_rels} relationships",
            "duration_ms": int(kgs_elapsed * 1000),
        })
        await asyncio.sleep(0)

        yield sse_event("kg_ready", _fresh_kg_event(
            "kgenskills", kgen_result, 0, kgs_elapsed, actual_kb,
            "kgen", nct_id, _cfg_keys["kgen"],
            extra_stats={"chunks_completed": len(all_chunks), "chunks_total": len(all_chunks)},
        ))
        await asyncio.sleep(0)

        _cache_save("kgen", nct_id, _cfg_keys["kgen"], kgen_result,
                     tokens=0, elapsed=kgs_elapsed,
                     model_fallback="kgen_deterministic",
                     bundle_version=_bundle_id(bundle_name),
                     document_context=_clinical_doc_ctx,
                     actual_kb=actual_kb)

    # Wait for LLM Full Shot (or emit cached result)
    if gem_from_log:
        yield sse_event("kg_ready", _cached_kg_event(
            "gemini", gem_logged_run, actual_kb, "gemini", nct_id, _cfg_keys["gemini"],
            model=model,
        ))
        await asyncio.sleep(0)
    elif gemini_task:
        try:
            gem_kg, gem_tokens, gem_elapsed, gem_errors, gem_truncated = await gemini_task
            gem_vis = build_vis_data(gem_kg)
            gem_entities = len(gem_vis["nodes"])

            yield sse_event("step_complete", {
                "step": "gemini", "pipeline": "gemini",
                "label": f"LLM Full Shot: {gem_entities} entities, {len(gem_vis['edges'])} relationships",
                "duration_ms": int(gem_elapsed * 1000),
                "tokens": gem_tokens,
            })
            await asyncio.sleep(0)

            yield sse_event("kg_ready", _fresh_kg_event(
                "gemini", gem_kg, gem_tokens, gem_elapsed, actual_kb,
                "gemini", nct_id, _cfg_keys["gemini"],
                model=model, truncated=gem_truncated,
            ))
            await asyncio.sleep(0)

            _cache_save("gemini", nct_id, _cfg_keys["gemini"], gem_kg,
                         tokens=gem_tokens, elapsed=gem_elapsed,
                         actual_kb=actual_kb)
        except Exception as e:
            # INIT-001 Sprint 02 / BUG-010 response: log full traceback.
            logger.exception("Clinical LLM Full Shot failed")
            yield sse_event("error", {
                "step": "gemini", "pipeline": "gemini",
                "message": f"LLM Full Shot failed: {e}",
                "recoverable": True,
            })
            await asyncio.sleep(0)

    # Wait for LLM Multi-Stage (or emit cached result)
    if mod_from_log:
        yield sse_event("kg_ready", _cached_kg_event(
            "modular", mod_logged_run, actual_kb, "modular", nct_id, _cfg_keys["modular"],
            model=model,
        ))
        await asyncio.sleep(0)
    elif modular_task:
        try:
            # Wave B: clinical modular now returns 5-tuple (h_tokens, l_tokens)
            # matching agentic-analyst. h_tokens / l_tokens both 0 for clinical
            # until ExtractionResult surfaces per-stage counts.
            mod_kg, mod_h_tokens, mod_l_tokens, mod_elapsed, mod_errors = await modular_task
            mod_tokens = mod_h_tokens + mod_l_tokens
            mod_vis = build_vis_data(mod_kg)
            mod_entities = len(mod_vis["nodes"])

            yield sse_event("step_complete", {
                "step": "modular", "pipeline": "modular",
                "label": f"LLM Multi-Stage: {mod_entities} entities, {len(mod_vis['edges'])} relationships",
                "duration_ms": int(mod_elapsed * 1000),
                "tokens": mod_tokens,
            })
            await asyncio.sleep(0)

            yield sse_event("kg_ready", _fresh_kg_event(
                "modular", mod_kg, mod_tokens, mod_elapsed, actual_kb,
                "modular", nct_id, _cfg_keys["modular"],
                model=model,
            ))
            await asyncio.sleep(0)

            _cache_save("modular", nct_id, _cfg_keys["modular"], mod_kg,
                         tokens=mod_tokens, elapsed=mod_elapsed,
                         actual_kb=actual_kb)
        except Exception as e:
            # INIT-001 Sprint 02 / BUG-010 response: log full traceback.
            logger.exception("Clinical LLM Multi-Stage failed")
            yield sse_event("error", {
                "step": "modular", "pipeline": "modular",
                "message": f"LLM Multi-Stage failed: {e}",
                "recoverable": True,
            })
            await asyncio.sleep(0)

    total_ms = int((time.time() - pipeline_start) * 1000)
    yield sse_event("done", {"total_duration_ms": total_ms})


from extraction import (  # noqa: E402  (re-export for legacy call sites)
    _run_agentic_analyst,
    _run_agentic_flash,
    _run_clinical_gemini_full_shot,
    _run_clinical_modular,
    _run_kgenskills,
)

# --- Intelligence Pipeline (Tab 1) ---

# In-memory cache of KG results per ticker (for cross-tab reuse).
# Keyed by ticker — single-user demo; not suitable for multi-user production.
_KG_CACHE_MAX = 5  # Max tickers in memory


class _LRUCache:
    """Thread-safe LRU cache for KG data. Evicts oldest entry when full.

    NOTE: Returns mutable references to cached dicts. Deep mutations
    (e.g., cache[key]["sub_key"] = val) are not tracked by the LRU —
    only top-level get/set operations touch the access order. Acceptable
    for the single-user demo; replace with Redis/memcached if generalised.
    """

    def __init__(self, maxsize: int = _KG_CACHE_MAX):
        self._data: OrderedDict = OrderedDict()
        self._maxsize = maxsize

    def get(self, key, default=None):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return default

    def __setitem__(self, key, value):
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        self._data.move_to_end(key)
        return self._data[key]

    def pop(self, key, *args):
        return self._data.pop(key, *args)

    def clear(self):
        self._data.clear()


_cache_lock = threading.Lock()
_kg_cache = _LRUCache()  # ticker -> {"kgs_kg": ..., "text": ..., "info": ...}


async def _warm_cache_from_disk(ticker: str, domain: str = "financial") -> dict | None:
    """Load KG from most recent KGSpin run log and rebuild text from cache.

    Supports both financial tickers (SEC EDGAR) and clinical NCT IDs (gold data).
    """
    ticker = ticker.upper()
    kgen_dir = _kgen_run_log.LOG_ROOT / ticker
    if not kgen_dir.exists():
        return None

    # Find most recent KGSpin log file (any config)
    files = sorted(kgen_dir.glob("kgen_*.json"), reverse=True)
    if not files:
        return None

    run_data = json.loads(files[0].read_text())
    kgs_kg = run_data.get("kg")
    if not kgs_kg:
        return None

    # Extract corpus_kb from filename config key
    fname = files[0].stem
    corpus_kb = DEFAULT_CORPUS_KB
    for segment in fname.split("@")[0].split("_"):
        if segment.startswith("corpus_kb="):
            try:
                corpus_kb = int(segment.split("=")[1])
            except ValueError:
                pass
            break

    demo_text = ""
    raw_html = ""
    actual_kb = 0

    if domain == "clinical" or ticker.startswith("NCT"):
        # Clinical: rebuild text from gold data publications
        try:
            gold_dir = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "gold" / "clinical"
            gold_path = gold_dir / f"{ticker}.json"
            if gold_path.exists():
                import json as _json
                gold_data = _json.loads(gold_path.read_text())
                sections = []
                for doc in gold_data.get("input_documents", []):
                    title = doc.get("title", "Untitled")
                    text = doc.get("text", doc.get("abstract", ""))
                    if text:
                        sections.append(f"--- Publication: {title} ---\n{text}")
                demo_text = "\n\n".join(sections)
                actual_kb = len(demo_text.encode("utf-8")) / 1024
        except Exception:
            pass
    else:
        # Financial: re-fetch SEC text from local EDGAR cache
        try:
            def _rebuild_text():
                from kgspin_plugin_financial.data_sources.edgar import EdgarDataSource
                edgar_ds = EdgarDataSource(cache_dir=DATA_LAKE_ROOT / "financial" / "sec_edgar")
                sec_doc = edgar_ds.get_document(ticker, "10-K")
                if not sec_doc:
                    return "", "", 0
                _bndl, _full, trunc, a_kb, _chunks = _parse_and_chunk(
                    sec_doc.raw_html, ticker, corpus_kb
                )
                return trunc, sec_doc.raw_html, a_kb
            demo_text, raw_html, actual_kb = await asyncio.to_thread(_rebuild_text)
        except Exception:
            pass  # KG still usable for lineage even without text

    entry = {
        "kgs_kg": kgs_kg,
        "text": demo_text,
        "raw_html": raw_html,
        "info": {"doc_id": ticker, "domain": domain},
        "corpus_kb": corpus_kb,
        "actual_kb": actual_kb,
    }
    with _cache_lock:
        _kg_cache[ticker] = entry
    return entry


IMPACT_QUESTIONS = {
    "financial": [
        "Who are the top executives of this company and what are their roles?",
        "What acquisitions has this company made and what was their strategic purpose?",
        "Who are the main competitors and how does this company differentiate?",
        "What is the company's revenue and how has it changed?",
        "What geographic regions does this company operate in?",
        "What regulatory bodies oversee the divisions that completed this company's largest acquisition?",
    ],
    "clinical": [
        "What is the primary condition or disease being studied in this trial?",
        "What interventions or drugs are being tested and what are their mechanisms of action?",
        "What are the primary and secondary endpoints of this trial?",
        "What are the inclusion and exclusion criteria for patient enrollment?",
        "Who are the principal investigators and what institutions are sponsoring this trial?",
        "What adverse events or safety signals have been reported?",
    ],
}

# Questions requiring multi-hop reasoning (transitive graph traversal)
_MULTIHOP_QUESTIONS = {
    "What regulatory bodies oversee the divisions that completed this company's largest acquisition?",
}

# Sprint 100: "Why This Matters" killer questions — one per domain.
# Designed to demonstrate KG advantage over raw text retrieval.
WTM_QUESTIONS = {
    "financial": (
        "Identify all subsidiaries and joint ventures mentioned in the Risk Factors "
        "that are not explicitly listed in Legal Proceedings or Related Party Transactions. "
        "For these entities, what is the total aggregate unfunded commitment or contingent "
        "liability mentioned across the entire document?"
    ),
    "clinical": (
        "Compare the Adverse Events reported in Phase II trials against the Exclusion "
        "Criteria for the current Phase III expansion. Which specific biomarkers are "
        "linked to patient dropouts in Phase II that have not yet been added as exclusion "
        "criteria for Phase III?"
    ),
}


async def run_intelligence(
    ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB,
    model: str = DEFAULT_GEMINI_MODEL, domain: str = "financial",
    llm_alias: str | None = None,
) -> AsyncGenerator[str, None]:
    """Intelligence pipeline - multi-source ingestion with real-time progress.

    Uses the same extraction logic as the compare tab (smart chunk selection,
    per-chunk H-Module, DocumentContext passed to L-Module) so that the
    resulting KG is identical for the same SEC filing.

    Supports both financial (SEC + finance/healthcare news) and clinical
    (PubMed gold data + healthcare news) domains.
    """
    is_clinical = domain == "clinical" or ticker.startswith("NCT")
    pipeline_start = time.time()
    yield ": connected\n\n"
    await asyncio.sleep(0)

    # Step 1: Resolve ticker / trial
    yield sse_event("step_start", {"step": "resolve_ticker", "label": "Resolving..." })
    await asyncio.sleep(0)
    t0 = time.time()
    if is_clinical:
        gold_dir = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "gold" / "clinical"
        gold_path = gold_dir / f"{ticker}.json"
        if gold_path.exists():
            import json as _json
            gold_data = _json.loads(gold_path.read_text())
            trial_title = gold_data.get("trial_title", ticker)
            info = {"name": trial_title, "domain": "clinical", "doc_id": ticker}
        else:
            info = {"name": ticker, "domain": "clinical", "doc_id": ticker}
            gold_data = None
    else:
        info = await asyncio.to_thread(resolve_ticker, ticker)
        gold_data = None
    duration = int((time.time() - t0) * 1000)
    yield sse_event("step_complete", {
        "step": "resolve_ticker",
        "label": f"Resolved: {ticker} \u2192 {info['name'][:60]}",
        "duration_ms": duration,
    })
    await asyncio.sleep(0)

    # Check if compare tab already extracted a KG for this ticker
    with _cache_lock:
        cached = _kg_cache.get(ticker)

    cached_kgs_kg = None
    source_label = "clinical" if is_clinical else "SEC"
    if cached and cached.get("kgs_kg"):
        cached_kgs_kg = cached["kgs_kg"]
        yield sse_event("step_start", {"step": "load_cache", "label": f"Loading cached {source_label} graph..."})
        cached_entities = len(cached_kgs_kg.get("entities", []))
        cached_rels = len(cached_kgs_kg.get("relationships", []))
        yield sse_event("step_complete", {
            "step": "load_cache",
            "label": f"Loaded {source_label} graph ({cached_entities} entities, {cached_rels} rels)",
            "duration_ms": 0,
        })
        await asyncio.sleep(0)

    # Step 2: Fetch primary documents (SEC for financial, gold data for clinical)
    articles = []  # list of (source_type, clean_text)
    sec_doc = None
    sec_text = None

    if is_clinical:
        # Clinical: load publications from gold data
        if not cached_kgs_kg:
            yield sse_event("step_start", {"step": "fetch_sec", "label": "Loading clinical publications..."})
            await asyncio.sleep(0)
            t0 = time.time()
            if gold_data and gold_data.get("input_documents"):
                for doc_idx, doc in enumerate(gold_data["input_documents"]):
                    title = doc.get("title", "Untitled")
                    text = doc.get("text", doc.get("abstract", ""))
                    if text:
                        yield sse_event("article_fetched", {
                            "source": "pubmed",
                            "source_id": f"{ticker}_pub_{doc_idx}",
                            "title": title[:100],
                            "chars": len(text),
                        })
                        articles.append(("pubmed", text))
                        await asyncio.sleep(0)
                duration = int((time.time() - t0) * 1000)
                yield sse_event("step_complete", {
                    "step": "fetch_sec",
                    "label": f"Loaded {len(articles)} publications",
                    "duration_ms": duration,
                })
            else:
                yield sse_event("step_complete", {
                    "step": "fetch_sec",
                    "label": "No gold data publications found",
                    "duration_ms": int((time.time() - t0) * 1000),
                })
            await asyncio.sleep(0)
        else:
            # Already extracted — use cached text
            clinical_text = cached.get("text", "")
            if clinical_text:
                yield sse_event("article_fetched", {
                    "source": "pubmed",
                    "source_id": f"{ticker}_clinical",
                    "title": f"{info['name'][:60]} (cached)",
                    "chars": len(clinical_text),
                    "cached": True,
                })
                articles.append(("pubmed", clinical_text))
            await asyncio.sleep(0)
    elif not cached_kgs_kg:
        yield sse_event("step_start", {"step": "fetch_sec", "label": "Fetching SEC 10-K filing..."})
        await asyncio.sleep(0)
        t0 = time.time()

        def _fetch_sec_doc(t):
            try:
                from kgspin_plugin_financial.data_sources.edgar import EdgarDataSource
                edgar_ds = EdgarDataSource(cache_dir=DATA_LAKE_ROOT / "financial" / "sec_edgar")
                doc = edgar_ds.get_document(t, "10-K")
                return doc
            except Exception as e:
                logger.warning(f"EDGAR fetch failed: {e}")
            return None

        sec_doc = await asyncio.to_thread(_fetch_sec_doc, ticker)
        duration = int((time.time() - t0) * 1000)

        if sec_doc:
            sec_text = sec_doc.clean_text
            size_kb = len(sec_doc.raw_html) // 1024
            yield sse_event("step_complete", {
                "step": "fetch_sec",
                "label": f"Fetched 10-K ({size_kb}KB)",
                "duration_ms": duration,
                "details": {
                    "source_url": sec_doc.source_url,
                    "filing_date": sec_doc.filing_date,
                    "accession_number": sec_doc.accession_number,
                    "company_name": info.get("name", "") or sec_doc.company_name or ticker,
                    "doc_id": ticker,
                    "size_kb": size_kb,
                },
            })
            yield sse_event("article_fetched", {
                "source": "sec_filing",
                "source_id": f"{ticker}_10K",
                "title": f"{info['name']} 10-K Annual Report",
                "chars": len(sec_text),
            })
            articles.append(("sec_filing", sec_text))
        else:
            yield sse_event("step_complete", {
                "step": "fetch_sec",
                "label": "SEC filing not available",
                "duration_ms": duration,
            })
        await asyncio.sleep(0)
    else:
        # SEC already extracted — skip fetch
        sec_text = cached.get("text", "")
        yield sse_event("article_fetched", {
            "source": "sec_filing",
            "source_id": f"{ticker}_10K",
            "title": f"{info['name']} 10-K (cached)",
            "chars": len(sec_text),
            "cached": True,
        })
        articles.append(("sec_filing", sec_text))
        await asyncio.sleep(0)

    # Step 3: Fetch news articles (per-source for granular progress)
    yield sse_event("step_start", {"step": "fetch_news", "label": "Fetching news articles..."})
    await asyncio.sleep(0)
    t0 = time.time()

    # News fetching goes through the canonical
    # kgspin-plugin-news-financial NewsApiProvider via _fetch_newsapi_articles.

    news_warning: str | None = None  # ProviderConfigurationError surface for the SSE stream

    finance_articles = []
    if not is_clinical:
        yield sse_event("step_progress", {
            "step": "fetch_news",
            "label": "Querying news (newsapi.org)...",
        })
        await asyncio.sleep(0)

        def _fetch_finance_news_via_plugin(company_name, t):
            try:
                items = _fetch_newsapi_articles(query=t, limit=5)
                if len(items) < 3:
                    items.extend(_fetch_newsapi_articles(query=company_name, limit=5))
                results = []
                for a in items[:8]:
                    if a.get("text") and len(a["text"].strip()) > 50:
                        results.append(("finance_news", a))
                return results, None
            except ProviderConfigurationError as e:
                logger.exception("[NEWSAPI_CONFIG] %s", e)
                return [], e.hint
            except CorpusFetchError as e:
                logger.exception("[NEWSAPI_FETCH_FAIL] %s", e)
                return [], e.actionable_hint

        finance_articles, news_warning = await asyncio.to_thread(
            _fetch_finance_news_via_plugin, info["name"], ticker
        )

    yield sse_event("step_progress", {
        "step": "fetch_news",
        "label": f"Querying healthcare news...{f' ({len(finance_articles)} finance articles found)' if finance_articles else ''}",
    })
    await asyncio.sleep(0)

    def _fetch_healthcare_news_via_plugin(company_name):
        try:
            items = _fetch_newsapi_articles(query=f"{company_name} drug clinical", limit=5)
            results = []
            for a in items[:8]:
                if a.get("text") and len(a["text"].strip()) > 50:
                    results.append(("healthcare_news", a))
            return results, None
        except ProviderConfigurationError as e:
            logger.exception("[NEWSAPI_CONFIG] %s", e)
            return [], e.hint
        except CorpusFetchError as e:
            logger.exception("[NEWSAPI_FETCH_FAIL] %s", e)
            return [], e.actionable_hint

    healthcare_articles, hc_warning = await asyncio.to_thread(
        _fetch_healthcare_news_via_plugin, info["name"]
    )
    if news_warning is None:
        news_warning = hc_warning

    news_articles = finance_articles + healthcare_articles
    duration = int((time.time() - t0) * 1000)
    yield sse_event("step_complete", {
        "step": "fetch_news",
        "label": f"Fetched {len(news_articles)} news articles",
        "duration_ms": duration,
    })
    await asyncio.sleep(0)

    for news_idx, (source_type, article) in enumerate(news_articles):
        # Sprint 06 Task 2 (VP Prod): SSE payload now carries published_at +
        # source_name in addition to title.
        yield sse_event("article_fetched", {
            "source": source_type,
            "source_id": f"{source_type}_{news_idx}",
            "title": (article.get("title") or "")[:100],
            "source_name": article.get("source_name", ""),
            "published_at": article.get("published_at", ""),
            "url": article.get("url", ""),
            "chars": len(article.get("text", "")),
        })
        articles.append((source_type, article.get("text", "")))
        await asyncio.sleep(0)

    if not news_articles:
        # Sprint 06 Task 2 (VP Prod): distinguish missing-key from zero-results
        if news_warning:
            yield sse_event("news_empty", {
                "message": news_warning,
                "reason": "newsapi_configuration",
                "finance_searched": not is_clinical,
                "healthcare_searched": True,
            })
        else:
            yield sse_event("news_empty", {
                "message": f"No recent news articles found for {ticker}.",
                "reason": "zero_results",
                "finance_searched": not is_clinical,
                "healthcare_searched": True,
            })
        await asyncio.sleep(0)

    if not articles:
        yield sse_event("error", {
            "step": "fetch_news",
            "message": "No data sources available",
            "recoverable": False,
        })
        yield sse_event("done", {"total_duration_ms": int((time.time() - pipeline_start) * 1000)})
        return

    if await request.is_disconnected():
        return

    # Sprint 33.13: Branch on cached vs non-cached path
    bundle = _get_bundle()
    news_only = [(s, t) for s, t in articles if s != "sec_filing"]

    # --- Wave J (PRD-056 v2): per-run bridging-first prep ------------------
    # Fetch the hub registry once per intel run; cache it on the module-level
    # cache. Admission tokens for bundle-consistent normalization are derived
    # from the bundle's TypeRegistry. SourceRefs + ExtractionContexts are
    # built per-article at merge time (commit 2). Bridge edges are materialized
    # from hub-registry matches after each merge.
    #
    # Note on the extractor call path: the demo calls `KnowledgeGraphExtractor.
    # extract()` directly (kgspin-core façade), which does not currently forward
    # the `extraction_context` kwarg defined on the Extractor ABC. Since
    # bundle-loaded bridge semantics are applied demo-side at merge time
    # (hub-registry + cross_hub_relations), we do not need the façade to consume
    # the context this sprint. Pushing the kwarg into the façade is tracked as
    # a kgspin-core Wave I follow-up.
    intel_domain = info.get("domain") or ("clinical" if is_clinical else "financial")
    hub_registry = await _fetch_hub_registry(intel_domain)
    admission_tokens = _bundle_admission_tokens(bundle)
    cross_hub_relations = _cross_hub_relations_from_bundle(bundle)
    filing_source_ref_dict = {
        "kind": "filing",
        "origin": "sec" if not is_clinical else "pubmed",
        "article_id": f"{ticker}_10K" if not is_clinical else f"{ticker}_pub",
        "fetched_at": "",
    }
    # Running bridge-creation state: populated in the merge loops below so the
    # graph_delta SSE event (commit 3) can surface bridges per article.
    bridges_by_article: list[list[dict]] = []
    spokes_by_article: list[list[dict]] = []

    # Sprint 33.14c: Shared news extraction helper (used by both paths)
    def _extract_news_articles(
        news_list, company_name, t, bndl, doc_ctx, _loop, _queue
    ):
        """Extract each news article individually, preserving source provenance.

        Uses loop.call_soon_threadsafe() for all queue writes since this
        function runs in asyncio.to_thread (background thread).
        """
        from kgspin_core.execution.extractor import KnowledgeGraphExtractor, DocumentChunker

        all_kgs = []
        # Pre-count total chunks for progress bar
        chunker_tmp = DocumentChunker(max_chunk_size=bndl.max_chunk_size)
        total_chunks = 0
        art_chunk_counts = []
        for idx, (source_type, title, text) in enumerate(news_list):
            n = len(chunker_tmp.chunk(text, doc_id=f"precount_{idx}"))
            art_chunk_counts.append(n)
            total_chunks += n

        chunks_done = 0
        for art_idx, (source_type, title, text) in enumerate(news_list):
            source_id = f"{source_type}_{art_idx}"
            extractor = KnowledgeGraphExtractor(bndl)
            art_chunks_done = 0
            art_total = art_chunk_counts[art_idx]

            def on_chunk(ci, ct, _aidx=art_idx, _atotal=art_total):
                nonlocal chunks_done, art_chunks_done
                chunks_done += 1
                art_chunks_done += 1
                _loop.call_soon_threadsafe(
                    _queue.put_nowait, ("chunk", chunks_done, total_chunks)
                )
                _loop.call_soon_threadsafe(
                    _queue.put_nowait, ("article_chunk", _aidx, art_chunks_done, _atotal)
                )

            result = extractor.extract(
                text=text,
                source_document=source_id,
                document_context=doc_ctx,
                on_chunk_complete=on_chunk,
            )
            kg = result.to_dict()
            all_kgs.append(kg)
            # Sprint 33.17b: Report vis-filtered entity counts (not raw)
            vis_ents = sum(1 for e in kg.get("entities", []) if e.get("entity_type") in _VIS_ACTOR_TYPES)
            # Sprint 33.17c: Vis-filter rels — only count rels where both endpoints are actor-type
            vis_ent_set = {e.get("text", "").lower().strip() for e in kg.get("entities", []) if e.get("entity_type") in _VIS_ACTOR_TYPES}
            vis_rels = sum(1 for r in kg.get("relationships", [])
                          if r.get("subject", {}).get("text", "").lower().strip() in vis_ent_set
                          and r.get("object", {}).get("text", "").lower().strip() in vis_ent_set)
            _loop.call_soon_threadsafe(
                _queue.put_nowait,
                ("article_done", art_idx, vis_ents, vis_rels),
            )

        return all_kgs

    if not cached_kgs_kg:
        # Non-cached path: full SEC extraction via _run_kgenskills.
        # Uses the SAME code path as the Compare tab to guarantee identical results.
        # No chunk budget truncation — processes ALL ~150 SEC chunks.

        sec_kg = {"entities": [], "relationships": []}
        trunc_text = ""

        if sec_doc:
            yield sse_event("step_start", {"step": "extraction", "label": "Extracting SEC knowledge graph..."})
            await asyncio.sleep(0)

            bundle, _full_text, trunc_text, _kb, _all_chunks = await asyncio.to_thread(
                _parse_and_chunk, sec_doc.raw_html, ticker, corpus_kb
            )

            progress_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def on_kgs_progress(idx, total, ent_count=0):
                loop.call_soon_threadsafe(
                    progress_queue.put_nowait, ("chunk", idx, total, ent_count)
                )

            def on_l_module_start(num_chunks):
                loop.call_soon_threadsafe(
                    progress_queue.put_nowait, ("l_module_start", num_chunks)
                )

            # Sprint 102: Build metadata dict for document seeding
            _sec_doc_metadata = {
                "company_name": info.get("name", ""),
                "doc_id": ticker,
                "cik": getattr(sec_doc, "cik", "") or "",
                "accession_number": getattr(sec_doc, "accession_number", "") or "",
                "filing_date": getattr(sec_doc, "filing_date", "") or "",
                "fiscal_year_end": getattr(sec_doc, "fiscal_year_end", "") or "",
            }

            t0 = time.time()
            sec_task = asyncio.create_task(asyncio.to_thread(
                _run_kgenskills, trunc_text, info["name"], ticker, bundle,
                _pipeline_ref_from_strategy("fan_out"),
                _get_registry_client(),
                on_kgs_progress, sec_doc.raw_html, on_l_module_start,
                document_metadata=_sec_doc_metadata,
            ))

            # Poll progress → SSE events
            while not sec_task.done():
                try:
                    msg = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                    if msg[0] == "chunk":
                        _, idx, total, ent_count = msg
                        yield sse_event("step_progress", {
                            "step": "extraction",
                            "progress": idx,
                            "total": total,
                            "label": f"H-Module: Chunk {idx}/{total} ({ent_count} entities)",
                        })
                        # Sprint 33.16: SEC article progress bar (article_idx=0)
                        yield sse_event("article_progress", {
                            "article_idx": 0,
                            "progress": idx,
                            "total": total,
                        })
                    elif msg[0] == "l_module_start":
                        yield sse_event("step_progress", {
                            "step": "extraction",
                            "label": "L-Module: Extracting relationships...",
                        })
                    await asyncio.sleep(0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0)
                except Exception:
                    break
                if await request.is_disconnected():
                    sec_task.cancel()
                    return

            # Drain remaining progress
            while not progress_queue.empty():
                msg = progress_queue.get_nowait()
                if msg[0] == "chunk":
                    _, idx, total, ent_count = msg
                    yield sse_event("step_progress", {
                        "step": "extraction",
                        "progress": idx, "total": total,
                        "label": f"H-Module: Chunk {idx}/{total} ({ent_count} entities)",
                    })
                    yield sse_event("article_progress", {
                        "article_idx": 0, "progress": idx, "total": total,
                    })
                await asyncio.sleep(0)

            try:
                sec_kg = sec_task.result()
            except Exception as e:
                logger.exception("Intelligence SEC extraction failed")
                yield sse_event("error", {
                    "step": "extraction", "pipeline": "intelligence",
                    "message": f"SEC extraction failed: {e}",
                    "recoverable": False,
                })
                yield sse_event("done", {"total_duration_ms": int((time.time() - t0) * 1000)})
                return
            duration = int((time.time() - t0) * 1000)
            sec_ents = len(sec_kg.get("entities", []))
            sec_rels = len(sec_kg.get("relationships", []))
            # Sprint 33.17b: Report vis-filtered counts for article sidebar
            sec_vis_ents = sum(1 for e in sec_kg.get("entities", []) if e.get("entity_type") in _VIS_ACTOR_TYPES)
            # Sprint 33.17c: Vis-filter rels — only count rels where both endpoints are actor-type
            sec_vis_ent_set = {e.get("text", "").lower().strip() for e in sec_kg.get("entities", []) if e.get("entity_type") in _VIS_ACTOR_TYPES}
            sec_vis_rels = sum(1 for r in sec_kg.get("relationships", [])
                               if r.get("subject", {}).get("text", "").lower().strip() in sec_vis_ent_set
                               and r.get("object", {}).get("text", "").lower().strip() in sec_vis_ent_set)
            yield sse_event("step_complete", {
                "step": "extraction",
                "label": f"SEC: {sec_ents} entities, {sec_rels} relationships",
                "duration_ms": duration,
            })
            # Sprint 33.16: Mark SEC article card as done
            yield sse_event("article_extracted", {
                "article_idx": 0,
                "entities": sec_vis_ents,
                "relationships": sec_vis_rels,
            })
            await asyncio.sleep(0)

            # Cache SEC-only KG for shared access (Compare tab can reuse)
            with _cache_lock:
                _kg_cache[ticker] = {"kgs_kg": sec_kg, "text": trunc_text, "raw_html": sec_doc.raw_html, "info": info}

        if await request.is_disconnected():
            return

        # Phase B: Per-article news extraction
        article_kgs = []
        if news_articles:
            yield sse_event("step_start", {
                "step": "news_extraction",
                "label": f"Extracting {len(news_articles)} news articles...",
            })
            await asyncio.sleep(0)

            from kgspin_core.execution.h_module import DocumentContext, ExtractedEntity
            coref_map = {
                "we": info["name"], "We": info["name"],
                "our": info["name"], "Our": info["name"],
                "the company": info["name"], "the Company": info["name"],
                "The Company": info["name"],
            }
            # Sprint 33.15c: Seed from H-Module entities (with aliases) for news matching
            h_ents = sec_kg.get("_h_module_entities", sec_kg.get("entities", []))
            seed_entities = [
                ExtractedEntity.from_dict(e) for e in h_ents
                if e.get("entity_type") in _VIS_ACTOR_TYPES
            ]
            doc_ctx = DocumentContext(
                main_entity=info["name"],
                main_entity_type="ORGANIZATION",
                entities=seed_entities,
                coreference_map=coref_map,
            )

            extraction_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            t0 = time.time()
            news_task = asyncio.create_task(asyncio.to_thread(
                _extract_news_articles, news_articles, info["name"], ticker,
                bundle, doc_ctx, loop, extraction_queue,
            ))

            while not news_task.done():
                try:
                    msg = await asyncio.wait_for(extraction_queue.get(), timeout=0.5)
                    if msg[0] == "chunk":
                        _, done, total = msg
                        yield sse_event("step_progress", {
                            "step": "news_extraction",
                            "progress": done,
                            "total": total,
                            "label": f"News: chunk {done}/{total}",
                        })
                    elif msg[0] == "article_chunk":
                        _, aidx, done, total = msg
                        yield sse_event("article_progress", {
                            "article_idx": aidx + 1,
                            "progress": done,
                            "total": total,
                        })
                    elif msg[0] == "article_done":
                        _, art_idx, ent_count, rel_count = msg
                        yield sse_event("article_extracted", {
                            "article_idx": art_idx + 1,
                            "entities": ent_count,
                            "relationships": rel_count,
                        })
                    await asyncio.sleep(0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0)
                except Exception:
                    break
                if await request.is_disconnected():
                    news_task.cancel()
                    return

            # Drain queue
            while not extraction_queue.empty():
                msg = extraction_queue.get_nowait()
                if msg[0] == "chunk":
                    _, done, total = msg
                    yield sse_event("step_progress", {
                        "step": "news_extraction", "progress": done,
                        "total": total, "label": f"News: chunk {done}/{total}",
                    })
                elif msg[0] == "article_chunk":
                    _, aidx, done, total = msg
                    yield sse_event("article_progress", {
                        "article_idx": aidx + 1,
                        "progress": done, "total": total,
                    })
                elif msg[0] == "article_done":
                    _, art_idx, ent_count, rel_count = msg
                    yield sse_event("article_extracted", {
                        "article_idx": art_idx + 1,
                        "entities": ent_count, "relationships": rel_count,
                    })
                await asyncio.sleep(0)

            try:
                article_kgs = news_task.result()
            except Exception as e:
                logger.exception("Intelligence news extraction failed")
                yield sse_event("error", {
                    "step": "news_extraction", "pipeline": "intelligence",
                    "message": f"News extraction failed: {e}",
                    "recoverable": False,
                })
                yield sse_event("done", {"total_duration_ms": int((time.time() - t0) * 1000)})
                return
            duration = int((time.time() - t0) * 1000)
            yield sse_event("step_complete", {
                "step": "news_extraction",
                "label": f"Extracted {len(article_kgs)} news articles",
                "duration_ms": duration,
            })
            await asyncio.sleep(0)

        # Phase C: Runtime merge — SEC base graph + news article graphs.
        # Wave J (MH #1, #10): provenance-preserving merge with bundle
        # admission tokens + per-article SourceRef.
        # Wave J (MH #2): bridge-edge creation from hub-registry matches,
        # gated by `HybridUtilityGate` (cross-hub bridges always commit).
        kgs_kg = sec_kg
        for i, akg in enumerate(article_kgs):
            overlay_sref = None
            if i < len(news_articles):
                news_source_type, news_article = news_articles[i]
                overlay_sref = _make_source_ref_for_article(
                    news_source_type, news_article, i
                )
            kgs_kg = _merge_kgs_with_provenance(
                kgs_kg, akg,
                admission_tokens=admission_tokens,
                base_source_ref=filing_source_ref_dict if i == 0 else None,
                overlay_source_ref=overlay_sref,
            )
            bridge_result = _create_bridges_from_matches(
                kgs_kg,
                current_hub=info.get("name"),
                hub_registry=hub_registry,
                cross_hub_relations=cross_hub_relations,
                admission_tokens=admission_tokens,
                source_ref=overlay_sref,
            )
            bridges_by_article.append(bridge_result.get("bridges_created", []))
            spokes_by_article.append(bridge_result.get("spokes_promoted", []))

        kgs_entities = len(kgs_kg.get("entities", []))
        kgs_rels = len(kgs_kg.get("relationships", []))

    else:
        # --- Sprint 33.13: Cached path — per-article extraction with provenance ---
        yield sse_event("step_start", {
            "step": "extraction",
            "label": f"Extracting {len(news_articles)} news articles...",
        })
        await asyncio.sleep(0)

        # Build DocumentContext from cached info for entity resolution
        from kgspin_core.execution.h_module import DocumentContext, ExtractedEntity
        coref_map = {
            "we": info["name"], "We": info["name"],
            "our": info["name"], "Our": info["name"],
            "the company": info["name"], "the Company": info["name"],
            "The Company": info["name"],
        }
        # Sprint 33.15c: Seed from H-Module entities (with aliases) for news matching
        h_ents = cached_kgs_kg.get("_h_module_entities", cached_kgs_kg.get("entities", []))
        seed_entities = [
            ExtractedEntity.from_dict(e) for e in h_ents
            if e.get("entity_type") in _VIS_ACTOR_TYPES
        ]
        document_context = DocumentContext(
            main_entity=info["name"],
            main_entity_type="ORGANIZATION",
            entities=seed_entities,
            coreference_map=coref_map,
        )

        # Thread-safe queue: capture event loop for call_soon_threadsafe
        extraction_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        t0 = time.time()
        extraction_task = asyncio.create_task(
            asyncio.to_thread(
                _extract_news_articles,
                news_articles, info["name"], ticker,
                bundle, document_context, loop, extraction_queue,
            )
        )

        # Poll for real chunk-level progress
        while not extraction_task.done():
            try:
                msg = await asyncio.wait_for(extraction_queue.get(), timeout=0.5)
                if msg[0] == "chunk":
                    _, done, total = msg
                    yield sse_event("step_progress", {
                        "step": "extraction",
                        "progress": done,
                        "total": total,
                        "label": f"Extracting: chunk {done}/{total}",
                    })
                elif msg[0] == "article_chunk":
                    _, aidx, done, total = msg
                    yield sse_event("article_progress", {
                        "article_idx": aidx + 1,
                        "progress": done,
                        "total": total,
                    })
                elif msg[0] == "article_done":
                    _, art_idx, ent_count, rel_count = msg
                    yield sse_event("article_extracted", {
                        "article_idx": art_idx + 1,  # +1 to skip SEC at index 0
                        "entities": ent_count,
                        "relationships": rel_count,
                    })
                await asyncio.sleep(0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0)
            except Exception:
                break
            if await request.is_disconnected():
                extraction_task.cancel()
                return

        # Drain queue
        while not extraction_queue.empty():
            msg = extraction_queue.get_nowait()
            if msg[0] == "chunk":
                _, done, total = msg
                yield sse_event("step_progress", {
                    "step": "extraction", "progress": done,
                    "total": total, "label": f"Extracting: chunk {done}/{total}",
                })
            elif msg[0] == "article_chunk":
                _, aidx, done, total = msg
                yield sse_event("article_progress", {
                    "article_idx": aidx + 1,
                    "progress": done, "total": total,
                })
            elif msg[0] == "article_done":
                _, art_idx, ent_count, rel_count = msg
                yield sse_event("article_extracted", {
                    "article_idx": art_idx + 1,
                    "entities": ent_count, "relationships": rel_count,
                })
            await asyncio.sleep(0)

        try:
            article_kgs = extraction_task.result()
        except Exception as e:
            logger.exception("Intelligence cached-path article extraction failed")
            yield sse_event("error", {
                "step": "extraction", "pipeline": "intelligence",
                "message": f"Article extraction failed: {e}",
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": int((time.time() - t0) * 1000)})
            return

        # Merge: cached SEC KG + all per-article news KGs.
        # Wave J: provenance-preserving merge + bridge creation per article.
        kgs_kg = cached_kgs_kg
        for i, akg in enumerate(article_kgs):
            overlay_sref = None
            if i < len(news_articles):
                news_source_type, news_article = news_articles[i]
                overlay_sref = _make_source_ref_for_article(
                    news_source_type, news_article, i
                )
            kgs_kg = _merge_kgs_with_provenance(
                kgs_kg, akg,
                admission_tokens=admission_tokens,
                base_source_ref=filing_source_ref_dict if i == 0 else None,
                overlay_source_ref=overlay_sref,
            )
            bridge_result = _create_bridges_from_matches(
                kgs_kg,
                current_hub=info.get("name"),
                hub_registry=hub_registry,
                cross_hub_relations=cross_hub_relations,
                admission_tokens=admission_tokens,
                source_ref=overlay_sref,
            )
            bridges_by_article.append(bridge_result.get("bridges_created", []))
            spokes_by_article.append(bridge_result.get("spokes_promoted", []))

        duration = int((time.time() - t0) * 1000)
        kgs_entities = len(kgs_kg.get("entities", []))
        kgs_rels = len(kgs_kg.get("relationships", []))

    # Compute per-type stats for the UI
    entity_types: dict = {}
    for ent in kgs_kg.get("entities", []):
        etype = ent.get("entity_type", "UNKNOWN")
        entity_types[etype] = entity_types.get(etype, 0) + 1
    rel_types: dict = {}
    for rel in kgs_kg.get("relationships", []):
        pred = rel.get("predicate", "?")
        rel_types[pred] = rel_types.get(pred, 0) + 1

    yield sse_event("step_complete", {
        "step": "extraction",
        "label": f"Extracted {kgs_entities} entities, {kgs_rels} relationships",
        "duration_ms": duration,
    })
    await asyncio.sleep(0)

    # Emit detailed source stats (reflect actual sources used)
    sources_used = []
    if sec_doc or cached_kgs_kg:
        sources_used.append("sec_filing")
    if news_only:
        sources_used.append("news_articles")
    yield sse_event("source_stats", {
        "source": "+".join(sources_used) if sources_used else "none",
        "entity_count": kgs_entities,
        "relationship_count": kgs_rels,
        "entity_types": entity_types,
        "relationship_types": rel_types,
        "duration_ms": duration,
    })
    await asyncio.sleep(0)

    # Sprint 33.14c: SEC-only KG is cached in Phase A (non-cached path) or
    # was already cached by Compare tab (cached path). No overwrite needed —
    # the shared cache always holds SEC-only data for cross-tab reuse.
    # Only write if nothing is cached yet (e.g., no SEC doc, news-only run).
    with _cache_lock:
        if ticker not in _kg_cache:
            _kg_cache[ticker] = {"kgs_kg": kgs_kg, "text": trunc_text if not cached_kgs_kg else "", "raw_html": sec_doc.raw_html if sec_doc else None, "info": info}

    # Build vis data and send KG
    kgs_vis = build_vis_data(kgs_kg)
    # Sprint 33.15b: Report post-filter vis counts (Bug 3/4)
    vis_entities = len(kgs_vis["nodes"])
    vis_rels = len(kgs_vis["edges"])

    # Sprint 33.17 (WI-4): Log Intelligence run to disk for history toggle
    _intel_cfg_hash = _intel_run_log.config_key(
        "intel", corpus_kb=corpus_kb, cv=DEMO_CACHE_VERSION
    )
    _intel_total = 0
    _cache_save("intel", ticker, _intel_cfg_hash, kgs_kg,
                 tokens=0, elapsed=duration / 1000,
                 model_fallback="kgen_deterministic")
    _intel_total = _intel_run_log.count(ticker, _intel_cfg_hash)

    yield sse_event("kg_ready", {
        "pipeline": "intelligence",
        "stats": {
            "entities": vis_entities,
            "relationships": vis_rels,
            "tokens": 0,
            "duration_ms": duration,
            "entity_types": entity_types,
            "relationship_types": rel_types,
        },
        "vis": kgs_vis,
        "total_runs": _intel_total,
    })
    await asyncio.sleep(0)

    # Done
    total_ms = int((time.time() - pipeline_start) * 1000)
    yield sse_event("done", {"total_duration_ms": total_ms})


# --- Impact Pipeline (Tab 3) ---


_DUAL_RESPONSE_FORMAT = (
    "## Response Format\n\n"
    "Return a JSON object with exactly these two fields:\n\n"
    '1. "natural_language_response": A clear, detailed paragraph that directly answers '
    "the question in plain English. Cite specific names, figures, and evidence. "
    "This must read like a human-written answer, NOT a list or bullet points.\n\n"
    '2. "structured_findings": An array of extracted findings, each with:\n'
    '   - "entity" (string): The primary entity\n'
    '   - "relationship" (string): How it relates\n'
    '   - "object" (string): The related entity or value\n'
    '   - "confidence" (number 0-1): Your confidence\n'
    '   - "evidence" (string): Brief source reference\n'
)


def _parse_dual_response(raw_text: str) -> dict:
    """Parse LLM JSON response with natural_language_response and structured_findings.

    Returns {"text": str|None, "json": dict|list|None}.
    """
    # Primary path: parse as JSON (expected from Gemini JSON mode)
    stripped = raw_text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            text = parsed.get("natural_language_response")
            findings = parsed.get("structured_findings")
            if findings is not None:
                return {"text": text, "json": {"structured_findings": findings}}
            # Fallback: LLM used "answer" key instead
            if "answer" in parsed:
                text = parsed["answer"]
                json_part = {k: v for k, v in parsed.items() if k != "answer"}
                return {"text": text, "json": json_part}
            # Sprint 100: Try common alternative text keys LLMs use
            for key in ("response", "result", "output", "text", "summary", "analysis"):
                if key in parsed and isinstance(parsed[key], str):
                    text = parsed[key]
                    json_part = {k: v for k, v in parsed.items() if k != key}
                    return {"text": text, "json": json_part or None}
            # Last resort: if there's a single string value, use it as text
            str_values = [(k, v) for k, v in parsed.items() if isinstance(v, str) and len(v) > 50]
            if len(str_values) == 1:
                text_key, text_val = str_values[0]
                json_part = {k: v for k, v in parsed.items() if k != text_key}
                return {"text": text_val, "json": json_part or None}
            # No recognized text field — stringify the whole response as readable text
            return {"text": None, "json": parsed}
        # Raw list (no text available)
        return {"text": None, "json": parsed}
    except json.JSONDecodeError:
        pass

    # Fallback for non-JSON text (shouldn't happen in JSON mode)
    answer_match = re.search(
        r"(?:^|\n)\s*ANSWER:\s*\n(.*?)(?:\n\s*JSON:\s*\n|$)", raw_text, re.DOTALL
    )
    json_match = re.search(
        r"```(?:json)?\s*\n(.*?)\n\s*```", raw_text, re.DOTALL
    )

    text_part = raw_text
    json_part = None

    if answer_match:
        text_part = answer_match.group(1).strip()
    if json_match:
        try:
            json_part = json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass
        if not answer_match:
            before = raw_text[: json_match.start()].strip()
            if before:
                text_part = before

    return {"text": text_part, "json": json_part}


async def run_impact(
    ticker: str, request: Request,
    llm_alias: str | None = None,
    legacy_model: str | None = None,
) -> AsyncGenerator[str, None]:
    """Impact analysis pipeline - compare LLM answers with vs without KG context."""
    pipeline_start = time.time()
    yield ": connected\n\n"
    await asyncio.sleep(0)

    # Check if we have a cached KG from compare or intelligence tab
    with _cache_lock:
        cached = _kg_cache.get(ticker)

    if not cached or "kgs_kg" not in cached:
        # Try to warm cache from disk logs
        yield sse_event("step_start", {"step": "warm_cache", "label": "Loading cached knowledge graph from disk..."})
        await asyncio.sleep(0)
        cached = await _warm_cache_from_disk(ticker)
        if cached and "kgs_kg" in cached:
            yield sse_event("step_complete", {"step": "warm_cache", "label": "Loaded KG from disk", "duration_ms": 0})
        else:
            yield sse_event("error", {
                "step": "extraction",
                "message": "No extraction data found. Please run extraction on the Compare tab first.",
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": 0})
            return

    yield sse_event("step_start", {"step": "load_cache", "label": "Loading cached knowledge graph..."})
    yield sse_event("step_complete", {
        "step": "load_cache",
        "label": f"Loaded KG ({len(cached['kgs_kg'].get('entities', []))} entities, "
                 f"{len(cached['kgs_kg'].get('relationships', []))} rels)",
        "duration_ms": 0,
    })
    await asyncio.sleep(0)

    kgs_kg = cached["kgs_kg"]
    extract_text = cached["text"]

    # Check Gemini availability
    gemini_available = bool(
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY")
    )
    if not gemini_available:
        yield sse_event("error", {
            "step": "qa",
            "message": "GEMINI_API_KEY not set. Impact analysis requires Gemini.",
            "recoverable": False,
        })
        yield sse_event("done", {"total_duration_ms": int((time.time() - pipeline_start) * 1000)})
        return

    if await request.is_disconnected():
        return

    # Run Q&A comparison
    questions = IMPACT_QUESTIONS.get("financial", [])
    yield sse_event("step_start", {"step": "qa", "label": f"Running {len(questions)} Q&A comparisons..."})
    await asyncio.sleep(0)

    # Build structured KG context
    kg_context = _build_kg_context_string(kgs_kg)
    # Build raw text context — send substantial text for fair comparison
    raw_context = extract_text[:30000]

    total_tokens_with = 0
    total_tokens_without = 0
    results = []

    for i, question in enumerate(questions):
        if await request.is_disconnected():
            return

        def _ask_both(q, kg_ctx, raw_ctx):
            from kgspin_demo_app.llm_backend import resolve_llm_backend
            backend = resolve_llm_backend(
                llm_alias=llm_alias,
                legacy_model=legacy_model,
                flow="impact",
            )

            # With KG — measure individually (not including sleep)
            prompt_with = (
                f"You are a financial analyst answering questions using a structured knowledge graph.\n\n"
                f"## Context (Knowledge Graph)\n{kg_ctx}\n\n"
                f"{_DUAL_RESPONSE_FORMAT}\n"
                f"## Question\n{q}"
            )
            t0 = time.time()
            result_with = backend.complete(prompt_with)
            time_with_ms = int((time.time() - t0) * 1000)

            # Delay between calls to avoid 429 rate limits
            time.sleep(1.5)

            # Without KG — measure individually
            prompt_without = (
                f"You are a financial analyst answering questions using a raw text document.\n\n"
                f"## Context (Document Text)\n{raw_ctx}\n\n"
                f"{_DUAL_RESPONSE_FORMAT}\n"
                f"## Question\n{q}"
            )
            t1 = time.time()
            result_without = backend.complete(prompt_without)
            time_without_ms = int((time.time() - t1) * 1000)

            parsed_with = _parse_dual_response(result_with.text)
            parsed_without = _parse_dual_response(result_without.text)

            return {
                "question": q,
                "with_graph": parsed_with["text"],
                "with_graph_json": parsed_with["json"],
                "without_graph": parsed_without["text"],
                "without_graph_json": parsed_without["json"],
                "prompt_with": prompt_with,
                "prompt_without": prompt_without,
                "tokens_with": result_with.tokens_used,
                "tokens_without": result_without.tokens_used,
                "time_with_ms": time_with_ms,
                "time_without_ms": time_without_ms,
                "is_multihop": q in _MULTIHOP_QUESTIONS,
            }

        try:
            qa_result = await asyncio.to_thread(_ask_both, question, kg_context, raw_context)
            total_tokens_with += qa_result["tokens_with"]
            total_tokens_without += qa_result["tokens_without"]
            results.append(qa_result)

            yield sse_event("qa_result", qa_result)
            await asyncio.sleep(0)
        except Exception as e:
            logger.warning(f"Impact Q&A failed for question {i}: {e}")
            yield sse_event("qa_result", {
                "question": question,
                "with_graph": f"Error: {e}",
                "without_graph": f"Error: {e}",
                "tokens_with": 0,
                "tokens_without": 0,
            })
            await asyncio.sleep(0)

        # Rate limit between questions — generous delay to avoid 429s
        await asyncio.sleep(3)

    qa_duration = int((time.time() - pipeline_start) * 1000)
    yield sse_event("step_complete", {
        "step": "qa",
        "label": f"Completed {len(results)} Q&A comparisons",
        "duration_ms": qa_duration,
        "tokens": total_tokens_with + total_tokens_without,
    })
    await asyncio.sleep(0)

    # Summary metrics
    avg_with = total_tokens_with // max(len(results), 1)
    avg_without = total_tokens_without // max(len(results), 1)
    savings = round((1 - avg_with / max(avg_without, 1)) * 100) if avg_without > 0 else 0
    avg_time_with = sum(r.get("time_with_ms", 0) for r in results) // max(len(results), 1)
    avg_time_without = sum(r.get("time_without_ms", 0) for r in results) // max(len(results), 1)

    yield sse_event("impact_summary", {
        "avg_tokens_with": avg_with,
        "avg_tokens_without": avg_without,
        "token_savings": savings,
        "questions_answered": len(results),
        "total_questions": len(questions),
        "avg_time_with_ms": avg_time_with,
        "avg_time_without_ms": avg_time_without,
    })
    await asyncio.sleep(0)

    # Quality analysis — compare with-graph vs without-graph answers
    qa_quality_analysis = None
    if results and gemini_available:
        yield sse_event("step_start", {"step": "quality_analysis", "label": "Analyzing answer quality..."})
        await asyncio.sleep(0)

        try:
            analysis_result = await asyncio.to_thread(
                functools.partial(
                    _run_impact_quality_analysis,
                    results,
                    llm_alias=llm_alias,
                    legacy_model=legacy_model,
                )
            )
            qa_quality_analysis = analysis_result
            yield sse_event("impact_quality_analysis", analysis_result)
            await asyncio.sleep(0)
        except Exception as e:
            logger.warning(f"Impact quality analysis failed: {e}")
            yield sse_event("impact_quality_analysis", {
                "analysis": {"summary": f"Analysis failed: {e}"},
                "tokens": 0,
            })

        yield sse_event("step_complete", {
            "step": "quality_analysis",
            "label": "Quality analysis complete",
            "duration_ms": 0,
        })
        await asyncio.sleep(0)

    # Save Q&A run to disk
    try:
        elapsed = time.time() - pipeline_start
        summary_data = {
            "avg_tokens_with": avg_with,
            "avg_tokens_without": avg_without,
            "token_savings": savings,
            "questions_answered": len(results),
            "total_questions": len(questions),
            "avg_time_with_ms": avg_time_with,
            "avg_time_without_ms": avg_time_without,
        }
        _impact_qa_run_log.log_qa_run(
            ticker=ticker,
            results=results,
            summary=summary_data,
            quality_analysis=qa_quality_analysis,
            elapsed_seconds=elapsed,
        )
    except Exception as e:
        logger.warning(f"Failed to save impact Q&A run log: {e}")

    total_ms = int((time.time() - pipeline_start) * 1000)
    yield sse_event("done", {"total_duration_ms": total_ms})


def _run_impact_quality_analysis(
    results: list,
    *,
    llm_alias: str | None = None,
    legacy_model: str | None = None,
) -> dict:
    """Analyze the quality of with-graph vs without-graph answers.

    Stage 0.5.4 (ADR-002): selector precedence — ``llm_alias`` → legacy
    ``model`` → flow override (``impact_llm`` / ``quality_analysis_llm``
    when set) → ``default_alias``. Shares the ``quality_analysis`` flow
    with :func:`run_quality_analysis` — set ``llm.quality_analysis_llm``
    to override.
    """
    from kgspin_demo_app.llm_backend import resolve_llm_backend

    try:
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="quality_analysis",
        )

        qa_pairs = []
        for i, r in enumerate(results):
            with_text = (r.get("with_graph") or "No text available")[:500]
            without_text = (r.get("without_graph") or "No text available")[:500]
            qa_pairs.append(
                f"### Question {i+1}: {r['question']}\n"
                f"**With KG:** {with_text}\n"
                f"**Without KG:** {without_text}\n"
            )

        prompt = f"""Analyze the quality of these Q&A comparisons where the same questions
were answered using a structured knowledge graph (With KG) vs raw document text (Without KG).

{chr(10).join(qa_pairs)}

## Analysis Instructions
For each question, compare the answers on: precision, citation quality, factual accuracy,
and hallucination risk. Then give an overall assessment.

Return JSON:
{{
  "summary": "2-3 sentence executive summary of which approach produces better answers",
  "per_question": [
    {{
      "question_num": 1,
      "winner": "with_kg|without_kg|tie",
      "reason": "Brief explanation"
    }}
  ],
  "scores": {{
    "with_kg_precision": "high/medium/low",
    "with_kg_citations": "high/medium/low",
    "without_kg_precision": "high/medium/low",
    "without_kg_citations": "high/medium/low"
  }},
  "hallucination_risk": {{
    "with_kg": 0-100,
    "without_kg": 0-100,
    "explanation": "Why one approach has higher/lower hallucination risk"
  }},
  "overall_winner": "with_kg|without_kg|tie",
  "overall_reason": "Brief explanation"
}}"""

        result = backend.complete(prompt)
        try:
            analysis = json.loads(result.text)
        except json.JSONDecodeError:
            analysis = {"summary": result.text, "error": "Failed to parse JSON"}

        return {"analysis": analysis, "tokens": result.tokens_used}
    except Exception as e:
        return {
            "analysis": {"summary": f"Quality analysis failed: {e}", "error": str(e)},
            "tokens": 0,
        }


def _build_kg_context_string(kg: dict) -> str:
    """Build a structured text representation of the KG for LLM context.

    Groups entities by type and relationships by predicate for better
    readability and more effective LLM grounding.
    """
    lines = ["# Knowledge Graph"]

    # Group entities by type
    entities_by_type: dict = {}
    for ent in kg.get("entities", []):
        etype = ent.get("entity_type", "UNKNOWN")
        entities_by_type.setdefault(etype, []).append(ent)

    lines.append("\n## Entities")
    for etype in sorted(entities_by_type.keys()):
        ents = sorted(entities_by_type[etype], key=lambda e: e.get("confidence", 0), reverse=True)
        lines.append(f"\n### {etype} ({len(ents)})")
        for ent in ents[:25]:
            lines.append(f"- {ent.get('text', '?')} (conf={ent.get('confidence', 0):.2f})")

    # Group relationships by predicate
    rels_by_pred: dict = {}
    for rel in kg.get("relationships", []):
        pred = rel.get("predicate", "?")
        rels_by_pred.setdefault(pred, []).append(rel)

    lines.append("\n## Relationships")
    for pred in sorted(rels_by_pred.keys()):
        rels = sorted(rels_by_pred[pred], key=lambda r: r.get("confidence", 0), reverse=True)
        lines.append(f"\n### {pred} ({len(rels)})")
        for rel in rels[:15]:
            s = rel.get("subject", {}).get("text", "?")
            o = rel.get("object", {}).get("text", "?")
            c = rel.get("confidence", 0)
            meta = rel.get("metadata", {})
            ev = rel.get("evidence", {})
            sent = ev.get("sentence_text", "")[:200] if isinstance(ev, dict) else ""

            meta_parts = []
            for mk, mv in (meta or {}).items():
                meta_parts.append(f"{mk}={mv}")
            meta_str = f" [{', '.join(meta_parts)}]" if meta_parts else ""

            lines.append(f"- {s} --> {o} (conf={c:.2f}){meta_str}")
            if sent:
                lines.append(f"  Evidence: \"{sent}\"")

    return "\n".join(lines)


# --- Entry Point ---

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    print(f"\n{'=' * 60}")
    print(f"  KGSpin vs Gemini LLM Comparison Demo")
    print(f"  Open http://localhost:{port} in your browser")
    print(f"{'=' * 60}\n")

    # Check env vars
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY")):
        print("  Warning: GEMINI_API_KEY not set. Gemini pipeline will be disabled.")
    if not os.environ.get("EDGAR_IDENTITY"):
        print("  Warning: EDGAR_IDENTITY not set. May not be able to fetch new filings.")
    print()

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", log_config=None)
