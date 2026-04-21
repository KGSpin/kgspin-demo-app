"""Minimal NewsAPI client used by both finance and healthcare news landers.

Protocol-FREE: zero imports from kgspin_core. Requests + tenacity only.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Iterator
from urllib.parse import urlparse

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


_NEWSAPI_EVERYTHING = "https://newsapi.org/v2/everything"


class NewsApiError(Exception):
    """Wraps NewsAPI failures (401 invalid key, 429 quota, 5xx, etc.)."""


@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
)
def _everything_request(
    api_key: str,
    user_agent: str,
    query: str,
    limit: int,
    timeout: int = 15,
) -> dict:
    """Hit /v2/everything. Credentials go in headers (VP Sec Mandate 3.1)."""
    headers = {"X-Api-Key": api_key, "User-Agent": user_agent}
    params = {"q": query, "pageSize": min(max(limit, 1), 100), "language": "en"}
    resp = requests.get(_NEWSAPI_EVERYTHING, headers=headers, params=params, timeout=timeout)
    if resp.status_code == 401:
        raise NewsApiError("NewsAPI 401 Unauthorized — check NEWSAPI_KEY")
    if resp.status_code == 429:
        raise NewsApiError("NewsAPI 429 Rate limited — retry later or upgrade plan")
    resp.raise_for_status()
    return resp.json()


def query_newsapi(
    api_key: str,
    user_agent: str,
    query: str,
    limit: int,
    logger: logging.Logger,
) -> list[dict]:
    """Query NewsAPI /v2/everything and return a list of article dicts.

    Each dict contains: url, title, description, content, publishedAt,
    source_name, author. The raw response bodies (title + description +
    content) become the landed artifact's text — core extracts from that.
    """
    logger.info(f"NewsAPI query={query!r} limit={limit}")
    data = _everything_request(api_key, user_agent, query, limit)
    articles = data.get("articles") or []
    results = []
    for a in articles[:limit]:
        url = (a.get("url") or "").strip()
        if not url:
            continue
        results.append({
            "url": url,
            "title": a.get("title") or "",
            "description": a.get("description") or "",
            "content": a.get("content") or "",
            "published_at": a.get("publishedAt") or "",
            "source_name": ((a.get("source") or {}).get("name")) or "",
            "author": a.get("author") or "",
        })
    logger.info(f"NewsAPI returned {len(results)} usable articles")
    return results


def article_identifier(url: str) -> str:
    """Deterministic short identifier for an article URL.

    Uses SHA-256 prefix + the domain hostname so the file-store layout
    is human-scannable ("nytimes-abc123def") while staying injection-safe.
    Domain chars outside ``[A-Za-z0-9-]`` are stripped.
    """
    host = urlparse(url).hostname or "unknown"
    # sanitize host: strip subdomain dots + non-alnum (except dash)
    host_sanitized = "".join(c for c in host.split(".")[-2 if "." in host else 0].lower() if c.isalnum() or c == "-") or "src"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{host_sanitized}-{digest}"


def article_body_text(article: dict) -> str:
    """Build the raw artifact text from NewsAPI fields.

    Landers write this as ``raw.txt``. Core's extractor treats it as
    unstructured text — no HTML parsing required downstream.
    """
    parts = []
    if article.get("title"):
        parts.append(article["title"])
    if article.get("description"):
        parts.append(article["description"])
    if article.get("content"):
        parts.append(article["content"])
    return "\n\n".join(parts)


def article_body_bytes_iter(article: dict) -> Iterator[bytes]:
    """Yield the article body as a single bytes chunk (for stream_to_file)."""
    yield article_body_text(article).encode("utf-8")
