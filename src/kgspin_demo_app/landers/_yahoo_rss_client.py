"""Yahoo Finance RSS client — used by the Yahoo RSS lander.

Protocol-FREE: zero imports from kgspin_core. Requests + feedparser
only (feedparser is already a demo dep via Sprint 07).

No credentials required — Yahoo's finance RSS feed is public:
``https://feeds.finance.yahoo.com/rss/2.0/headline?s=<TICKER>&region=US&lang=en-US``

VP Eng MAJOR (Sprint 11 Phase 1 review) — resource limits are
mandatory because RSS feeds are external untrusted inputs:

- **Fetch timeout: 15 seconds** (``requests.get(timeout=15)``)
- **Response size cap: 5 MiB** (streams body, aborts on overflow)
- **Entry count cap: 100 items** (post-parse; protects against a
  pathologically large feed that just fits under 5 MiB)

Each limit raises a typed ``YahooRssError`` subclass so callers can
distinguish timeout vs. oversize vs. malformed-XML.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Iterator
from urllib.parse import urlparse, quote

import feedparser
import requests


_YAHOO_RSS_BASE = "https://feeds.finance.yahoo.com/rss/2.0/headline"

FEED_TIMEOUT_SECONDS: int = 15
FEED_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MiB
FEED_MAX_ENTRIES: int = 100
_STREAM_CHUNK_BYTES: int = 64 * 1024


class YahooRssError(Exception):
    """Base class for Yahoo RSS client failures."""


class YahooRssTimeout(YahooRssError):
    """Raised when feed fetch exceeds ``FEED_TIMEOUT_SECONDS``."""


class YahooRssFeedTooLarge(YahooRssError):
    """Raised when the feed response body exceeds ``FEED_MAX_BYTES``.

    We abort the download mid-stream and never parse a partial body —
    feedparser on truncated XML is a silent-corruption hazard.
    """

    def __init__(self, ticker: str, limit_bytes: int) -> None:
        self.ticker = ticker
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Yahoo RSS feed for {ticker!r} exceeded {limit_bytes:,} bytes; aborted."
        )


class YahooRssMalformed(YahooRssError):
    """Raised when feedparser reports a bozo exception the client can't ignore.

    Transient "feed missing declared charset" bozo flags are still accepted
    (feedparser sets ``bozo=1`` for them but the parse succeeds) — this
    subclass is reserved for parses where ``entries`` is empty **and** the
    bozo exception is a hard XML / protocol error.
    """


def _build_feed_url(ticker: str) -> str:
    """Yahoo wants a single uppercase ticker, URL-encoded defensively.

    Tickers are already validated by the caller to be ``[A-Z]{1,5}`` — the
    ``quote()`` is belt-and-braces in case a future caller slips in a dot
    (e.g. ``BRK.A``) or dash (``BRK-A``).
    """
    safe = quote(ticker.upper(), safe="")
    return f"{_YAHOO_RSS_BASE}?s={safe}&region=US&lang=en-US"


def _fetch_feed_bytes(ticker: str, user_agent: str) -> bytes:
    """Stream the RSS body with the VP Eng-mandated caps.

    Implementation note: we use ``requests`` (not ``feedparser.parse(url)``
    directly) because feedparser's URL path doesn't respect a byte cap — it
    reads the full response into memory before parsing. Streaming lets us
    abort on overflow before writing anything downstream.
    """
    url = _build_feed_url(ticker)
    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.5"}
    try:
        resp = requests.get(url, headers=headers, timeout=FEED_TIMEOUT_SECONDS, stream=True)
    except requests.Timeout as e:
        raise YahooRssTimeout(
            f"Yahoo RSS timed out after {FEED_TIMEOUT_SECONDS}s for {ticker!r}"
        ) from e
    except requests.RequestException as e:
        raise YahooRssError(f"Yahoo RSS request failed for {ticker!r}: {e}") from e

    if resp.status_code != 200:
        resp.close()
        raise YahooRssError(
            f"Yahoo RSS returned HTTP {resp.status_code} for {ticker!r}"
        )

    buf = bytearray()
    try:
        for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > FEED_MAX_BYTES:
                raise YahooRssFeedTooLarge(ticker, FEED_MAX_BYTES)
    finally:
        resp.close()
    return bytes(buf)


def query_yahoo_rss(
    user_agent: str,
    ticker: str,
    limit: int,
    logger: logging.Logger,
) -> list[dict]:
    """Fetch + parse Yahoo Finance RSS for a ticker; return article dicts.

    Each dict: ``url``, ``title``, ``description``, ``content`` (empty —
    Yahoo RSS doesn't surface full bodies), ``published_at``,
    ``source_name`` (always ``"Yahoo Finance"``), ``author`` (empty).

    Enforces VP Eng caps: 15s timeout, 5 MiB response, 100 entries
    post-parse. ``limit`` is additionally clamped at the caller's
    request but ``FEED_MAX_ENTRIES`` is the hard ceiling.
    """
    logger.info(f"Yahoo RSS query ticker={ticker!r} limit={limit}")
    raw = _fetch_feed_bytes(ticker, user_agent)

    # feedparser accepts bytes directly; we avoid writing to disk.
    parsed = feedparser.parse(raw)
    entries = parsed.get("entries") or []

    # bozo=1 alone is not an error — feedparser sets it for charset-less
    # feeds that parse correctly. Only reject when there are zero entries
    # AND a hard XML bozo_exception.
    if not entries:
        bozo_exc = parsed.get("bozo_exception")
        if bozo_exc is not None:
            raise YahooRssMalformed(
                f"Yahoo RSS feed for {ticker!r} failed to parse: {bozo_exc}"
            )

    # Entry-count cap. Clamp to min(caller_limit, FEED_MAX_ENTRIES).
    hard_cap = max(1, min(limit, FEED_MAX_ENTRIES))
    entries = entries[:hard_cap]

    results: list[dict] = []
    for entry in entries:
        url = (entry.get("link") or "").strip()
        if not url:
            continue
        # feedparser normalizes published dates to ``published`` string
        # and ``published_parsed`` struct. Keep the string form; lander
        # consumers can reparse if they need structured time.
        results.append({
            "url": url,
            "title": (entry.get("title") or "").strip(),
            "description": (entry.get("summary") or "").strip(),
            "content": "",  # Yahoo RSS has no full body
            "published_at": (entry.get("published") or "").strip(),
            "source_name": "Yahoo Finance",
            "author": "",
        })
    logger.info(f"Yahoo RSS returned {len(results)} usable articles")
    return results


def article_identifier(url: str) -> str:
    """Deterministic short identifier for an article URL.

    Same shape as ``_newsapi_client.article_identifier`` and
    ``_marketaux_client.article_identifier``: SHA-256 prefix + hostname
    stem. ADR-004 requires that article IDs don't encode the backend —
    the lander's ``name`` on the CORPUS_DOCUMENT record is the source of
    truth for provenance.
    """
    host = urlparse(url).hostname or "unknown"
    host_sanitized = "".join(
        c for c in host.split(".")[-2 if "." in host else 0].lower()
        if c.isalnum() or c == "-"
    ) or "src"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{host_sanitized}-{digest}"


def article_body_text(article: dict) -> str:
    """Build the raw artifact text from Yahoo RSS fields.

    Yahoo RSS has no content body in the feed — landed artifact is
    title + summary only (~200 chars). That's the deliberate trade-off
    per Sprint 11 plan §4 out-of-scope: Yahoo full-article scraping is
    a separate integration.
    """
    parts = []
    if article.get("title"):
        parts.append(article["title"])
    if article.get("description"):
        parts.append(article["description"])
    return "\n\n".join(parts)


def article_body_bytes_iter(article: dict) -> Iterator[bytes]:
    """Yield the article body as a single bytes chunk (for stream_to_file)."""
    yield article_body_text(article).encode("utf-8")
