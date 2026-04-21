"""Sprint 11 Task 7 — NewsApiLander tests (domain-agnostic per ADR-004).

Replaces Sprint 09's HealthNewsLander tests. The Sprint 11 lander has
``name = "newsapi"`` and accepts ``domain`` as a runtime argument.
Tests cover the financial + clinical paths against the same lander
instance to pin ADR-004's "one lander, many domains" invariant.
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


SENTINEL_NEWSAPI_KEY = "SENTINEL-DO-NOT-LEAK-ffffffff-NEWSAPI-KEY"

SAMPLE_ARTICLE = {
    "url": "https://example.com/news/deadbeef",
    "title": "MSFT Q1 Earnings",
    "description": "Microsoft beat estimates.",
    "content": "Microsoft reported strong Q1 earnings.",
    "published_at": "2026-04-15T14:00:00Z",
    "source_name": "Example Wire",
    "author": "Jane Reporter",
}


def test_lander_is_a_document_fetcher() -> None:
    from kgspin_demo_app.landers.newsapi import NewsApiLander
    lander = NewsApiLander()
    assert isinstance(lander, DocumentFetcher)
    assert lander.name == "newsapi"
    assert lander.version == "3.0.0"
    assert lander.contract_version == DOCUMENT_FETCHER_CONTRACT_VERSION


def test_fetch_financial_happy_path(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.newsapi import NewsApiLander, newsapi_article_id

    article_id = newsapi_article_id(url=SAMPLE_ARTICLE["url"], for_date="2026-04-17")
    lander = NewsApiLander()
    result = lander.fetch(
        domain="financial",
        source="newsapi",
        identifier={"article_id": article_id},
        article=SAMPLE_ARTICLE,
        query="MSFT earnings",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )
    assert isinstance(result, FetchResult)
    landed = Path(result.pointer.value)
    assert landed.is_file()
    # Path includes `/financial/` segment from domain.
    assert "/financial/" in str(landed)
    assert result.metadata["article_id"] == article_id
    assert result.metadata["query"] == "MSFT earnings"
    assert result.metadata["lander_name"] == "newsapi"
    assert result.hash == hashlib.sha256(landed.read_bytes()).hexdigest()


def test_fetch_clinical_happy_path(tmp_path: Path) -> None:
    """ADR-004 §2: same lander instance, domain=clinical produces a
    clinical-domain landed path + corpus_document metadata.
    """
    from kgspin_demo_app.landers.newsapi import NewsApiLander, newsapi_article_id

    article_id = newsapi_article_id(url=SAMPLE_ARTICLE["url"], for_date="2026-04-17")
    lander = NewsApiLander()
    result = lander.fetch(
        domain="clinical",
        source="newsapi",
        identifier={"article_id": article_id},
        article=SAMPLE_ARTICLE,
        query="semaglutide",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )
    landed = Path(result.pointer.value)
    assert "/clinical/" in str(landed)
    assert result.metadata["query"] == "semaglutide"


def test_article_id_does_not_encode_domain() -> None:
    """ADR-004: the ID shape is ``newsapi:YYYY-MM-DD:<sha8>`` — no
    financial/clinical tag baked in.
    """
    from kgspin_demo_app.landers.newsapi import newsapi_article_id
    aid = newsapi_article_id(url="https://x.com/a", for_date="2026-04-17")
    assert aid.startswith("newsapi:2026-04-17:")
    assert "financial" not in aid
    assert "clinical" not in aid
    assert "health" not in aid


def test_invalid_query_rejected(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.newsapi import NewsApiLander
    lander = NewsApiLander()
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="clinical",
            source="newsapi",
            identifier={"article_id": "newsapi:2026-04-17:deadbeef"},
            article=SAMPLE_ARTICLE,
            query="<script>alert('xss')</script>",
            output_root=tmp_path / "corpus",
        )


def test_missing_article_id_rejected(tmp_path: Path) -> None:
    from kgspin_demo_app.landers.newsapi import NewsApiLander
    lander = NewsApiLander()
    with pytest.raises(FetcherError):
        lander.fetch(
            domain="financial",
            source="newsapi",
            identifier={},
            article=SAMPLE_ARTICLE,
            query="MSFT",
            output_root=tmp_path / "corpus",
        )


def test_credential_never_leaks_into_artifact_or_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture,
) -> None:
    """VP Sec sentinel: the NEWSAPI_KEY never appears in the landed
    artifact, metadata, serialized FetchResult, logs, or stdout.
    Parametrized across the newsapi lander (MARKETAUX_API_KEY is
    covered separately; Yahoo RSS has no credential to leak).
    """
    from kgspin_demo_app.landers.newsapi import NewsApiLander, newsapi_article_id

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("NEWSAPI_KEY", SENTINEL_NEWSAPI_KEY)

    article_id = newsapi_article_id(url=SAMPLE_ARTICLE["url"], for_date="2026-04-17")
    lander = NewsApiLander()
    result = lander.fetch(
        domain="financial",
        source="newsapi",
        identifier={"article_id": article_id},
        article=SAMPLE_ARTICLE,
        query="MSFT",
        api_key=SENTINEL_NEWSAPI_KEY,
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )

    landed = Path(result.pointer.value)
    assert SENTINEL_NEWSAPI_KEY not in landed.read_text()
    assert SENTINEL_NEWSAPI_KEY not in json.dumps(result.metadata)
    assert SENTINEL_NEWSAPI_KEY not in result.model_dump_json()
    assert SENTINEL_NEWSAPI_KEY not in str(landed)
    for record in caplog.records:
        assert SENTINEL_NEWSAPI_KEY not in record.getMessage()
    captured = capsys.readouterr()
    assert SENTINEL_NEWSAPI_KEY not in captured.out
    assert SENTINEL_NEWSAPI_KEY not in captured.err
