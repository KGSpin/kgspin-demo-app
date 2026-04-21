"""Sprint 11 Task 3/7 — Yahoo RSS client resource-cap tests (VP Eng MAJOR).

Enforced caps (per ADR-004 + VP Eng Phase 1 review):

- 15s timeout → ``YahooRssTimeout``
- 5 MiB response body cap → ``YahooRssFeedTooLarge``
- 100-entry post-parse cap
- malformed XML with zero entries → ``YahooRssMalformed``
"""

from __future__ import annotations

import logging

import pytest
import requests


_MINIMAL_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>MSFT News</title>
<item><title>Headline A</title><link>https://x.com/a</link>
<description>Body A</description><pubDate>Wed, 15 Apr 2026 14:00:00 GMT</pubDate></item>
<item><title>Headline B</title><link>https://x.com/b</link>
<description>Body B</description><pubDate>Wed, 15 Apr 2026 14:05:00 GMT</pubDate></item>
</channel></rss>"""


class _FakeResponse:
    def __init__(self, status_code: int, chunks: list[bytes] | None = None,
                 raise_timeout: bool = False) -> None:
        self.status_code = status_code
        self._chunks = chunks or []
        self._raise_timeout = raise_timeout

    def iter_content(self, chunk_size: int = 64 * 1024):
        if self._raise_timeout:
            raise requests.Timeout("synthetic timeout")
        for ch in self._chunks:
            yield ch

    def close(self) -> None:
        pass


def _patch_get(monkeypatch, response_or_exc) -> None:
    """Patch requests.get used by _yahoo_rss_client."""
    from kgspin_demo_app.landers import _yahoo_rss_client as _yrss

    def fake_get(*args, **kwargs):
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        return response_or_exc

    monkeypatch.setattr(_yrss.requests, "get", fake_get)


def test_successful_small_feed(monkeypatch, caplog) -> None:
    from kgspin_demo_app.landers._yahoo_rss_client import query_yahoo_rss

    resp = _FakeResponse(200, chunks=[_MINIMAL_RSS])
    _patch_get(monkeypatch, resp)

    caplog.set_level(logging.DEBUG)
    articles = query_yahoo_rss("ua/1.0", "MSFT", 5, logging.getLogger("test"))

    assert len(articles) == 2
    assert articles[0]["url"] == "https://x.com/a"
    assert articles[0]["title"] == "Headline A"
    assert articles[0]["source_name"] == "Yahoo Finance"
    assert articles[0]["content"] == ""


def test_size_cap_aborts_download(monkeypatch) -> None:
    from kgspin_demo_app.landers._yahoo_rss_client import (
        YahooRssFeedTooLarge,
        query_yahoo_rss,
        FEED_MAX_BYTES,
    )

    # 6 MiB of dummy XML — exceeds the 5 MiB cap.
    oversize = [b"<?xml version='1.0'?><rss><channel>",
                b"x" * (FEED_MAX_BYTES + 1_000),
                b"</channel></rss>"]
    resp = _FakeResponse(200, chunks=oversize)
    _patch_get(monkeypatch, resp)

    with pytest.raises(YahooRssFeedTooLarge):
        query_yahoo_rss("ua/1.0", "MSFT", 5, logging.getLogger("test"))


def test_timeout_raises_typed_error(monkeypatch) -> None:
    from kgspin_demo_app.landers._yahoo_rss_client import (
        YahooRssTimeout, query_yahoo_rss,
    )

    _patch_get(monkeypatch, requests.Timeout("synthetic timeout"))
    with pytest.raises(YahooRssTimeout):
        query_yahoo_rss("ua/1.0", "MSFT", 5, logging.getLogger("test"))


def test_non_200_status_raises(monkeypatch) -> None:
    from kgspin_demo_app.landers._yahoo_rss_client import (
        YahooRssError, query_yahoo_rss,
    )

    resp = _FakeResponse(500, chunks=[b"server error"])
    _patch_get(monkeypatch, resp)
    with pytest.raises(YahooRssError):
        query_yahoo_rss("ua/1.0", "MSFT", 5, logging.getLogger("test"))


def test_entry_count_cap(monkeypatch) -> None:
    """Even if a feed ships 200 entries, ``limit`` + ``FEED_MAX_ENTRIES``
    clamp output to 100 max. With ``limit=50`` the clamp becomes 50.
    """
    from kgspin_demo_app.landers._yahoo_rss_client import query_yahoo_rss

    items = b"".join(
        f'<item><title>t{i}</title><link>https://x.com/{i}</link>'
        f'<description>d</description></item>'.encode()
        for i in range(200)
    )
    feed = b"<?xml version='1.0'?><rss><channel>" + items + b"</channel></rss>"
    resp = _FakeResponse(200, chunks=[feed])
    _patch_get(monkeypatch, resp)

    articles = query_yahoo_rss("ua/1.0", "MSFT", 50, logging.getLogger("test"))
    assert len(articles) == 50


def test_malformed_feed_with_no_entries(monkeypatch) -> None:
    from kgspin_demo_app.landers._yahoo_rss_client import (
        YahooRssMalformed, query_yahoo_rss,
    )

    # Hard XML error + no entries → YahooRssMalformed.
    resp = _FakeResponse(200, chunks=[b"this is not xml at all <<< >>>"])
    _patch_get(monkeypatch, resp)
    with pytest.raises(YahooRssMalformed):
        query_yahoo_rss("ua/1.0", "MSFT", 5, logging.getLogger("test"))
