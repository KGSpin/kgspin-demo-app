"""Marketaux client — used by the finance-news lander.

Protocol-FREE: zero imports from kgspin_core. Requests + tenacity only.

Marketaux is a **specialized financial-data provider** that maps news
articles to ticker symbols + sector keywords at fetch time. It's the
right backend for ticker-scoped finance news. General-purpose, term-
scoped news (including cross-domain clinical per Sprint 11) uses
NewsAPI.org via ``_newsapi_client.py`` + ``landers/newsapi.py``.

Env: ``MARKETAUX_API_KEY`` (required by the Sprint 11 ``marketaux`` lander).

Auth note: Marketaux takes the key as the ``api_token`` query param
(its own convention). VP Sec LOW still applies — we never log or
serialize the param; the `requests.get(params={...})` lib handles
URL-encoding internally without echoing to our own logs.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlparse

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


_MARKETAUX_NEWS_ALL = "https://api.marketaux.com/v1/news/all"


class MarketauxError(Exception):
    """Wraps Marketaux failures (401 invalid key, 429 quota, 5xx, etc.)."""


@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
)
def _news_all_request(
    api_key: str,
    user_agent: str,
    ticker: str,
    limit: int,
    days: int = 7,
    timeout: int = 15,
) -> dict:
    """Hit /v1/news/all scoped by symbol.

    Marketaux auth is ``?api_token=...`` — its own convention. Keep it
    confined to this function; never echo to logs.
    """
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "api_token": api_key,
        "symbols": ticker,
        "published_after": start.strftime("%Y-%m-%dT%H:%M"),
        "limit": min(max(limit, 1), 50),  # Marketaux hard-caps at 50
        "language": "en",
    }
    resp = requests.get(_MARKETAUX_NEWS_ALL, headers=headers, params=params, timeout=timeout)
    if resp.status_code == 401:
        raise MarketauxError("Marketaux 401 Unauthorized — check MARKETAUX_API_KEY")
    if resp.status_code == 429:
        raise MarketauxError("Marketaux 429 Rate limited — retry later or upgrade plan")
    if resp.status_code == 402:
        raise MarketauxError("Marketaux 402 Payment required — plan quota exhausted")
    resp.raise_for_status()
    return resp.json()


def query_marketaux(
    api_key: str,
    user_agent: str,
    ticker: str,
    limit: int,
    logger: logging.Logger,
    days: int = 7,
) -> list[dict]:
    """Query Marketaux /v1/news/all for a ticker and return article dicts.

    Each dict: url, title, description, published_at, source_name,
    tickers (list of related symbols), keywords. `description` is the
    full available body; Marketaux doesn't expose article full-text in
    the free tier — we rely on title + description + entity keywords
    as the landed artifact content.
    """
    logger.info(f"Marketaux query ticker={ticker!r} limit={limit} days={days}")
    data = _news_all_request(api_key, user_agent, ticker, limit, days=days)
    items = data.get("data") or []
    results: list[dict] = []
    for item in items[:limit]:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        entities = item.get("entities") or []
        tickers = [
            e.get("symbol", "")
            for e in entities
            if e.get("type") == "equity" and e.get("symbol")
        ]
        results.append({
            "url": url,
            "title": item.get("title") or "",
            "description": item.get("description") or "",
            "content": "",  # Marketaux doesn't ship full content in news/all
            "published_at": item.get("published_at") or "",
            "source_name": item.get("source") or "Marketaux",
            "tickers": tickers,
            "keywords": item.get("keywords") or [],
        })
    logger.info(f"Marketaux returned {len(results)} usable articles")
    return results


def article_identifier(url: str) -> str:
    """Deterministic short identifier for an article URL.

    Same shape as ``_newsapi_client.article_identifier``: SHA-256 prefix
    + domain hostname. Consumers can't tell by the id whether the
    source was Marketaux or NewsAPI — the lander's ``source`` field in
    CorpusDocumentMetadata carries that distinction.
    """
    host = urlparse(url).hostname or "unknown"
    host_sanitized = "".join(
        c for c in host.split(".")[-2 if "." in host else 0].lower()
        if c.isalnum() or c == "-"
    ) or "src"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{host_sanitized}-{digest}"


def article_body_text(article: dict) -> str:
    """Build the raw artifact text from Marketaux fields.

    Title + description + keywords. Keywords are included so a
    downstream extractor has topical scaffolding (Marketaux surfaces
    things like ``["earnings", "guidance", "M&A"]`` that won't
    otherwise appear in the truncated description).
    """
    parts = []
    if article.get("title"):
        parts.append(article["title"])
    if article.get("description"):
        parts.append(article["description"])
    keywords = article.get("keywords") or []
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords))
    tickers = article.get("tickers") or []
    if tickers:
        parts.append("Related tickers: " + ", ".join(tickers))
    return "\n\n".join(parts)


def article_body_bytes_iter(article: dict) -> Iterator[bytes]:
    """Yield the article body as a single bytes chunk (for stream_to_file)."""
    yield article_body_text(article).encode("utf-8")
