"""Refresh-corpus endpoints (per domain / per source).

Each handler validates its identifier, then streams an SSE response from
:func:`demo_compare._run_lander_subprocess` — the lander-subprocess
runner stays in demo_compare for now (Wave C will hoist it into a
dedicated module).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_NCT_RE = re.compile(r"^NCT[0-9]{8}$")
_NEWS_QUERY_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,100}$")

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _lander_subprocess(*args, **kwargs):
    from demo_compare import _run_lander_subprocess
    return _run_lander_subprocess(*args, **kwargs)


def _derive_clinical_query_from_nct(nct: str, default: str = "") -> str:
    """Thin wrapper around ``services.derive_clinical_query_from_nct``
    that uses the demo's shared registry client."""
    from demo_compare import _get_registry_client
    from kgspin_demo_app.services import derive_clinical_query_from_nct as _impl
    return _impl(_get_registry_client(), nct, default=default)


@router.get("/api/refresh-corpus/sec/{doc_id}")
async def refresh_corpus_sec(doc_id: str, request: Request, filing: str = "10-K"):
    """Sprint 07 Task 7: trigger the SEC lander from the UI."""
    t = doc_id.strip().upper()
    if not _TICKER_RE.fullmatch(t):
        return JSONResponse(
            {"error": f"Invalid ticker {doc_id!r}. Expected 1-5 ASCII letters."},
            status_code=400,
        )
    if filing not in {"10-K", "10-Q", "8-K"}:
        return JSONResponse(
            {"error": f"Invalid filing {filing!r}."},
            status_code=400,
        )
    return StreamingResponse(
        _lander_subprocess(
            "sec", ["--ticker", t, "--filing", filing],
            registry_key=("financial", "sec_edgar", {"ticker": t, "form": filing}),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/api/refresh-corpus/clinical/{doc_id}")
async def refresh_corpus_clinical(doc_id: str, request: Request):
    """Sprint 07 Task 7: trigger the clinical lander from the UI."""
    n = doc_id.strip().upper()
    if not _NCT_RE.fullmatch(n):
        return JSONResponse(
            {"error": f"Invalid NCT id {doc_id!r}. Expected NCT followed by 8 digits."},
            status_code=400,
        )
    return StreamingResponse(
        _lander_subprocess(
            "clinical", ["--nct", n],
            registry_key=("clinical", "clinicaltrials_gov", {"nct": n}),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/api/refresh-corpus/yahoo-rss/{doc_id}")
async def refresh_corpus_yahoo_rss(doc_id: str, request: Request, limit: int = 5):
    """Sprint 11 (ADR-004): trigger the real Yahoo Finance RSS lander."""
    t = doc_id.strip().upper()
    if not _TICKER_RE.fullmatch(t):
        return JSONResponse({"error": f"Invalid ticker {doc_id!r}."}, status_code=400)
    if not (1 <= limit <= 20):
        return JSONResponse({"error": f"limit must be 1..20; got {limit}"}, status_code=400)
    return StreamingResponse(
        _lander_subprocess(
            "yahoo-rss", ["--ticker", t, "--limit", str(limit)],
            registry_key=("financial", "yahoo_rss", {"ticker": t}),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/api/refresh-corpus/marketaux/{doc_id}")
async def refresh_corpus_marketaux(doc_id: str, request: Request, limit: int = 5):
    """Sprint 11 (ADR-004): trigger the Marketaux finance news lander."""
    t = doc_id.strip().upper()
    if not _TICKER_RE.fullmatch(t):
        return JSONResponse({"error": f"Invalid ticker {doc_id!r}."}, status_code=400)
    if not (1 <= limit <= 20):
        return JSONResponse({"error": f"limit must be 1..20; got {limit}"}, status_code=400)
    return StreamingResponse(
        _lander_subprocess(
            "marketaux", ["--ticker", t, "--limit", str(limit)],
            registry_key=("financial", "marketaux", {"ticker": t}),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/api/refresh-corpus/newsapi")
async def refresh_corpus_newsapi(
    request: Request,
    query: str = "",
    domain: str = "clinical",
    limit: int = 5,
    nct: str = "",
):
    """Sprint 11 (ADR-004): trigger the domain-agnostic NewsAPI lander."""
    from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS

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
        _lander_subprocess(
            "newsapi",
            ["--query", q, "--domain", domain, "--limit", str(limit)],
            registry_key=(domain, "newsapi", {"query": q}),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/api/refresh-corpus/news/{domain}")
async def refresh_all_domain_news(
    domain: str,
    request: Request,
    ticker: str = "",
    query: str = "",
    nct: str = "",
    limit: int = 5,
):
    """VP Prod primary button: fire every news lander for ``domain`` in parallel."""
    from demo_compare import _NEWS_SOURCES_BY_DOMAIN
    from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS
    from sse.events import sse_event

    if domain not in DOMAIN_FETCHERS:
        return JSONResponse({"error": f"Unknown domain {domain!r}."}, status_code=400)
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
            try:
                async for ev in _lander_subprocess(kind, args, registry_key=rk):
                    await queue.put((kind, ev))
            except Exception as e:
                logger.exception("Lander driver failed: kind=%s", kind)
                await queue.put((kind, sse_event("error", {
                    "step": f"lander:{kind}",
                    "message": f"{kind} lander failed: {e}",
                    "recoverable": False,
                })))
            finally:
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
        headers=_SSE_HEADERS,
    )
