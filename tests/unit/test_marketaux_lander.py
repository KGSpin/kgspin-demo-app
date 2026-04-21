"""Sprint 11 Task 7 — MarketauxLander tests.

Covers the ADR-004 invariants on the Sprint 11 Marketaux backend:
``name = "marketaux"``, domain as runtime arg, MARKETAUX_API_KEY
credential hygiene.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest
from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
)
from kgspin_interface.resources import FilePointer


SENTINEL_MARKETAUX_KEY = "SENTINEL-DO-NOT-LEAK-ffffffff-MARKETAUX"

SAMPLE_MARKETAUX_ARTICLE = {
    "url": "https://www.marketaux.com/news/msft-earnings",
    "title": "MSFT Q1 Beats",
    "description": "Microsoft posts record quarterly earnings.",
    "content": "",  # Marketaux doesn't ship full content in news/all
    "published_at": "2026-04-15T14:00:00Z",
    "source_name": "Reuters via Marketaux",
    "tickers": ["MSFT", "AAPL"],
    "keywords": ["earnings", "guidance"],
}


def test_lander_is_a_document_fetcher() -> None:
    from kgspin_demo_app.landers.marketaux import MarketauxLander
    lander = MarketauxLander()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "marketaux"
    assert lander.version == "1.0.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_fetch_happy_path(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.marketaux import (
        MarketauxLander, marketaux_article_id,
    )
    article_id = marketaux_article_id(
        url=SAMPLE_MARKETAUX_ARTICLE["url"], for_date="2026-04-17",
    )
    lander = MarketauxLander()
    result = lander.fetch(
        domain="financial",
        source="marketaux",
        identifier={"article_id": article_id},
        article=SAMPLE_MARKETAUX_ARTICLE,
        ticker="MSFT",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )
    assert isinstance(result, FetchResult)
    landed = Path(result.pointer.value)
    body = landed.read_text()
    assert SAMPLE_MARKETAUX_ARTICLE["title"] in body
    assert SAMPLE_MARKETAUX_ARTICLE["description"] in body
    # Keywords + tickers serialized into extras.
    assert result.metadata["keywords"] == "earnings,guidance"
    assert result.metadata["related_tickers"] == "MSFT,AAPL"
    assert result.metadata["lander_name"] == "marketaux"
    assert result.hash == hashlib.sha256(landed.read_bytes()).hexdigest()


def test_article_id_does_not_encode_domain() -> None:
    from kgspin_demo_app.landers.marketaux import marketaux_article_id
    aid = marketaux_article_id(url="https://x.com/a", for_date="2026-04-17")
    assert aid.startswith("marketaux:2026-04-17:")
    assert "financial" not in aid
    assert "clinical" not in aid


def test_invalid_ticker_rejected(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.marketaux import MarketauxLander
    lander = MarketauxLander()
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="financial",
            source="marketaux",
            identifier={"article_id": "marketaux:2026-04-17:deadbeef"},
            article=SAMPLE_MARKETAUX_ARTICLE,
            ticker="MSFT; rm -rf /",  # injection
            output_root=tmp_path / "corpus",
        )


def test_missing_article_rejected(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.marketaux import MarketauxLander
    lander = MarketauxLander()
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="financial",
            source="marketaux",
            identifier={"article_id": "marketaux:2026-04-17:deadbeef"},
            article=None,  # type: ignore[arg-type]
            ticker="MSFT",
            output_root=tmp_path / "corpus",
        )


def test_credential_never_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture,
) -> None:
    """VP Sec sentinel: MARKETAUX_API_KEY must never surface in the
    landed artifact, metadata, FetchResult dump, logs, path, or stdout.
    """
    from kgspin_demo_app.landers.marketaux import (
        MarketauxLander, marketaux_article_id,
    )

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("MARKETAUX_API_KEY", SENTINEL_MARKETAUX_KEY)

    article_id = marketaux_article_id(
        url=SAMPLE_MARKETAUX_ARTICLE["url"], for_date="2026-04-17",
    )
    lander = MarketauxLander()
    result = lander.fetch(
        domain="financial",
        source="marketaux",
        identifier={"article_id": article_id},
        article=SAMPLE_MARKETAUX_ARTICLE,
        ticker="MSFT",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )

    landed = Path(result.pointer.value)
    assert SENTINEL_MARKETAUX_KEY not in landed.read_text()
    assert SENTINEL_MARKETAUX_KEY not in json.dumps(result.metadata)
    assert SENTINEL_MARKETAUX_KEY not in result.model_dump_json()
    assert SENTINEL_MARKETAUX_KEY not in str(landed)
    for record in caplog.records:
        assert SENTINEL_MARKETAUX_KEY not in record.getMessage()
    captured = capsys.readouterr()
    assert SENTINEL_MARKETAUX_KEY not in captured.out
    assert SENTINEL_MARKETAUX_KEY not in captured.err
