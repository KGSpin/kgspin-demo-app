"""Sprint 11 Task 7 — cross-domain news lander test (ADR-004 headline invariant).

Pins the core ADR-004 claim: a single ``NewsApiLander`` instance,
invoked against ``domain="financial"`` and ``domain="clinical"``,
produces two distinct CORPUS_DOCUMENT records whose ``domain`` fields
diverge — while the fetcher_id on the FETCHER side remains a single
``"newsapi"`` record.

If this test ever starts needing two separate lander instances or two
separate ``name`` attributes to pass, the ADR-004 anti-pattern has
regressed. Fail hard.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kgspin_interface.resources import FilePointer


SAMPLE_ARTICLE_FIN = {
    "url": "https://x.com/msft-earnings",
    "title": "MSFT Beats",
    "description": "MSFT Q1 earnings.",
    "content": "Microsoft reported record Q1.",
    "published_at": "2026-04-15T14:00:00Z",
    "source_name": "Wire",
    "author": "Jane",
}

SAMPLE_ARTICLE_CLI = {
    "url": "https://x.com/semaglutide-trial",
    "title": "Semaglutide Reduces Weight",
    "description": "Phase 3 clinical trial results.",
    "content": "Participants lost 15% body weight.",
    "published_at": "2026-04-15T14:00:00Z",
    "source_name": "Wire",
    "author": "Pat",
}


def test_same_lander_serves_financial_and_clinical(tmp_path: Path) -> None:
    """One lander instance → two corpus_document records with distinct
    ``domain`` fields. The lander's ``name`` attribute is ``"newsapi"``
    in both paths (ADR-004 §1).
    """
    from kgspin_demo_app.landers.newsapi import NewsApiLander, newsapi_article_id

    lander = NewsApiLander()
    assert lander.name == "newsapi"

    fin_aid = newsapi_article_id(
        url=SAMPLE_ARTICLE_FIN["url"], for_date="2026-04-17",
    )
    cli_aid = newsapi_article_id(
        url=SAMPLE_ARTICLE_CLI["url"], for_date="2026-04-17",
    )

    fin_result = lander.fetch(
        domain="financial",
        source="newsapi",
        identifier={"article_id": fin_aid},
        article=SAMPLE_ARTICLE_FIN,
        query="MSFT earnings",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )
    cli_result = lander.fetch(
        domain="clinical",
        source="newsapi",
        identifier={"article_id": cli_aid},
        article=SAMPLE_ARTICLE_CLI,
        query="semaglutide",
        output_root=tmp_path / "corpus",
        date="2026-04-17",
    )

    # 1. Both landed.
    assert isinstance(fin_result.pointer, FilePointer)
    assert isinstance(cli_result.pointer, FilePointer)
    assert Path(fin_result.pointer.value).is_file()
    assert Path(cli_result.pointer.value).is_file()

    # 2. Paths carry distinct domains.
    assert "/financial/" in str(fin_result.pointer.value)
    assert "/clinical/" in str(cli_result.pointer.value)

    # 3. Lander name is the SAME — backend, not domain.
    assert fin_result.metadata["lander_name"] == "newsapi"
    assert cli_result.metadata["lander_name"] == "newsapi"

    # 4. Article IDs don't encode domain.
    assert "financial" not in fin_aid
    assert "clinical" not in cli_aid


def test_single_fetcher_registers_once_for_multi_domain(tmp_path: Path) -> None:
    """ADR-004 §2: FETCHER record is one-per-backend. Even though
    ``newsapi`` serves both financial and clinical, the registry has a
    single ``newsapi`` FETCHER entry — ``register_all`` emits it once.
    """
    from kgspin_demo_app.cli.register_fetchers import register_all
    from kgspin_interface.registry_client import ResourceKind
    from tests.fakes.registry_client import FakeRegistryClient

    fake = FakeRegistryClient()
    ids = register_all(fake)

    # Sprint 11: 5 fetchers (sec_edgar, clinicaltrials_gov, marketaux,
    # yahoo_rss, newsapi) — each registered exactly once.
    assert len(ids) == 5
    assert len(set(ids)) == 5
    fetcher_ids = {
        r.metadata.get("spec", {}).get("fetcher_id")
        for r in fake.list(ResourceKind.FETCHER)
    }
    assert fetcher_ids == {
        "sec_edgar", "clinicaltrials_gov", "marketaux", "yahoo_rss", "newsapi",
    }

    # Calling fetch() under both domains should NOT add new FETCHER
    # records — the fetcher is domain-agnostic per ADR-004 §2.
    from kgspin_demo_app.landers.newsapi import NewsApiLander, newsapi_article_id
    lander = NewsApiLander()
    for domain in ("financial", "clinical"):
        lander.fetch(
            domain=domain,
            source="newsapi",
            identifier={"article_id": newsapi_article_id(
                url=f"https://x.com/{domain}", for_date="2026-04-17",
            )},
            article={**SAMPLE_ARTICLE_FIN, "url": f"https://x.com/{domain}"},
            query="test",
            output_root=tmp_path / "corpus",
            date="2026-04-17",
        )

    # FETCHER count unchanged.
    assert len(fake.list(ResourceKind.FETCHER)) == 5
