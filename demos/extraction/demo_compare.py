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
from typing import AsyncGenerator, List, Optional

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
    resolve_pipeline_config,
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
            ticker="<unknown>",
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
    """
    from types import SimpleNamespace
    identifier = metadata.get("identifier") or {}
    source_extras = metadata.get("source_extras") or {}
    text = raw_bytes.decode("utf-8", errors="ignore")
    return SimpleNamespace(
        raw_html=text,
        company_name=source_extras.get("company_name") or identifier.get("ticker") or ticker,
        cik=source_extras.get("cik", ""),
        accession_number=source_extras.get("accession_number", ""),
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
                meta = resource.metadata or {}
                stored_q = ((meta.get("source_extras") or {}).get("query") or "").lower()
                if stored_q and (stored_q in q_lower or q_lower in stored_q):
                    query_matches.append(resource)
            query_matches.sort(
                key=lambda r: (r.metadata or {}).get("fetch_timestamp", ""),
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


def _try_corpus_fetch(ticker: str):
    """Sprint 10: read the most-recent landed artifact via admin's
    ``ResourceRegistryClient``.

    Returns an EdgarDocument-shaped adapter on hit so the downstream
    extraction pipeline is unchanged. Raises ``CorpusFetchError`` when
    no registered document matches the identifier; SSE handlers catch
    it and surface a "Corpus Missing — click Refresh" hint to the UI.
    """
    is_clinical = ticker.startswith("NCT")
    if is_clinical:
        domain, source, identifier_key = "clinical", "clinicaltrials_gov", "nct"
        normalized_id = ticker
    else:
        domain, source, identifier_key = "financial", "sec_edgar", "ticker"
        normalized_id = ticker.upper()
    attempted = [source]

    client = _get_registry_client()
    candidates = client.list(ResourceKind.CORPUS_DOCUMENT, domain=domain, source=source)
    matches = [
        r for r in candidates
        if (r.metadata or {}).get("identifier", {}).get(identifier_key) == normalized_id
    ]

    if matches:
        matches.sort(
            key=lambda r: (r.metadata or {}).get("fetch_timestamp", ""),
            reverse=True,
        )
        latest = matches[0]
        pointer = client.resolve_pointer(latest.id)
        try:
            raw_bytes = _read_pointer_bytes(pointer)
        except CorpusFetchError as cfe:
            # Re-raise with the correct ticker on the envelope.
            raise CorpusFetchError(
                ticker=ticker,
                reason=cfe.reason,
                actionable_hint=cfe.actionable_hint,
                attempted=attempted + cfe.attempted,
            )
        return _adapt_to_sec_doc_shape(raw_bytes, latest.metadata or {}, ticker)

    # Last-resort fixture fallback for offline CI (tests/fixtures/corpus/<ticker>.html).
    # Preserved per Sprint 10 plan; the legacy ``_corpus_create_provider`` factory
    # was retired in Sprint 09 so we instantiate ``MockDocumentFetcher`` directly.
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
        # Fixture miss is expected in most runs; fall through to actionable error.
        pass

    if is_clinical:
        hint = (
            f"No landed artifact for {ticker}. Run:\n"
            f"  uv run kgspin-demo-lander-clinical --nct {ticker}\n"
            f"...then re-run extraction. Or click 'Refresh Local Corpus' in the UI."
        )
    else:
        hint = (
            f"No landed artifact for {ticker}. Run:\n"
            f"  uv run kgspin-demo-lander-sec --ticker {ticker}\n"
            f"...then re-run extraction. Or click 'Refresh Local Corpus' in the UI. "
            f"(SEC_USER_AGENT env var required for the lander.)"
        )
    raise CorpusFetchError(
        ticker=ticker,
        reason="no landed artifact",
        actionable_hint=hint,
        attempted=attempted,
    )


# Keep the old name as an alias so any stale callers don't break.
_try_mock_fetch = _try_corpus_fetch

# Sprint 33.3: Cache invalidation — bump when UI/schema changes break compatibility.
# Changing this constant auto-invalidates all existing cached run logs.
DEMO_CACHE_VERSION = "4.0.0"  # 4.0.0: Sprint 118 pipeline/domain split

# Sprint 33.3: Byte-based corpus sizing — replaces MAX_DEMO_CHUNKS
VALID_CORPUS_KB = {0, 100, 200, 500}
DEFAULT_CORPUS_KB = 0  # Sprint 80: Full document is default (CEO directive)

# Sprint 141: Chunk size is now max chars per chunk (size-based, not count-based).
# Default 30K chars (~8K tokens). Legacy count values (1, 6, 12, 24) mapped to 30K.
VALID_CHUNK_SIZES = {3000, 10000, 30000, 50000, 0, 1, 6, 12, 24}
DEFAULT_CHUNK_SIZE = 30000

# Sprint 33.18: Model selection with pricing (per 1M tokens)
GEMINI_MODEL_PRICING = {
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "label": "2.5 Flash Lite"},
    "gemini-2.5-flash":      {"input": 0.30, "output": 2.50, "label": "2.5 Flash"},
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
_init_lock = threading.Lock()
_bundle_cache: dict[str, object] = {}  # bundle_name -> ExtractionBundle
_CACHED_BUNDLE = None  # kept for purge_cache compatibility
_CACHED_GLINER_BACKEND = None


def _get_bundle(bundle_name: str | None = None, pipeline_id: str | None = None):
    """Load and cache a bundle by name. Defaults to BUNDLE_PATH.

    Split-bundle path (domain + pipeline overlay) is the sole runtime
    load strategy when a ``bundle_name`` is supplied — admin's
    ``bundle_compiled`` registry resolves the directory; ``pipeline_id``
    drives the overlay. The legacy monolithic ``ExtractionBundle.load``
    branch remains as a fallback for the default (no-bundle-name) case,
    which still honors module-level ``BUNDLE_PATH``.
    """
    global _CACHED_BUNDLE

    if bundle_name:
        pid = pipeline_id or "fan-out"
        return _get_split_bundle(bundle_name, pid)

    bundle_path = BUNDLE_PATH
    if bundle_path is None:
        raise FileNotFoundError(
            "No default bundle available: admin has no financial "
            "bundle_compiled registered. Run `kgspin-admin sync "
            "archetypes <blueprint>` to register bundles."
        )
    bundle_name = BUNDLE_PATH.name

    if bundle_name in _bundle_cache:
        return _bundle_cache[bundle_name]

    with _init_lock:
        if bundle_name not in _bundle_cache:
            from kgspin_core.execution.extractor import ExtractionBundle
            _bundle_cache[bundle_name] = ExtractionBundle.load(bundle_path)
        _CACHED_BUNDLE = _bundle_cache[bundle_name]
        return _bundle_cache[bundle_name]


def _get_split_bundle(domain_id: str, pipeline_id: str):
    """Sprint 118: Load and cache a split bundle (domain + pipeline overlay).

    Cache key: '{domain_id}+{pipeline_id}' to distinguish combinations.
    """
    cache_key = f"{domain_id}+{pipeline_id}"
    if cache_key in _bundle_cache:
        return _bundle_cache[cache_key]

    with _init_lock:
        if cache_key not in _bundle_cache:
            from kgspin_core.execution.extractor import ExtractionBundle
            domain_path = resolve_domain_bundle_path(domain_id)
            pipeline_config = resolve_pipeline_config(pipeline_id)
            _bundle_cache[cache_key] = ExtractionBundle.load_split(
                domain_path, pipeline_config,
            )
        return _bundle_cache[cache_key]


def _bundle_id(bundle_name: str | None = None) -> str:
    """Sprint 42.6: Return the bundle directory name — the unique ID.

    Used for cache keys, display, and log metadata. No parsing or stripping.
    Same string that locates the bundle on disk.

    Examples:
        'financial-fast-v1.0.0' -> 'financial-fast-v1.0.0'
        'financial-v1.8.0'      -> 'financial-v1.8.0'
        None                    -> default bundle directory name
    """
    return bundle_name or BUNDLE_PATH.name


def _split_bundle_id(domain_id: str, pipeline_id: str) -> str:
    """Sprint 118: Build cache-safe bundle identifier for split bundles."""
    return f"dom={domain_id}_p={pipeline_id}"


def _get_gliner_backend():
    global _CACHED_GLINER_BACKEND
    if _CACHED_GLINER_BACKEND is None:
        with _init_lock:
            if _CACHED_GLINER_BACKEND is None:
                try:
                    from kgspin_core.agents.backends import create_backend
                    _CACHED_GLINER_BACKEND = create_backend(
                        backend_type="gliner",
                        labels=[
                            "PERSON", "ORGANIZATION", "LOCATION", "PRODUCT",
                            "GENERIC_BUSINESS_CATEGORY", "ABSTRACT_CONCEPT",
                        ],
                        negative_labels={
                            "GENERIC_BUSINESS_CATEGORY", "ABSTRACT_CONCEPT",
                        },
                    )
                except Exception:
                    pass
    return _CACHED_GLINER_BACKEND


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


class GeminiRunLog:
    """Disk-based run log for Gemini KG extraction results.

    Structure: ~/.kgenskills/logs/gemini/{TICKER}/{config_key}@{timestamp}.json
    Each file stores one complete extraction run.
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "gemini"
    MAX_RUNS = 20  # Max logged runs per ticker+config

    def config_key(self, method: str, **kwargs) -> str:
        """Human-readable cache key from method name + args.

        Example: kgen_bv=1.2.3_corpus_kb=200_cv=2.4.0
        """
        parts = [method]
        for k, v in sorted(kwargs.items()):
            parts.append(f"{k}={v}")
        return "_".join(parts)

    def _run_dir(self, ticker: str) -> Path:
        return self.LOG_ROOT / ticker.upper()

    @staticmethod
    def _strip_bv(cfg_key: str) -> str:
        """Remove bv=... and pv=... segments for version-agnostic matching.

        Bundle version and prompt version changes should not invalidate
        cached LLM extraction runs — the results are still valid.
        Sprint 42.6: Also strip bs= and cv= for backward compat with old cached files.
        """
        key = re.sub(r'_?bv=[^_@]*', '', cfg_key)
        key = re.sub(r'_?pv=[^_@]*', '', key)
        key = re.sub(r'_?bs=[^_@]*', '', key)
        key = re.sub(r'_?cv=[^_@]*', '', key)
        return key

    def _run_files(self, ticker: str, cfg_key: str) -> List[Path]:
        """Get all run files for ticker+config, sorted newest first.

        Matches are bv- and pv-agnostic: a query for 'gemini_corpus_kb=200_cv=2.5.0'
        also matches old files with different bv= or pv= segments,
        so that bundle/prompt version changes don't invalidate the disk cache.
        """
        run_dir = self._run_dir(ticker)
        if not run_dir.exists():
            return []
        normalized = self._strip_bv(cfg_key)
        files = [
            f for f in run_dir.glob("*.json")
            if self._strip_bv(f.stem.split("@", 1)[0]) == normalized
        ]
        files.sort(
            key=lambda p: p.stem.split("@", 1)[1] if "@" in p.stem else "",
            reverse=True,
        )
        return files

    def log_run(
        self,
        ticker: str,
        cfg_hash: str,
        kg: dict,
        total_tokens: int,
        elapsed_seconds: float,
        model: str,
        analysis: Optional[dict] = None,
        cache_version: str = "",
        bundle_version: str = "",
    ) -> Path:
        """Log a completed run to disk. Returns file path."""
        run_dir = self._run_dir(ticker)
        run_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"{datetime.now(timezone.utc).microsecond:06d}Z"
        filename = f"{cfg_hash}@{ts}.json"
        filepath = run_dir / filename

        run_data = {
            "ticker": ticker.upper(),
            "config_key": cfg_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "entity_count": len(kg.get("entities", [])),
            "relationship_count": len(kg.get("relationships", [])),
            "kg": kg,
            "analysis": analysis,
            "demo_cache_version": cache_version,
            "bundle_version": bundle_version,
        }

        filepath.write_text(json.dumps(run_data, default=str, indent=2))
        logger.info(f"Logged Gemini run: {filepath}")

        # Cleanup old runs beyond MAX_RUNS
        existing = self._run_files(ticker, cfg_hash)
        if len(existing) > self.MAX_RUNS:
            for old_file in existing[self.MAX_RUNS:]:
                old_file.unlink(missing_ok=True)
                logger.info(f"Cleaned up old run: {old_file}")

        return filepath

    def update_run_analysis(
        self, ticker: str, cfg_hash: str, index: int, analysis: dict
    ) -> None:
        """Update the analysis field of a logged run."""
        files = self._run_files(ticker, cfg_hash)
        if index < 0 or index >= len(files):
            return
        filepath = files[index]
        run_data = json.loads(filepath.read_text())
        run_data["analysis"] = analysis
        filepath.write_text(json.dumps(run_data, default=str, indent=2))

    def list_runs(self, ticker: str, cfg_hash: str) -> List[dict]:
        """List all logged runs for ticker+config, sorted newest first."""
        files = self._run_files(ticker, cfg_hash)
        runs = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                runs.append({
                    "path": str(f),
                    "created_at": data.get("created_at", ""),
                    "model": data.get("model", ""),
                    "total_tokens": data.get("total_tokens", 0),
                    "elapsed_seconds": data.get("elapsed_seconds", 0),
                    "entity_count": data.get("entity_count", 0),
                    "relationship_count": data.get("relationship_count", 0),
                    "has_analysis": data.get("analysis") is not None,
                })
            except Exception:
                continue
        return runs

    def load_run(self, ticker: str, cfg_hash: str, index: int) -> Optional[dict]:
        """Load a specific run by index (0 = newest). Returns full run data."""
        files = self._run_files(ticker, cfg_hash)
        if index < 0 or index >= len(files):
            return None
        try:
            return json.loads(files[index].read_text())
        except Exception:
            return None

    def latest(self, ticker: str, cfg_hash: str) -> Optional[dict]:
        """Load the most recent run. Shortcut for load_run(..., 0)."""
        return self.load_run(ticker, cfg_hash, 0)

    def count(self, ticker: str, cfg_hash: str) -> int:
        """Number of logged runs for this ticker+config."""
        return len(self._run_files(ticker, cfg_hash))


# Singleton run logs
_run_log = GeminiRunLog()


class ModularRunLog(GeminiRunLog):
    """Disk-based run log for LLM Multi-Stage extraction results.

    Separate namespace from Full Shot logs: ~/.kgenskills/logs/modular/{TICKER}/
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "modular"


_modular_run_log = ModularRunLog()


class KGenRunLog(GeminiRunLog):
    """Disk-based run log for KGSpin extraction results.

    Sprint 33.10: Separate namespace so page refreshes don't re-run the 430s
    deterministic pipeline. ~/.kgenskills/logs/kgen/{TICKER}/
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "kgen"

    @staticmethod
    def _strip_bv(cfg_key: str) -> str:
        """Strip bv=, pv=, bs=, cv= for cache matching.

        KGen cache keys by ticker+size only. Old cached files with bv= in
        their filenames still need to match the new key (without bv=).
        """
        key = re.sub(r'_?bv=[^_@]*', '', cfg_key)
        key = re.sub(r'_?pv=[^_@]*', '', key)
        key = re.sub(r'_?bs=[^_@]*', '', key)
        key = re.sub(r'_?cv=[^_@]*', '', key)
        return key


_kgen_run_log = KGenRunLog()


class IntelRunLog(GeminiRunLog):
    """Sprint 33.17: Disk-based run log for Intelligence pipeline results.

    ~/.kgenskills/logs/intel/{TICKER}/
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "intel"


_intel_run_log = IntelRunLog()


class ImpactQARunLog(GeminiRunLog):
    """Disk-based run log for Impact Q&A comparison results.

    ~/.kgenskills/logs/impact_qa/{TICKER}/
    Each file stores one complete Q&A run (all questions + summary + quality analysis).
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "impact_qa"

    def log_qa_run(
        self,
        ticker: str,
        results: list,
        summary: dict,
        quality_analysis: dict | None,
        elapsed_seconds: float,
    ) -> Path:
        """Log a completed Q&A comparison run to disk."""
        ticker = ticker.upper()
        run_dir = self._run_dir(ticker)
        run_dir.mkdir(parents=True, exist_ok=True)

        ts = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + f"{datetime.now(timezone.utc).microsecond:06d}Z"
        )
        cfg_key = "impact_qa"
        filename = f"{cfg_key}@{ts}.json"
        filepath = run_dir / filename

        total_tokens = sum(
            r.get("tokens_with", 0) + r.get("tokens_without", 0) for r in results
        )

        run_data = {
            "ticker": ticker,
            "config_key": cfg_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": "gemini",
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "questions_count": len(results),
            "results": results,
            "summary": summary,
            "quality_analysis": quality_analysis,
        }

        filepath.write_text(json.dumps(run_data, default=str, indent=2))
        logger.info(f"Logged impact Q&A run: {filepath}")

        # Cleanup old runs beyond MAX_RUNS
        existing = self._run_files(ticker, cfg_key)
        if len(existing) > self.MAX_RUNS:
            for old_file in existing[self.MAX_RUNS :]:
                old_file.unlink(missing_ok=True)

        return filepath


_impact_qa_run_log = ImpactQARunLog()


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
) -> dict:
    """Build cache config keys for all three extraction pipelines.

    Returns {"gemini": str, "modular": str, "kgen": str}.

    Sprint 118: All keys include a readable ``dom=`` segment when a domain
    version is selected, so cached runs can be queried by domain version
    across all pipelines (e.g. "all v12 extracts including LLMs").

    Sprint 86: KGSpin cache key includes ``bid`` (bundle_id) so
    different strategy × linguistic combinations produce separate caches.
    """
    gem_pv = _prompt_version_hash(
        "gemini_extractor", "GeminiKGExtractor", bundle_path, patterns_path, model,
    )
    mod_pv = _prompt_version_hash(
        "gemini_aligned_extractor", "GeminiAlignedExtractor", bundle_path, patterns_path, model,
    )
    # Sprint 86: Include bundle_id as opaque discriminator for KGSpin.
    # No parsing of bundle_id is permitted — treat as black box (VP Eng mandate).
    kgen_kwargs = {"corpus_kb": corpus_kb}
    if bundle_name:
        kgen_kwargs["bid"] = bundle_name
    # Sprint 118: Include domain_id as readable segment in LLM cache keys.
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


@app.get("/api/refresh-corpus/sec/{ticker}")
async def refresh_corpus_sec(ticker: str, request: Request, filing: str = "10-K"):
    """Sprint 07 Task 7: trigger the SEC lander from the UI.

    Input is validated against ``_TICKER_RE`` (VP Sec Mandate 7.1) BEFORE
    any subprocess invocation. Invalid tickers return HTTP 400 without
    touching ``subprocess.run``.
    """
    t = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(t):
        return JSONResponse(
            {"error": f"Invalid ticker {ticker!r}. Expected 1-5 ASCII letters."},
            status_code=400,
        )
    if filing not in {"10-K", "10-Q", "8-K"}:
        return JSONResponse(
            {"error": f"Invalid filing {filing!r}."},
            status_code=400,
        )
    return StreamingResponse(
        _run_lander_subprocess(
            "sec", ["--ticker", t, "--filing", filing],
            registry_key=("financial", "sec_edgar", {"ticker": t, "form": filing}),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/refresh-corpus/clinical/{nct}")
async def refresh_corpus_clinical(nct: str, request: Request):
    """Sprint 07 Task 7: trigger the clinical lander from the UI."""
    n = nct.strip().upper()
    if not _NCT_RE.fullmatch(n):
        return JSONResponse(
            {"error": f"Invalid NCT id {nct!r}. Expected NCT followed by 8 digits."},
            status_code=400,
        )
    return StreamingResponse(
        _run_lander_subprocess(
            "clinical", ["--nct", n],
            registry_key=("clinical", "clinicaltrials_gov", {"nct": n}),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/refresh-corpus/yahoo-rss/{ticker}")
async def refresh_corpus_yahoo_rss(ticker: str, request: Request, limit: int = 5):
    """Sprint 11 (ADR-004): trigger the real Yahoo Finance RSS lander."""
    t = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(t):
        return JSONResponse(
            {"error": f"Invalid ticker {ticker!r}."},
            status_code=400,
        )
    if not (1 <= limit <= 20):
        return JSONResponse({"error": f"limit must be 1..20; got {limit}"}, status_code=400)
    return StreamingResponse(
        _run_lander_subprocess(
            "yahoo-rss", ["--ticker", t, "--limit", str(limit)],
            registry_key=("financial", "yahoo_rss", {"ticker": t}),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/refresh-corpus/marketaux/{ticker}")
async def refresh_corpus_marketaux(ticker: str, request: Request, limit: int = 5):
    """Sprint 11 (ADR-004): trigger the Marketaux finance news lander."""
    t = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(t):
        return JSONResponse(
            {"error": f"Invalid ticker {ticker!r}."},
            status_code=400,
        )
    if not (1 <= limit <= 20):
        return JSONResponse({"error": f"limit must be 1..20; got {limit}"}, status_code=400)
    return StreamingResponse(
        _run_lander_subprocess(
            "marketaux", ["--ticker", t, "--limit", str(limit)],
            registry_key=("financial", "marketaux", {"ticker": t}),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _derive_clinical_query_from_nct(nct: str, default: str = "") -> str:
    """Sprint 11 VP Prod requirement — now a thin wrapper.

    Sprint 12 Task 10.1 extracted the actual derivation logic to
    ``kgspin_demo_app.services.clinical_query`` per VP Eng Phase 3
    "God Method" concern. This wrapper preserves the legacy call shape
    (uses the module-level registry client) so in-file call sites
    don't need to change.
    """
    from kgspin_demo_app.services import derive_clinical_query_from_nct as _impl
    return _impl(_get_registry_client(), nct, default=default)


@app.get("/api/refresh-corpus/newsapi")
async def refresh_corpus_newsapi(
    request: Request,
    query: str = "",
    domain: str = "clinical",
    limit: int = 5,
    nct: str = "",
):
    """Sprint 11 (ADR-004): trigger the domain-agnostic NewsAPI lander.

    ``--domain`` selects which domain the landed ``corpus_document``
    records are tagged with. Default ``clinical`` reflects the Sprint 11
    VP Prod pattern for NCT-trial-driven news; pass ``domain=financial``
    to land news for a ticker-driven query instead.

    VP Prod (Sprint 11 plan Task 6): if ``nct`` is supplied and ``query``
    is empty, derive the query from the NCT trial's ``condition`` +
    interventions. Gracefully degrades to requiring the operator to
    type ``query`` if metadata derivation yields nothing.
    """
    if domain not in DOMAIN_FETCHERS or "newsapi" not in DOMAIN_FETCHERS.get(domain, ()):
        return JSONResponse(
            {"error": f"NewsAPI lander not configured for domain {domain!r}."},
            status_code=400,
        )

    q = (query or "").strip()
    if not q and nct:
        nct_norm = nct.strip().upper()
        if _NCT_RE.fullmatch(nct_norm):
            q = _derive_clinical_query_from_nct(nct_norm)

    if not q or not _NEWS_QUERY_RE.fullmatch(q):
        return JSONResponse(
            {
                "error": (
                    f"Invalid or missing query {query!r}. Expected 1-100 chars, "
                    "alphanumerics / spaces / _- only. For NCT-trial-driven news, "
                    "pass nct=<NCT########> and trial metadata will seed the query, "
                    "or type the query yourself."
                )
            },
            status_code=400,
        )
    if not (1 <= limit <= 20):
        return JSONResponse({"error": f"limit must be 1..20; got {limit}"}, status_code=400)

    return StreamingResponse(
        _run_lander_subprocess(
            "newsapi",
            ["--query", q, "--domain", domain, "--limit", str(limit)],
            registry_key=(domain, "newsapi", {"query": q}),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Sprint 11 Task 6: composite "Refresh All <Domain> News" (VP Prod primary button) ---


@app.get("/api/refresh-corpus/news/{domain}")
async def refresh_all_domain_news(
    domain: str,
    request: Request,
    ticker: str = "",
    query: str = "",
    nct: str = "",
    limit: int = 5,
):
    """VP Prod primary button: fire every news lander for ``domain`` in parallel.

    Routes:
    - ``domain=financial``, ``ticker=T`` → marketaux + yahoo-rss (ticker-scoped)
      plus newsapi (term-scoped; uses ticker as query).
    - ``domain=clinical``, ``nct=NCT########`` → newsapi (query auto-derived
      from trial condition + interventions, per VP Prod comment).

    Returns an SSE stream that interleaves progress from all concurrent
    lander subprocesses. Per-backend drill-down endpoints remain
    available (``/yahoo-rss``, ``/marketaux``, ``/newsapi``) for the
    advanced UI toggle.
    """
    if domain not in DOMAIN_FETCHERS:
        return JSONResponse(
            {"error": f"Unknown domain {domain!r}."},
            status_code=400,
        )
    news_sources = _NEWS_SOURCES_BY_DOMAIN.get(domain, ())
    if not news_sources:
        return JSONResponse(
            {"error": f"No news sources configured for domain {domain!r}."},
            status_code=400,
        )
    if not (1 <= limit <= 20):
        return JSONResponse({"error": f"limit must be 1..20; got {limit}"}, status_code=400)

    plans: list[tuple[str, list[str], tuple[str, str, dict[str, str]]]] = []

    if domain == "financial":
        t = ticker.strip().upper()
        if not _TICKER_RE.fullmatch(t):
            return JSONResponse(
                {"error": f"domain=financial requires a ticker; got {ticker!r}."},
                status_code=400,
            )
        if "marketaux" in news_sources:
            plans.append(("marketaux",
                          ["--ticker", t, "--limit", str(limit)],
                          ("financial", "marketaux", {"ticker": t})))
        if "yahoo_rss" in news_sources:
            plans.append(("yahoo-rss",
                          ["--ticker", t, "--limit", str(limit)],
                          ("financial", "yahoo_rss", {"ticker": t})))
        if "newsapi" in news_sources:
            plans.append(("newsapi",
                          ["--query", t, "--domain", "financial", "--limit", str(limit)],
                          ("financial", "newsapi", {"query": t})))

    elif domain == "clinical":
        n = nct.strip().upper()
        if not _NCT_RE.fullmatch(n):
            return JSONResponse(
                {"error": f"domain=clinical requires nct=<NCT########>; got {nct!r}."},
                status_code=400,
            )
        # Derive query from trial metadata. If nothing derives, fall
        # back to the NCT id so the operator still gets a result set
        # (can refine via the drill-down /newsapi endpoint with a
        # hand-typed query).
        q = _derive_clinical_query_from_nct(n, default=n)
        if "newsapi" in news_sources:
            plans.append(("newsapi",
                          ["--query", q, "--domain", "clinical", "--limit", str(limit)],
                          ("clinical", "newsapi", {"query": q})))

    if not plans:
        return JSONResponse(
            {"error": f"No runnable lander plans for domain={domain!r}."},
            status_code=400,
        )

    async def _fan_out():
        """Fire every plan concurrently; interleave their SSE events."""
        import asyncio as _asyncio
        queue: _asyncio.Queue[tuple[str, str]] = _asyncio.Queue()
        DONE_SENTINEL = ("__done__", "")

        async def _drive(kind: str, args: list[str], rk: tuple[str, str, dict[str, str]]):
            async for ev in _run_lander_subprocess(kind, args, registry_key=rk):
                await queue.put((kind, ev))
            await queue.put(DONE_SENTINEL)

        tasks = [
            _asyncio.create_task(_drive(kind, args, rk))
            for (kind, args, rk) in plans
        ]
        remaining = len(tasks)
        try:
            while remaining > 0:
                item = await queue.get()
                if item == DONE_SENTINEL:
                    remaining -= 1
                    continue
                _, ev = item
                yield ev
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    return StreamingResponse(
        _fan_out(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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
    """Return the target entity types, hierarchy, and relationship types from the patterns YAML."""
    import yaml as _yaml
    try:
        patterns_path = PATTERNS_PATH
        if bundle:
            # Derive patterns path from bundle name
            candidate = Path("bundles") / f"{bundle.replace('bundles/', '')}.yaml"
            if candidate.exists():
                patterns_path = candidate
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


@app.get("/api/compare-clinical/{nct_id}")
async def compare_clinical(
    nct_id: str, request: Request, bundle: str = "",
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
            nct_id, request, bundle_name=bundle_name,
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


_STRATEGY_TO_PIPELINE_ID_LEGACY = {
    "fan_out": "fan-out",
    "discovery_rapid": "discovery-rapid",
    "discovery_deep": "discovery-deep",
}

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


def _resolve_pipeline_config_ref(pipeline_config_ref: str) -> str | None:
    """Sprint 12 Task 5: resolve an admin resource id → strategy name.

    Returns the ``PipelineConfig.name`` (e.g. ``"agentic_flash"``) on
    success, or ``None`` if the ref is empty / admin can't resolve it.
    Caller combines this with the legacy ``strategy`` query param —
    ``pipeline_config_ref`` takes precedence when both are supplied.

    Contract note (VP Eng Mock vs. Core gate): demo currently consumes
    the resolved string via the legacy ``_STRATEGY_TO_PIPELINE_ID_LEGACY``
    mapping. When core Sprint 18 T2 lands, demo will pass the full
    resource id (or ``PipelineConfig`` object) straight through to
    core's dispatcher — one-line pydantic field change expected, no
    rewire of the surrounding endpoint surface.
    """
    ref = (pipeline_config_ref or "").strip()
    if not ref:
        return None
    try:
        client = _get_registry_client()
        resource = client.get(ref)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[PIPELINE_CONFIG_REF] admin lookup failed for %r: %s: %s",
            ref, type(e).__name__, str(e)[:100],
        )
        return None
    if resource is None:
        logger.info("[PIPELINE_CONFIG_REF] admin returned None for %r", ref)
        return None
    return ((resource.metadata or {}).get("name")) or None


def _pipeline_id_from_compare_args(strategy: str, pipeline_config_ref: str) -> str | None:
    """Pick the pipeline_id from either a ref or a legacy strategy arg.

    Precedence: ``pipeline_config_ref`` > ``strategy`` > ``None``.
    The legacy-to-pipeline-id map stays demo-side until core Sprint 18
    T2 lands the canonical ref-based dispatcher.
    """
    resolved_strategy = _resolve_pipeline_config_ref(pipeline_config_ref) or strategy
    return _STRATEGY_TO_PIPELINE_ID_LEGACY.get(resolved_strategy) if resolved_strategy else None


@app.get("/api/compare/{ticker}")
async def compare(ticker: str, request: Request, force_refresh: int = 0, corpus_kb: int = DEFAULT_CORPUS_KB, chunk_size: int = DEFAULT_CHUNK_SIZE, model: str = DEFAULT_GEMINI_MODEL, bundle: str = "", strategy: str = "", pipeline_config_ref: str = "", confidence_floor: float = -1.0, llm_alias: str = ""):
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
    # Sprint 12 Task 5: prefer pipeline_config_ref (admin resource id);
    # fall back to legacy strategy string.
    pipeline_id = _pipeline_id_from_compare_args(strategy, pipeline_config_ref)
    # Sprint 12 Task 8: confidence_floor precedence — query arg >
    # admin pipeline param > hardcoded fallback (0.55). Query arg of
    # -1.0 means "not supplied by caller" (0.55 is a valid operator
    # choice so we can't use it as the sentinel). Admin lookup uses
    # the resolved pipeline name; graceful-degrade to 0.55 on miss.
    confidence_floor = _resolve_confidence_floor(
        query_value=confidence_floor,
        pipeline_name=_resolve_pipeline_config_ref(pipeline_config_ref) or strategy,
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


@app.get("/api/refresh-agentic-flash/{ticker}")
async def refresh_agentic_flash(ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, model: str = DEFAULT_GEMINI_MODEL, bundle: str = "", llm_alias: str = ""):
    """INIT-001 Sprint 04: Re-run only Agentic Flash (single-prompt LLM) for this ticker.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias. ``model`` is retained as deprecated compat; passing both is 400.
    """
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


@app.get("/api/refresh-agentic-analyst/{ticker}")
async def refresh_agentic_analyst(ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, chunk_size: int = DEFAULT_CHUNK_SIZE, model: str = DEFAULT_GEMINI_MODEL, bundle: str = "", llm_alias: str = ""):
    """INIT-001 Sprint 04: Re-run only Agentic Analyst (schema-aware chunked LLM) for this ticker.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias. ``model`` is retained as deprecated compat; passing both is 400.
    """
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


@app.get("/api/refresh-discovery/{ticker}")
async def refresh_discovery(ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, bundle: str = "", strategy: str = "", pipeline_config_ref: str = ""):
    """Re-run a zero-token KGSpin strategy (fan_out / discovery_rapid / discovery_deep).

    Sprint 12 Task 5: accepts ``pipeline_config_ref`` (admin resource
    id) alongside the legacy ``strategy`` arg. Precedence: ref >
    strategy > None.
    """
    if corpus_kb not in VALID_CORPUS_KB:
        corpus_kb = DEFAULT_CORPUS_KB
    bundle_name = bundle if bundle else None
    pipeline_id = _pipeline_id_from_compare_args(strategy, pipeline_config_ref)
    return StreamingResponse(
        _run_kgen_refresh(ticker.upper(), request, corpus_kb, bundle_name=bundle_name, pipeline_id=pipeline_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/cancel-multistage/{ticker}")
async def cancel_multistage(ticker: str):
    """Sprint 33.6: Cancel a running Multi-Stage extraction and show partial results."""
    event = _modular_cancel_events.get(ticker.upper())
    if event:
        event.set()
        return JSONResponse({"status": "cancelled"})
    return JSONResponse({"status": "not_running"})


@app.get("/api/scores/{ticker}")
async def get_scores(ticker: str):
    """PRD-048: Lightweight endpoint to recompute Performance Delta scores from cached KGs."""
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


@app.post("/api/refresh-analysis/{ticker}")
async def refresh_analysis(ticker: str, request: Request):
    """Sprint 33.11: Re-run quality analysis using whatever KGs are currently cached.

    Stage 0.5.4 (ADR-002): accepts optional ``llm_alias`` / ``model`` in
    the JSON body (backwards compatible — an empty body still works).
    """
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


@app.post("/api/compare-qa/{ticker}")
async def compare_qa(ticker: str, request: Request):
    """Sprint 91: Compare Q&A across slot-loaded graphs.

    Request body: {graphs: [{pipeline, bundle, slot_index}], domain: str,
                   llm_alias?: str, model?: str}
    Response: {results: [{question, answers: [{answer, tokens}]}], analysis: str}

    Stage 0.5.4 (ADR-002): body accepts ``llm_alias`` (preferred) or the
    deprecated ``model`` string. Passing both is a 400 error.
    """
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


@app.get("/api/intelligence/{ticker}")
async def intelligence(ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, model: str = DEFAULT_GEMINI_MODEL, domain: str = "financial", llm_alias: str = ""):
    """Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered
    alias; ``model`` stays as deprecated compat. Passing both returns 400.
    """
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


@app.get("/api/refresh-intel/{ticker}")
async def refresh_intel(ticker: str, request: Request, corpus_kb: int = DEFAULT_CORPUS_KB, model: str = DEFAULT_GEMINI_MODEL, llm_alias: str = ""):
    """Sprint 33.17: Re-run Intelligence pipeline for this ticker.

    Stage 0.5.4 (ADR-002): ``llm_alias`` selects an admin-registered LLM
    alias; ``model`` retained as deprecated compat. Passing both is 400.
    """
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


@app.get("/api/impact/{ticker}")
async def impact(
    ticker: str, request: Request,
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


@app.get("/api/why-this-matters/{ticker}")
async def why_this_matters(
    ticker: str,
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
        return {"error": "No cached data for ticker. Load a graph first.", "ticker": ticker}

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
        return {"error": "No graph available yet. Run at least one pipeline first.", "ticker": ticker}

    # Check Gemini availability
    gemini_available = bool(
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY")
    )
    if not gemini_available:
        return {"error": "GEMINI_API_KEY not set.", "ticker": ticker}

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


@app.get("/api/impact/lineage/{ticker}")
async def lineage_data(ticker: str, domain: str = "financial", pipeline: str = "kgenskills"):
    """Return KG vis data + source text + full evidence index for lineage exploration.

    Sprint 05 HITL-round-2 fix: accept a ``pipeline`` query param so the
    full-screen modal can show lineage for the slot's ACTUAL pipeline
    (kgenskills / gemini / modular), not the hardcoded KGSpin graph.
    Defaults to ``kgenskills`` to preserve the Impact tab's old behavior.
    """
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


@app.get("/api/impact/reproducibility/{ticker}")
async def reproducibility_benchmark(
    ticker: str, corpus_kb: int = DEFAULT_CORPUS_KB,
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


def _sort_run_files_by_timestamp(files):
    """Sort run log files by timestamp (newest first), not by full filename.

    Filenames are {config_key}@{timestamp}.json. Sorting by full filename
    breaks when config keys differ (e.g., corpus_kb=500 sorts before
    corpus_kb=200 lexicographically, regardless of actual timestamps).
    """
    return sorted(
        files,
        key=lambda p: p.stem.split("@", 1)[1] if "@" in p.stem else "",
        reverse=True,
    )


def _latest_config_key(runs_by_key: dict) -> str:
    """Pick the config key group with the most recent timestamp."""
    return max(
        runs_by_key,
        key=lambda h: max(
            (f.stem.split("@", 1)[1] for f in runs_by_key[h] if "@" in f.stem),
            default="",
        ),
    )


@app.get("/api/gemini-runs/{ticker}")
async def gemini_runs(ticker: str):
    """List all logged Gemini runs for a ticker (across all config hashes)."""
    ticker = ticker.upper()
    run_dir = _run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    # Find all config keys present
    all_files = list(run_dir.glob("*.json"))
    # Group by config key (everything before @ in filename)
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = parts[0]
            if cfg not in runs_by_key:
                runs_by_key[cfg] = []
            runs_by_key[cfg].append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    # Pick config key group with most recent timestamp
    latest_key = _latest_config_key(runs_by_key)
    runs = _run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@app.get("/api/gemini-runs/{ticker}/{index}")
async def gemini_run_detail(ticker: str, index: int):
    """Load a specific Gemini run by index. Includes pre-built vis data."""
    ticker = ticker.upper()

    # Find the config hash from the most recent runs
    run_dir = _run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    # Determine config key from most recent file by timestamp
    cfg_key = all_files[0].stem.split("@", 1)[0]
    run_data = _run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = build_vis_data(kg)
    total = _run_log.count(ticker, cfg_key)

    # Sprint 33.5: Enrich stats with errors and throughput for audit table sync
    elapsed_s = run_data.get("elapsed_seconds", 0)
    provenance = kg.get("provenance", {})
    error_count = provenance.get("error_count", 0)
    # Estimate text size from provenance or fallback
    text_kb = provenance.get("corpus_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None

    # PRD-048: Sync cache so /api/scores reflects the viewed run
    with _cache_lock:
        if ticker in _kg_cache:
            _kg_cache[ticker]["gem_kg"] = kg

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": run_data.get("entity_count", 0),
            "relationships": run_data.get("relationship_count", 0),
            "tokens": run_data.get("total_tokens", 0),
            "duration_ms": int(elapsed_s * 1000),
            "errors": error_count,
            "throughput_kb_sec": round(throughput, 1) if throughput else None,
            "actual_kb": round(text_kb, 1) if text_kb else None,
        },
        "analysis": run_data.get("analysis"),
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", ""),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
    })


@app.get("/api/modular-runs/{ticker}")
async def modular_runs(ticker: str):
    """List all logged LLM Multi-Stage runs for a ticker."""
    ticker = ticker.upper()
    run_dir = _modular_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = parts[0]
            if cfg not in runs_by_key:
                runs_by_key[cfg] = []
            runs_by_key[cfg].append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _modular_run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@app.get("/api/modular-runs/{ticker}/{index}")
async def modular_run_detail(ticker: str, index: int):
    """Load a specific LLM Multi-Stage run by index. Includes pre-built vis data."""
    ticker = ticker.upper()

    run_dir = _modular_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    cfg_key = all_files[0].stem.split("@", 1)[0]
    run_data = _modular_run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = build_vis_data(kg)
    total = _modular_run_log.count(ticker, cfg_key)

    # Sprint 33.5: Enrich stats with errors and throughput for audit table sync
    elapsed_s = run_data.get("elapsed_seconds", 0)
    provenance = kg.get("provenance", {})
    error_count = provenance.get("error_count", 0)
    text_kb = provenance.get("corpus_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None

    # PRD-048: Sync cache so /api/scores reflects the viewed run
    with _cache_lock:
        if ticker in _kg_cache:
            _kg_cache[ticker]["mod_kg"] = kg

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": run_data.get("entity_count", 0),
            "relationships": run_data.get("relationship_count", 0),
            "tokens": run_data.get("total_tokens", 0),
            "duration_ms": int(elapsed_s * 1000),
            "errors": error_count,
            "throughput_kb_sec": round(throughput, 1) if throughput else None,
            "actual_kb": round(text_kb, 1) if text_kb else None,
            "chunks_total": run_data.get("chunks_total", 0),
        },
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", ""),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
    })


# --- KGSpin Run History (Sprint 33.17) ---


@app.get("/api/kgen-runs/{ticker}")
async def kgen_runs(ticker: str):
    """List all logged KGSpin runs for a ticker."""
    ticker = ticker.upper()
    run_dir = _kgen_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    # Group by bv-agnostic key so old (bv=v1.0.0) and new files merge
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = GeminiRunLog._strip_bv(parts[0])
            if cfg not in runs_by_key:
                runs_by_key[cfg] = []
            runs_by_key[cfg].append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _kgen_run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@app.get("/api/kgen-runs/{ticker}/{index}")
async def kgen_run_detail(ticker: str, index: int):
    """Load a specific KGSpin run by index. Includes pre-built vis data."""
    ticker = ticker.upper()

    run_dir = _kgen_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    # Use bv-agnostic key so old and new runs are in the same group
    cfg_key = GeminiRunLog._strip_bv(all_files[0].stem.split("@", 1)[0])
    run_data = _kgen_run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = build_vis_data(kg)
    total = _kgen_run_log.count(ticker, cfg_key)

    elapsed_s = run_data.get("elapsed_seconds", 0)
    cpu_cost = (elapsed_s / 3600) * _CPU_COST_PER_HOUR
    text_kb = kg.get("provenance", {}).get("corpus_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None
    est_chunks = max(1, round(text_kb * 1024 / DEFAULT_CHUNK_SIZE)) if text_kb > 0 else 0

    # PRD-048: Sync cache so /api/scores reflects the viewed run
    with _cache_lock:
        if ticker in _kg_cache:
            _kg_cache[ticker]["kgs_kg"] = kg

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": len(vis["nodes"]),
            "relationships": len(vis["edges"]),
            "tokens": 0,
            "duration_ms": int(elapsed_s * 1000),
            "throughput_kb_sec": round(throughput, 1) if throughput else None,
            "actual_kb": round(text_kb, 1) if text_kb else None,
            "cpu_cost": round(cpu_cost, 6),
            "num_chunks": est_chunks,
        },
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", "kgen_deterministic"),
        "bundle_version": run_data.get("bundle_version", kg.get("provenance", {}).get("bundle_version", "1.0")),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
    })


# --- Slot Cache Check (Sprint 91) ---


@app.get("/api/slot-cache-check/{ticker}")
async def slot_cache_check(ticker: str, pipeline: str = "", bundle: str = "", strategy: str = "", pipeline_config_ref: str = ""):
    """Check if a cached run exists for a pipeline+bundle combo and return it.

    Sprint 12 Task 5: accepts ``pipeline_config_ref`` (admin resource
    id) alongside the legacy ``strategy`` arg.

    Returns {cached: true, vis, stats, total_runs, run_index} or {cached: false}.
    """
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

    # Sprint 12 Task 5: pipeline_config_ref > strategy > None.
    pipeline_id = _pipeline_id_from_compare_args(strategy, pipeline_config_ref)

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
        _patterns = Path(__file__).resolve().parent.parent.parent / "bundles" / "legacy" / "clinical.yaml"
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


@app.get("/api/intel-runs/{ticker}")
async def intel_runs(ticker: str):
    """List all logged Intelligence pipeline runs for a ticker."""
    ticker = ticker.upper()
    run_dir = _intel_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = parts[0]
            if cfg not in runs_by_key:
                runs_by_key[cfg] = []
            runs_by_key[cfg].append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _intel_run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@app.get("/api/intel-runs/{ticker}/{index}")
async def intel_run_detail(ticker: str, index: int):
    """Load a specific Intelligence pipeline run by index."""
    ticker = ticker.upper()

    run_dir = _intel_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    cfg_key = all_files[0].stem.split("@", 1)[0]
    run_data = _intel_run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = build_vis_data(kg)
    total = _intel_run_log.count(ticker, cfg_key)

    elapsed_s = run_data.get("elapsed_seconds", 0)

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": len(vis["nodes"]),
            "relationships": len(vis["edges"]),
            "tokens": 0,
            "duration_ms": int(elapsed_s * 1000),
        },
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", "kgen_deterministic"),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
    })


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
            global _CACHED_BUNDLE, _bundle_predicates_cache
            _kg_cache.clear()
            _CACHED_BUNDLE = None
            _bundle_cache.clear()
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


@app.get("/api/impact-qa-runs/{ticker}")
async def impact_qa_runs(ticker: str):
    """List all logged Impact Q&A runs for a ticker."""
    ticker = ticker.upper()
    cfg_key = "impact_qa"
    runs = _impact_qa_run_log.list_runs(ticker, cfg_key)
    return JSONResponse({"runs": runs, "total": len(runs), "config_key": cfg_key})


@app.get("/api/impact-qa-runs/{ticker}/{index}")
async def impact_qa_run_detail(ticker: str, index: int):
    """Load a specific Impact Q&A run by index (0 = newest)."""
    ticker = ticker.upper()
    cfg_key = "impact_qa"
    run_data = _impact_qa_run_log.load_run(ticker, cfg_key, index)
    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)
    total = _impact_qa_run_log.count(ticker, cfg_key)
    return JSONResponse({
        "results": run_data.get("results", []),
        "summary": run_data.get("summary", {}),
        "quality_analysis": run_data.get("quality_analysis"),
        "created_at": run_data.get("created_at", ""),
        "elapsed_seconds": run_data.get("elapsed_seconds", 0),
        "total_tokens": run_data.get("total_tokens", 0),
        "run_index": index,
        "total_runs": total,
    })


# --- HITL Feedback System (Sprint 39, PRD-042) ---

_feedback_store = None


def _get_feedback_store():
    global _feedback_store
    if _feedback_store is None:
        from kgenskills.feedback.store import create_feedback_store
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


@app.get("/api/document/text/{ticker}")
async def get_document_text(ticker: str):
    """Return cached truncated 10-K text for evidence review (Sprint 39.3)."""
    ticker = ticker.upper()
    with _cache_lock:
        cached = _kg_cache.get(ticker)
    if not cached or not cached.get("text"):
        return JSONResponse({"error": "No cached text. Run extraction first."}, status_code=404)
    text = cached["text"]
    return JSONResponse({"ticker": ticker, "text": text, "length": len(text)})


@app.post("/api/feedback/false_positive")
async def submit_false_positive(request: Request):
    """Store a user-flagged False Positive from the KGS graph."""
    from kgenskills.feedback.models import FalsePositiveFeedback

    body = await request.json()
    bundle = _get_bundle()
    fp = FalsePositiveFeedback(
        bundle_version=getattr(bundle, "version", "unknown"),
        document_id=body.get("document_id", ""),
        pipeline=body.get("pipeline", "kgenskills"),
        feedback_target=body.get("feedback_target", "relationship"),
        subject_text=body.get("subject_text", ""),
        subject_type=body.get("subject_type", ""),
        predicate=body.get("predicate", ""),
        object_text=body.get("object_text", ""),
        object_type=body.get("object_type", ""),
        confidence=float(body.get("confidence", 0)),
        evidence_sentence=body.get("evidence_sentence", ""),
        source_document=body.get("source_document", ""),
        chunk_id=body.get("chunk_id", ""),
        extraction_method=body.get("extraction_method", ""),
        reasons=body.get("reasons", []),
        reason_detail=body.get("reason_detail", ""),
        corrected_type=body.get("corrected_type", ""),
        resolve_to_entity=body.get("resolve_to_entity", ""),
        flagged_by=body.get("flagged_by", "user"),
    )
    store = _get_feedback_store()
    feedback_id = store.add_false_positive(fp)
    return JSONResponse({"id": feedback_id, "status": "stored"})


@app.post("/api/feedback/false_negative")
async def submit_false_negative(request: Request):
    """Store a user-validated False Negative from the LLM graph.

    Validates predicate against bundle schema (VP Eng guardrail #2).
    """
    from kgenskills.feedback.models import FalseNegativeFeedback

    body = await request.json()
    feedback_target = body.get("feedback_target", "relationship")
    predicate = body.get("predicate", "")
    evidence_sentence = body.get("evidence_sentence", "")

    # Sprint 39.3: Entity-level FN skips predicate/evidence validation
    if feedback_target == "relationship":
        # Strict predicate validation against bundle schema (VP Eng guardrail #2)
        valid_predicates = {p["name"] for p in _get_bundle_predicates()}
        if predicate not in valid_predicates:
            return JSONResponse(
                {
                    "error": f"Invalid predicate '{predicate}'. Must be one of: {sorted(valid_predicates)}",
                    "valid_predicates": sorted(valid_predicates),
                },
                status_code=400,
            )

        # Zero Trust evidence validation (VP Eng guardrail #4)
        if len(evidence_sentence) < 10:
            return JSONResponse(
                {"error": "evidence_sentence must be at least 10 characters"},
                status_code=400,
            )
        if len(evidence_sentence) > 1000:
            return JSONResponse(
                {"error": "evidence_sentence must be at most 1000 characters"},
                status_code=400,
            )

    bundle = _get_bundle()
    fn = FalseNegativeFeedback(
        bundle_version=getattr(bundle, "version", "unknown"),
        document_id=body.get("document_id", ""),
        pipeline=body.get("pipeline", ""),
        feedback_target=feedback_target,
        subject_text=body.get("subject_text", ""),
        subject_type=body.get("subject_type", ""),
        predicate=predicate,
        object_text=body.get("object_text", ""),
        object_type=body.get("object_type", ""),
        evidence_sentence=evidence_sentence,
        source_document=body.get("source_document", ""),
        original_confidence=float(body.get("original_confidence", 0)),
        original_evidence=body.get("original_evidence", ""),
    )
    store = _get_feedback_store()
    feedback_id = store.add_false_negative(fn)
    return JSONResponse({"id": feedback_id, "status": "stored"})


@app.post("/api/feedback/true_positive")
async def submit_true_positive(request: Request):
    """Store a user-confirmed True Positive from any graph panel (Sprint 90)."""
    from kgenskills.feedback.models import FalsePositiveFeedback

    body = await request.json()
    bundle = _get_bundle()
    fp = FalsePositiveFeedback(
        bundle_version=getattr(bundle, "version", "unknown"),
        document_id=body.get("document_id", ""),
        pipeline=body.get("pipeline", "kgenskills"),
        feedback_target="entity_tp",
        subject_text=body.get("subject_text", ""),
        subject_type=body.get("subject_type", ""),
        predicate="",
        object_text="",
        object_type="",
        confidence=float(body.get("confidence", 0)),
        evidence_sentence="",
        source_document="",
        chunk_id="",
        extraction_method="",
        reasons=["confirmed_tp"],
        reason_detail="",
        corrected_type="",
        flagged_by="user",
    )
    store = _get_feedback_store()
    feedback_id = store.add_false_positive(fp)
    return JSONResponse({"id": feedback_id, "status": "stored"})


@app.post("/api/feedback/retract")
async def retract_feedback(request: Request):
    """Soft-delete a feedback entry."""
    body = await request.json()
    feedback_id = body.get("feedback_id", "")
    if not feedback_id:
        return JSONResponse({"error": "feedback_id required"}, status_code=400)

    store = _get_feedback_store()
    retracted = store.retract(feedback_id)
    if retracted:
        return JSONResponse({"status": "retracted"})
    return JSONResponse({"status": "not_found"}, status_code=404)


@app.post("/api/feedback/bulk_retract")
async def bulk_retract_feedback(request: Request):
    """Bulk soft-delete feedback entries matching filters.

    Body params (all optional, acts as AND filter):
      - document_id: retract only for this document
      - feedback_type: "fp", "fn", or "tp" (default: all)
      - reason: retract only entries containing this reason code
    """
    body = await request.json()
    document_id = body.get("document_id")
    feedback_type = body.get("feedback_type")
    reason = body.get("reason")

    store = _get_feedback_store()
    count = store.bulk_retract(
        document_id=document_id,
        feedback_type=feedback_type,
        reason=reason,
    )
    logger.info(
        f"Bulk retract: {count} entries retracted "
        f"(document_id={document_id}, type={feedback_type}, reason={reason})"
    )
    return JSONResponse({"status": "ok", "retracted_count": count})


@app.get("/api/feedback/list")
async def list_feedback(request: Request):
    """Return all active (non-retracted) FP and FN entries from the feedback store.

    Sprint 48: Enables the Flag Explorer to show persisted feedback on app load,
    not just session-generated flags.
    """
    store = _get_feedback_store()
    document_id = request.query_params.get("document_id")
    fps = store.get_false_positives(document_id=document_id)
    fns = store.get_false_negatives(document_id=document_id)
    return JSONResponse({
        "false_positives": [fp.to_dict() for fp in fps],
        "false_negatives": [fn.to_dict() for fn in fns],
    })


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
        "ticker": ticker,
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


def sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


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


def _merge_kgs(base_kg: dict, overlay_kg: dict) -> dict:
    """Merge two KG dicts, deduplicating entities by normalized_text+type.

    Sprint 33.13: First-wins dedup — base (SEC) entities take priority over
    overlay (news). Relationships deduped by (subject, predicate, object) triple.
    Sprint 33.15: Suffix-stripped normalization for identity resolution.
    """
    from kgspin_demo_app.services.entity_resolution import normalize_entity_text

    merged: dict = {"entities": [], "relationships": []}

    seen_entities: set = set()
    for ent in base_kg.get("entities", []) + overlay_kg.get("entities", []):
        key = (
            ent.get("entity_type", ""),
            normalize_entity_text(ent.get("text", "")),
        )
        if key not in seen_entities:
            seen_entities.add(key)
            merged["entities"].append(ent)

    seen_rels: set = set()
    for rel in base_kg.get("relationships", []) + overlay_kg.get("relationships", []):
        subj = rel.get("subject", {})
        obj = rel.get("object", {})
        key = (
            normalize_entity_text(subj.get("text", "")),
            rel.get("predicate", ""),
            normalize_entity_text(obj.get("text", "")),
        )
        if key not in seen_rels:
            seen_rels.add(key)
            merged["relationships"].append(rel)

    # Carry forward other top-level keys (metadata, etc.)
    for k in set(list(base_kg.keys()) + list(overlay_kg.keys())):
        if k not in ("entities", "relationships"):
            merged[k] = overlay_kg.get(k, base_kg.get(k))

    return merged


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


def build_quality_analysis_prompt(
    kgs_kg: dict, gem_kg: dict, gem_tokens: int,
    mod_kg: Optional[dict] = None, mod_tokens: int = 0,
    kgs_stats: Optional[dict] = None,
    gem_stats: Optional[dict] = None,
    mod_stats: Optional[dict] = None,
) -> str:
    """Build prompt for Gemini to compare KGs (2-way or 3-way).

    Sprint 90: Now includes pre-computed schema compliance and requests
    per-pipeline structured output for individual quality assessment cards.
    """
    # Load target schema from patterns YAML for grading context
    import yaml as _yaml
    try:
        with open(PATTERNS_PATH) as f:
            _patterns = _yaml.safe_load(f)
        _types = _patterns.get("types", {})
        valid_type_names = set(_types.keys())
        for info in _types.values():
            valid_type_names.update(info.get("subtypes", {}).keys())
        schema_lines = []
        for parent, info in sorted(_types.items()):
            subs = sorted(info.get("subtypes", {}).keys())
            if subs:
                schema_lines.append(f"  - {parent} (subtypes: {', '.join(subs)})")
            else:
                schema_lines.append(f"  - {parent}")
        valid_rels = [rp["name"] for rp in _patterns.get("relationship_patterns", [])]
        schema_section = f"""## TARGET SCHEMA (what all pipelines were asked to extract)
Valid entity types: {', '.join(sorted(valid_type_names))}
Type hierarchy:
{chr(10).join(schema_lines)}
Valid relationship types: {', '.join(sorted(valid_rels))}

Entities with types NOT in the valid list above are NOISE — they were not requested and should count AGAINST the pipeline that produced them when scoring precision."""
    except Exception:
        schema_section = ""
        valid_type_names = set()

    # Sprint 90: Pre-compute schema compliance (deterministic, not LLM-guessed)
    kgs_compliance = compute_schema_compliance(kgs_kg, valid_type_names)
    gem_compliance = compute_schema_compliance(gem_kg, valid_type_names) if gem_kg else None
    mod_compliance = compute_schema_compliance(mod_kg, valid_type_names) if mod_kg else None

    compliance_section = "\n## PRE-COMPUTED SCHEMA COMPLIANCE (deterministic — use these exact numbers)\n"
    compliance_section += f"- KGSpin: {kgs_compliance['compliance_pct']}% ({kgs_compliance['on_schema']}/{kgs_compliance['total']} on-schema)"
    if kgs_compliance['off_schema_types']:
        compliance_section += f" — off-schema types: {', '.join(kgs_compliance['off_schema_types'])}"
    compliance_section += "\n"
    if gem_compliance:
        compliance_section += f"- LLM Full Shot: {gem_compliance['compliance_pct']}% ({gem_compliance['on_schema']}/{gem_compliance['total']} on-schema)"
        if gem_compliance['off_schema_types']:
            compliance_section += f" — off-schema types: {', '.join(gem_compliance['off_schema_types'])}"
        compliance_section += "\n"
    if mod_compliance:
        compliance_section += f"- LLM Multi-Stage: {mod_compliance['compliance_pct']}% ({mod_compliance['on_schema']}/{mod_compliance['total']} on-schema)"
        if mod_compliance['off_schema_types']:
            compliance_section += f" — off-schema types: {', '.join(mod_compliance['off_schema_types'])}"
        compliance_section += "\n"

    kgs_entities = kgs_kg.get("entities", [])
    gem_entities = gem_kg.get("entities", [])
    kgs_rels = kgs_kg.get("relationships", [])
    gem_rels = gem_kg.get("relationships", [])

    # Truncate to top entities/relationships by confidence to keep prompt size manageable
    MAX_ENTITIES = 100
    MAX_RELS = 80

    def summarize_entities(entities):
        sorted_ents = sorted(entities, key=lambda e: e.get("confidence", 0), reverse=True)
        truncated = len(sorted_ents) > MAX_ENTITIES
        display = sorted_ents[:MAX_ENTITIES]
        lines = []
        for e in display:
            lines.append(f"  - {e.get('text', '?')} ({e.get('entity_type', '?')}, conf={e.get('confidence', 0):.2f})")
        if truncated:
            lines.append(f"  ... and {len(sorted_ents) - MAX_ENTITIES} more (showing top {MAX_ENTITIES} by confidence)")
        return "\n".join(lines)

    def summarize_rels(rels):
        sorted_rels = sorted(rels, key=lambda r: r.get("confidence", 0), reverse=True)
        truncated = len(sorted_rels) > MAX_RELS
        display = sorted_rels[:MAX_RELS]
        lines = []
        for r in display:
            s = r.get("subject", {}).get("text", "?")
            o = r.get("object", {}).get("text", "?")
            p = r.get("predicate", "?")
            c = r.get("confidence", 0)
            lines.append(f"  - {s} --[{p}]--> {o} (conf={c:.2f})")
        if truncated:
            lines.append(f"  ... and {len(sorted_rels) - MAX_RELS} more (showing top {MAX_RELS} by confidence)")
        return "\n".join(lines)

    # Sprint 33.5: Optional Multi-Stage section for 3-way comparison
    mod_section = ""
    mod_pipeline_json = ""
    if mod_kg:
        mod_entities = mod_kg.get("entities", [])
        mod_rels = mod_kg.get("relationships", [])
        mod_section = f"""

## LLM Multi-Stage ({mod_tokens:,} tokens used)
Entities ({len(mod_entities)} total):
{summarize_entities(mod_entities)}

Relationships ({len(mod_rels)} total):
{summarize_rels(mod_rels)}"""
        mod_pipeline_json = """,
    "multistage": {{
      "assessment": "2-3 sentence qualitative assessment",
      "strengths": "key strengths",
      "weaknesses": "key weaknesses",
      "precision": "high/medium/low",
      "recall": "high/medium/low"
    }}"""

    # Build performance metrics section from stats
    perf_section = ""
    if kgs_stats or gem_stats or mod_stats:
        perf_section = "\n## Performance Metrics\n"
        if kgs_stats:
            perf_section += f"- KGSpin: {kgs_stats.get('duration_ms', 0) / 1000:.1f}s, "
            perf_section += f"CPU cost ${kgs_stats.get('cpu_cost', 0):.4f}, "
            perf_section += f"{kgs_stats.get('num_chunks', 0)} chunks (embarrassingly parallel — scales linearly with CPUs)\n"
        if gem_stats:
            perf_section += f"- Full Document: {gem_stats.get('duration_ms', 0) / 1000:.1f}s, "
            perf_section += f"{gem_stats.get('tokens', 0):,} tokens (single monolithic API call, cannot be parallelized)\n"
        if mod_stats:
            perf_section += f"- Chunked: {mod_stats.get('duration_ms', 0) / 1000:.1f}s, "
            perf_section += f"{mod_stats.get('tokens', 0):,} tokens, "
            perf_section += f"{mod_stats.get('chunks_total', 0)} chunks (chunk-level parallelism possible)\n"

    comparison_type = "three" if mod_kg else "two"
    winner_options = "kgenskills|fullshot|multistage|tie" if mod_kg else "kgenskills|fullshot|tie"
    cost_line = f"KGSpin: 0 tokens. LLM Full Shot: {gem_tokens:,} tokens."
    if mod_kg:
        cost_line += f" LLM Multi-Stage: {mod_tokens:,} tokens."

    # Compute 3-way pairwise Performance Delta metrics for the prompt
    delta_scores = compute_diagnostic_scores(kgs_kg, mod_kg=mod_kg, gem_kg=gem_kg if gem_kg else None)

    delta_section = "\n## Pairwise Performance Delta\n"
    for pair_key, pair_data in delta_scores.get("pairs", {}).items():
        pair_labels = {
            "kgs_vs_multistage": ("KGSpin", "LLM Multi-Stage"),
            "kgs_vs_fullshot": ("KGSpin", "LLM Full Shot"),
            "multistage_vs_fullshot": ("LLM Multi-Stage", "LLM Full Shot"),
        }
        a_label, b_label = pair_labels.get(pair_key, ("A", "B"))
        delta_section += f"### {a_label} vs {b_label}\n"
        delta_section += f"- Entity overlap: {pair_data['entity_overlap']} shared, {pair_data['a_only_entities']} {a_label}-only, {pair_data['b_only_entities']} {b_label}-only\n"
        delta_section += f"- Relationship overlap: {pair_data['relationship_overlap']} shared, {pair_data['a_only_relationships']} {a_label}-only, {pair_data['b_only_relationships']} {b_label}-only\n"

    # Build counts summary table
    counts_table = f"""## EXACT COUNTS (use ONLY these numbers — do NOT invent or estimate other counts)
| Pipeline | Entities | Relationships | Tokens | Schema Compliance |
|----------|----------|---------------|--------|-------------------|
| KGSpin | {len(kgs_entities)} | {len(kgs_rels)} | 0 | {kgs_compliance['compliance_pct']}% |
| LLM Full Shot | {len(gem_entities)} | {len(gem_rels)} | {gem_tokens:,} | {gem_compliance['compliance_pct'] if gem_compliance else 'N/A'}% |"""
    if mod_kg:
        counts_table += f"\n| LLM Multi-Stage | {len(mod_entities)} | {len(mod_rels)} | {mod_tokens:,} | {mod_compliance['compliance_pct'] if mod_compliance else 'N/A'}% |"

    return f"""Compare {comparison_type} knowledge graphs extracted from the same document.

{schema_section}
{compliance_section}
{counts_table}

## KGSpin (Compiled Semantics - 0 LLM tokens)
Entities ({len(kgs_entities)} total):
{summarize_entities(kgs_entities)}

Relationships ({len(kgs_rels)} total):
{summarize_rels(kgs_rels)}

## LLM Full Shot ({gem_tokens:,} tokens used)
Entities ({len(gem_entities)} total):
{summarize_entities(gem_entities)}

Relationships ({len(gem_rels)} total):
{summarize_rels(gem_rels)}
{mod_section}
{perf_section}
{delta_section}
## Analysis
For EACH pipeline, provide an independent qualitative assessment covering: schema compliance (use the pre-computed numbers above), entity coverage, relationship quality, and noise level.

CRITICAL GRADING RULES:
1. Use ONLY the exact counts from the EXACT COUNTS table above. Do NOT hallucinate or infer different counts.
2. **Schema compliance is pre-computed.** Use the exact percentages from the PRE-COMPUTED SCHEMA COMPLIANCE section. Do NOT re-count.
3. **Relationship coverage matters.** Do not declare a winner with significantly fewer relationships.
4. **Cost matters.** A zero-cost pipeline that achieves comparable schema-compliant results should be preferred over an expensive one. Explicitly penalize LLM winners if quality delta over KGSpin is <10% but cost is >100x.
5. Pipelines that only extract on-schema entity types demonstrate better precision and should be scored accordingly.

The Pairwise Performance Delta section above contains pre-computed consensus metrics. Use those numbers — do NOT re-count overlaps yourself.

Return JSON with per-pipeline assessments:
{{
  "summary": "2-3 sentence executive summary",
  "pipelines": {{
    "kgenskills": {{
      "assessment": "2-3 sentence qualitative assessment",
      "strengths": "key strengths",
      "weaknesses": "key weaknesses",
      "precision": "high/medium/low",
      "recall": "high/medium/low"
    }},
    "fullshot": {{
      "assessment": "2-3 sentence qualitative assessment",
      "strengths": "key strengths",
      "weaknesses": "key weaknesses",
      "precision": "high/medium/low",
      "recall": "high/medium/low"
    }}{mod_pipeline_json}
  }},
  "cost_analysis": "{cost_line} Assessment of value including scale economics.",
  "winner": "{winner_options}",
  "winner_reason": "Brief explanation"
}}"""


def run_quality_analysis(
    kgs_kg: dict, gem_kg: dict, gem_tokens: int,
    mod_kg: Optional[dict] = None, mod_tokens: int = 0,
    kgs_stats: Optional[dict] = None,
    gem_stats: Optional[dict] = None,
    mod_stats: Optional[dict] = None,
    *,
    llm_alias: str | None = None,
    legacy_model: str | None = None,
) -> dict:
    """Run quality analysis using an LLM (2-way or 3-way).

    Stage 0.5.4 (ADR-002): backend selection follows the demo's alias
    precedence (``llm_alias`` → legacy ``model`` → flow override →
    ``default_alias``). Callers thread the ambient request selectors in.
    """
    from kgspin_demo_app.llm_backend import resolve_llm_backend

    # Sprint 90: Pre-compute schema compliance (deterministic, returned alongside LLM analysis)
    valid_types = _load_valid_entity_types()
    schema_compliance = {
        "kgenskills": compute_schema_compliance(kgs_kg, valid_types),
    }
    if gem_kg:
        schema_compliance["fullshot"] = compute_schema_compliance(gem_kg, valid_types)
    if mod_kg:
        schema_compliance["multistage"] = compute_schema_compliance(mod_kg, valid_types)

    try:
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="quality_analysis",
        )
        prompt = build_quality_analysis_prompt(
            kgs_kg, gem_kg, gem_tokens, mod_kg, mod_tokens,
            kgs_stats=kgs_stats, gem_stats=gem_stats, mod_stats=mod_stats,
        )
        result = backend.complete(prompt)

        try:
            analysis = json.loads(result.text)
        except json.JSONDecodeError:
            analysis = {"summary": result.text, "error": "Failed to parse JSON"}

        # Inject pre-computed schema compliance into analysis
        analysis["schema_compliance"] = schema_compliance

        return {"analysis": analysis, "tokens": result.tokens_used}

    except Exception as e:
        return {
            "analysis": {
                "summary": f"Quality analysis failed: {e}",
                "error": str(e),
                "schema_compliance": schema_compliance,
            },
            "tokens": 0,
        }


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
            "ticker": ticker,
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
                "ticker": ticker,
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
        _parse_and_chunk, sec_html, ticker, corpus_kb, bundle_name, pipeline_id
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
        # Domain-agnostic: keys match bundle.document_seed_facts[].source_field.
        _doc_metadata = {
            "company_name": _company,
            "ticker": ticker,
            "cik": sec_doc.cik or "",
            "accession_number": sec_doc.accession_number or "",
            "filing_date": sec_doc.filing_date or "",
            "fiscal_year_end": sec_doc.fiscal_year_end or "",
        }
        kgs_task = asyncio.create_task(
            asyncio.to_thread(
                _run_kgenskills, demo_text, info["name"], ticker, bundle,
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
                # INIT-001 Sprint 03 / BUG-010 response: every slot entry
                # point logs the full traceback; same discipline as the
                # KGSpin slot wrapper above.
                logger.exception("LLM Full Shot compare slot failed")
                yield sse_event("error", {
                    "step": "gemini", "pipeline": "gemini",
                    "message": f"LLM Full Shot failed: {e}",
                    "recoverable": True,
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
                # INIT-001 Sprint 03 / BUG-010 response: every slot entry point logs.
                logger.exception("LLM Multi-Stage compare slot failed")
                yield sse_event("error", {
                    "step": "modular", "pipeline": "modular",
                    "message": f"LLM Multi-Stage failed: {e}",
                    "recoverable": True,
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

    # INIT-001 Sprint 02: log + surface any failure during early bundle
    # resolution. Structural/other pipeline_ids can fail here if the split
    # bundle overlay is broken, before _run_kgenskills is ever scheduled.
    try:
        bundle = _get_bundle(bundle_name, pipeline_id=pipeline_id)
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
    # Sprint 118: Use split bundle ID for cache key when using split bundles
    if bundle_name and pipeline_id and DOMAIN_BUNDLES_DIR.is_dir() and (DOMAIN_BUNDLES_DIR / bundle_name).is_dir():
        _bid = _split_bundle_id(bundle_name, pipeline_id)
    else:
        _bid = bundle_name or BUNDLE_PATH.name
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
                "ticker": cfe.ticker,
                "reason": cfe.reason,
                "message": cfe.actionable_hint,
                "attempted": cfe.attempted,
                "recoverable": False,
            })
            yield sse_event("done", {"total_duration_ms": 0})
            return
        _bundle_tmp, _full, demo_text, actual_kb, _ = await asyncio.to_thread(
            _parse_and_chunk, sec_doc.raw_html, ticker, corpus_kb, bundle_name, pipeline_id
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

    bundle = _get_bundle(bundle_name, pipeline_id=pipeline_id)
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
        "ticker": ticker,
        "cik": sec_doc.cik or "",
        "accession_number": sec_doc.accession_number or "",
        "filing_date": sec_doc.filing_date or "",
        "fiscal_year_end": sec_doc.fiscal_year_end or "",
    } if sec_doc else None

    kgs_task = asyncio.create_task(
        asyncio.to_thread(
            _run_kgenskills, demo_text, info["name"], ticker, bundle,
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
    # Sprint 118: Use split bundle ID when applicable
    if bundle_name and pipeline_id and DOMAIN_BUNDLES_DIR.is_dir() and (DOMAIN_BUNDLES_DIR / bundle_name).is_dir():
        _log_bid = _split_bundle_id(bundle_name, pipeline_id)
    else:
        _log_bid = bundle_name or BUNDLE_PATH.name
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
                "ticker": cfe.ticker,
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
            )
        )

        while not gemini_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(gemini_task), timeout=1.0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0)
            if await request.is_disconnected():
                gemini_task.cancel()
                return

        try:
            gem_kg, gem_tokens, gem_elapsed, gem_errors, gem_truncated = gemini_task.result()
        except Exception as e:
            # INIT-001 Sprint 02 / BUG-010 response: every slot entry point
            # logs the full traceback so upstream bugs can be diagnosed on the
            # first occurrence instead of requiring a re-run with added logging.
            logger.exception("LLM Full Shot refresh failed")
            yield sse_event("error", {
                "step": "gemini", "pipeline": "gemini",
                "message": f"LLM Full Shot failed: {e}",
                "recoverable": True,
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
            if await request.is_disconnected():
                modular_task.cancel()
                return

        # Sprint 33.6: Cleanup cancel event
        _modular_cancel_events.pop(ticker, None)
        try:
            mod_kg, h_tokens, l_tokens, mod_elapsed, mod_errors = modular_task.result()
        except Exception as e:
            # INIT-001 Sprint 02 / BUG-010 response: log full traceback.
            logger.exception("LLM Multi-Stage refresh failed")
            yield sse_event("error", {
                "step": "modular", "pipeline": "modular",
                "message": f"LLM Multi-Stage failed: {e}",
                "recoverable": True,
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
                     bundle_name: str | None = None, pipeline_id: str | None = None):
    """Parse HTML to text, truncate to corpus_kb at paragraph boundary, then chunk.

    Sprint 33.3: Byte-based corpus sizing. All pipelines receive the same
    truncated text. KGSpin and Multi-Stage share the same chunk boundaries
    (VP Parity Guard). Full Shot gets the raw truncated text.

    Returns:
        (bundle, full_text, truncated_text, actual_kb, all_chunks)
    """
    from kgspin_core.execution.extractor import DocumentChunker

    from kgspin_core.execution.preprocessors import resolve_preprocessors

    bundle = _get_bundle(bundle_name, pipeline_id=pipeline_id)
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
                "ticker": cfe.ticker,
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
        _clinical_patterns = Path(__file__).resolve().parent.parent.parent / "bundles" / "legacy" / "clinical.yaml"
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
            "ticker": nct_id,
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

        kgen_result = await kgen_task
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
            mod_kg, mod_tokens, mod_elapsed, mod_errors = await modular_task
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


def _run_kgenskills(
    text: str, company_name: str, ticker: str, bundle,
    on_chunk_complete=None, raw_html=None,
    on_l_module_start=None,
    on_table_extraction_start=None,
    on_table_extraction_done=None,
    on_post_chunk_progress=None,
    document_metadata: dict = None,
) -> dict:
    """Run KGSpin extraction via unified run_pipeline().

    Sprint 42.5: Thin wrapper around KnowledgeGraphExtractor.run_pipeline().
    The extractor reads bundle.execution_strategy internally to dispatch
    to one of the deterministic KGSpin pipelines (emergent / structural /
    default) or the LLM-backed pipelines (llm_full_shot / llm_multi_stage).

    The deterministic paths (KGSpin Base / Emergent / Structural) all use
    GLiNER as their NER backend and consume zero LLM tokens. They are the
    "compare baseline" against the LLM slots — the whole point of the
    demo is to show the deterministic pipelines produce knowledge graphs
    without paying for inference.
    """
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor

    backend = _get_gliner_backend()
    extractor = KnowledgeGraphExtractor(bundle)

    def log_cb(msg):
        logger.info(msg)

    result = extractor.run_pipeline(
        text=text,
        main_entity=company_name,
        source_document=f"{ticker}_10K",
        backend=backend,
        raw_html=raw_html,
        log_callback=log_cb,
        on_chunk_complete=on_chunk_complete,
        on_post_chunk_progress=on_post_chunk_progress,
        document_metadata=document_metadata,
    )

    kg_dict = result.to_dict()
    # Sprint 101: Attach quarantine count for demo display.
    quarantine_count = len(getattr(extractor, '_last_emergent_quarantine', []) or [])
    kg_dict["_quarantine_count"] = quarantine_count
    # Preserve H-Module entities (with aliases) for news seeding
    if hasattr(result, '_h_module_entities') and result._h_module_entities:
        kg_dict["_h_module_entities"] = [e.to_dict() for e in result._h_module_entities]
    return kg_dict


def _run_agentic_flash(
    text: str,
    company_name: str,
    source_id: str,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run LLM Full Shot extraction — entire corpus in one prompt.

    Stage 0.5.4 (ADR-002): accepts either ``llm_alias`` (preferred), a
    direct ``(llm_provider, llm_model)`` pair, or the legacy ``model``
    string (Gemini-only, DeprecationWarning). Falls through to
    ``AppSettings.llm.default_alias`` when no selector is supplied.

    Returns (kg_dict, tokens, elapsed, error_count, truncated) for backwards
    compatibility with the calling SSE event builder.
    """
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor
    from kgspin_core.execution.pipeline_engine import PipelineConfigRef
    from kgspin_demo_app.llm_backend import resolve_llm_backend
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    # Agentic strategies are YAML-dispatched via admin (ADR-031 / Sprint 18).
    # Load the base discovery-deep bundle for type info, then hand
    # pipeline_config_ref=agentic-flash to run_pipeline — core resolves
    # the overlay from admin's pipeline_config registry at dispatch.
    bundle = _get_bundle(
        bundle_name=Path(bundle_path).name if bundle_path else None,
        pipeline_id="discovery-deep",
    )
    registry_client = HttpResourceRegistryClient()

    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        legacy_model=model,
    )
    extractor = KnowledgeGraphExtractor(bundle)

    def log_cb(msg):
        logger.info(msg)

    logger.info(f"[AGENTIC_FLASH] starting model={model} text_chars={len(text)}")
    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=company_name,
            source_document=source_id,
            backend=backend,
            log_callback=log_cb,
            pipeline_config_ref=PipelineConfigRef(name="agentic-flash", version="v1"),
            registry_client=registry_client,
        )
        elapsed = time.time() - t0
        logger.info(f"[AGENTIC_FLASH] complete elapsed={elapsed:.2f}s")
        kg = result.to_dict()
        return kg, 0, elapsed, 0, False
    except Exception:
        logger.exception("Agentic Flash run_pipeline failed")
        raise



def _run_agentic_analyst(
    text: str,
    company_name: str,
    source_id: str,
    on_chunk_complete=None,
    cancel_event=None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run LLM Multi-Stage extraction — macro-chunked with schema awareness.

    Stage 0.5.4 (ADR-002): accepts ``llm_alias`` / direct provider+model /
    legacy ``model``. See :func:`_run_agentic_flash` for the precedence
    contract; both wrappers delegate to
    :func:`kgspin_demo_app.llm_backend.resolve_llm_backend`.

    Returns (kg, h_tokens, l_tokens, elapsed, error_count) to match the existing
    caller interface. h_tokens is reported as the total; l_tokens stays 0.
    """
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor
    from kgspin_core.execution.pipeline_engine import PipelineConfigRef
    from kgspin_demo_app.llm_backend import resolve_llm_backend
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    bundle = _get_bundle(
        bundle_name=Path(bundle_path).name if bundle_path else None,
        pipeline_id="discovery-deep",
    )
    registry_client = HttpResourceRegistryClient()

    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        legacy_model=model,
    )
    extractor = KnowledgeGraphExtractor(bundle)

    def log_cb(msg):
        logger.info(msg)

    logger.info(f"[AGENTIC_ANALYST] starting model={model} chunk_size={chunk_size} text_chars={len(text)}")
    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=company_name,
            source_document=source_id,
            backend=backend,
            on_chunk_complete=on_chunk_complete,
            log_callback=log_cb,
            pipeline_config_ref=PipelineConfigRef(name="agentic-analyst", version="v1"),
            registry_client=registry_client,
        )
        elapsed = time.time() - t0
        logger.info(f"[AGENTIC_ANALYST] complete elapsed={elapsed:.2f}s")
        kg = result.to_dict()
        # INIT-001 Sprint 03 known limitation: ExtractionResult doesn't surface
        # LLM token counts. Dev-report tech-debt entry for a core-team
        # enhancement to expose tokens via result.provenance.
        return kg, 0, 0, elapsed, 0
    except Exception:
        logger.exception("Agentic Analyst run_pipeline failed")
        raise


def _run_clinical_gemini_full_shot(
    text: str,
    trial_name: str,
    source_id: str,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run LLM Full Shot extraction with clinical bundle + patterns.

    Stage 0.5.4 (ADR-002): same selector precedence as
    :func:`_run_agentic_flash`.
    """
    from dataclasses import replace
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor
    from kgspin_demo_app.llm_backend import resolve_llm_backend

    base_bundle = _get_bundle(
        bundle_name=Path(bundle_path).name if bundle_path else None,
        pipeline_id="discovery-deep",
    )
    bundle = replace(base_bundle, execution_strategy="agentic_flash")
    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        legacy_model=model,
    )
    extractor = KnowledgeGraphExtractor(bundle)

    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=trial_name,
            source_document=source_id,
            backend=backend,
        )
        elapsed = time.time() - t0
        return result.to_dict(), 0, elapsed, 0, False
    except Exception:
        logger.exception("Clinical LLM Full Shot run_pipeline failed")
        raise


def _run_clinical_modular(
    text: str,
    trial_name: str,
    source_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run LLM Multi-Stage extraction with clinical bundle + patterns.

    Stage 0.5.4 (ADR-002): same selector precedence as
    :func:`_run_agentic_analyst`.
    """
    from dataclasses import replace
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor
    from kgspin_demo_app.llm_backend import resolve_llm_backend

    base_bundle = _get_bundle(
        bundle_name=Path(bundle_path).name if bundle_path else None,
        pipeline_id="discovery-deep",
    )
    bundle = replace(base_bundle, execution_strategy="agentic_analyst")
    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        legacy_model=model,
    )
    extractor = KnowledgeGraphExtractor(bundle)

    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=trial_name,
            source_document=source_id,
            backend=backend,
        )
        elapsed = time.time() - t0
        return result.to_dict(), 0, elapsed, 0
    except Exception:
        logger.exception("Clinical LLM Multi-Stage run_pipeline failed")
        raise


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
        "info": {"ticker": ticker, "domain": domain},
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
            info = {"name": trial_title, "domain": "clinical", "ticker": ticker}
        else:
            info = {"name": ticker, "domain": "clinical", "ticker": ticker}
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
                    "ticker": ticker,
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

    # Sprint 06 Task 2: news fetching now goes through the canonical
    # kgspin-plugin-news-financial NewsApiProvider via _fetch_newsapi_articles.
    # Pre-Sprint-06 the code imported from `kgenskills.data_sources.*` which
    # was the OLD monolithic package removed in the kgspin-core split — those
    # imports silently failed and the Explorer tab showed zero articles.

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
        # --- Sprint 33.14c: Non-cached path — full SEC extraction via _run_kgenskills ---
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
                "ticker": ticker,
                "cik": getattr(sec_doc, "cik", "") or "",
                "accession_number": getattr(sec_doc, "accession_number", "") or "",
                "filing_date": getattr(sec_doc, "filing_date", "") or "",
                "fiscal_year_end": getattr(sec_doc, "fiscal_year_end", "") or "",
            }

            t0 = time.time()
            sec_task = asyncio.create_task(asyncio.to_thread(
                _run_kgenskills, trunc_text, info["name"], ticker, bundle,
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

            sec_kg = sec_task.result()
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

            article_kgs = news_task.result()
            duration = int((time.time() - t0) * 1000)
            yield sse_event("step_complete", {
                "step": "news_extraction",
                "label": f"Extracted {len(article_kgs)} news articles",
                "duration_ms": duration,
            })
            await asyncio.sleep(0)

        # Phase C: Runtime merge — SEC base graph + news article graphs
        kgs_kg = sec_kg
        for akg in article_kgs:
            kgs_kg = _merge_kgs(kgs_kg, akg)

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

        article_kgs = extraction_task.result()

        # Merge: cached SEC KG + all per-article news KGs
        kgs_kg = cached_kgs_kg
        for akg in article_kgs:
            kgs_kg = _merge_kgs(kgs_kg, akg)

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
