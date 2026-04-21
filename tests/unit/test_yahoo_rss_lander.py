"""Sprint 11 Task 7 — YahooRssLander tests (replaces Sprint 09 yahoo_news tests).

Focus: ADR-004 compliance — no domain in the lander name, runtime
``domain`` arg on ``fetch()``, backend-named CORPUS_DOCUMENT source.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
)
from kgspin_interface.resources import FilePointer


SAMPLE_YAHOO_ARTICLE = {
    "url": "https://finance.yahoo.com/news/msft-q1-deadbeef",
    "title": "MSFT Beats Q1 Estimates",
    "description": "Microsoft reported strong earnings.",
    "content": "",  # Yahoo RSS has no body
    "published_at": "Wed, 15 Apr 2026 14:00:00 GMT",
    "source_name": "Yahoo Finance",
    "author": "",
}


def test_lander_is_a_document_fetcher() -> None:
    from kgspin_demo_app.landers.yahoo_rss import YahooRssLander
    lander = YahooRssLander()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "yahoo_rss"
    assert lander.version == "3.0.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_fetch_happy_path(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.yahoo_rss import YahooRssLander, yahoo_rss_article_id

    article_id = yahoo_rss_article_id(
        url=SAMPLE_YAHOO_ARTICLE["url"], for_date="2026-04-17",
    )
    lander = YahooRssLander()
    result = lander.fetch(
        domain="financial",
        source="yahoo_rss",
        identifier={"article_id": article_id},
        article=SAMPLE_YAHOO_ARTICLE,
        ticker="MSFT",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )

    assert isinstance(result, FetchResult)
    assert isinstance(result.pointer, FilePointer)
    landed = Path(result.pointer.value)
    assert landed.is_file()
    body = landed.read_text()
    assert SAMPLE_YAHOO_ARTICLE["title"] in body
    assert SAMPLE_YAHOO_ARTICLE["description"] in body
    # Path contains the file-safe article id.
    assert article_id.replace(":", "_") in landed.name
    # Metadata pins the Sprint 11 backend-named fields.
    assert result.metadata["article_id"] == article_id
    assert result.metadata["ticker"] == "MSFT"
    assert result.metadata["source_name"] == "Yahoo Finance"
    assert result.metadata["lander_name"] == "yahoo_rss"
    # Hash matches file bytes.
    assert result.hash == hashlib.sha256(landed.read_bytes()).hexdigest()


def test_article_id_does_not_encode_domain() -> None:
    """ADR-004 §1: article IDs must NOT encode the domain — domain is a
    corpus_document field, not part of the backend-scoped ID.
    """
    from kgspin_demo_app.landers.yahoo_rss import yahoo_rss_article_id
    aid = yahoo_rss_article_id(url="https://finance.yahoo.com/x", for_date="2026-04-17")
    assert aid.startswith("yahoo_rss:2026-04-17:")
    assert "financial" not in aid
    assert "clinical" not in aid


def test_missing_article_raises(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.yahoo_rss import YahooRssLander
    lander = YahooRssLander()
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="financial",
            source="yahoo_rss",
            identifier={"article_id": "yahoo_rss:2026-04-17:deadbeef"},
            article=None,  # type: ignore[arg-type]
            ticker="MSFT",
            output_root=tmp_path / "corpus",
        )


def test_invalid_ticker_rejected(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.yahoo_rss import YahooRssLander
    lander = YahooRssLander()
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="financial",
            source="yahoo_rss",
            identifier={"article_id": "yahoo_rss:2026-04-17:deadbeef"},
            article=SAMPLE_YAHOO_ARTICLE,
            ticker="JNJ; rm -rf /",  # injection
            output_root=tmp_path / "corpus",
        )
