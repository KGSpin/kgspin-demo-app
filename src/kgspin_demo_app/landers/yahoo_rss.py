"""Yahoo Finance RSS lander — ``YahooRssLander(DocumentFetcher)``.

Sprint 11 replaces the Sprint 07 misnomer (``YahooNewsLander`` which
actually hit NewsAPI) with a real Yahoo Finance RSS backend.

Per ADR-004 (Backend-named Landers):
- ``name = "yahoo_rss"`` — backend-named, not domain-named.
- ``fetch()`` accepts ``domain`` at runtime; the lander is
  financial-only in current practice (``DOMAIN_FETCHERS["financial"]``)
  but makes no compile-time domain assumption.

Identifier shape: ``{"article_id": "yahoo_rss:<YYYY-MM-DD>:<sha8>"}``.
ADR-004 §1 requires article IDs NOT encode the domain — domain lives
on the CORPUS_DOCUMENT record.

No credentials required — Yahoo's RSS feed is public. VP Sec
credential-leakage sentinel test therefore skips this lander (tracked
in Task 7's parametrization).

Env vars:
- ``KGSPIN_CORPUS_ROOT`` (optional)
- ``KGSPIN_ADMIN_URL`` (REQUIRED for the CLI, per Sprint 09 convention)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
)
from kgspin_interface.resources import (
    CorpusDocumentMetadata,
    FilePointer,
)

from . import _shared
from . import _yahoo_rss_client as _yrss
from .metadata import build_source_extras, iso_utc_now


LANDER_CLI_NAME = "kgspin-demo-lander-yahoo-rss"
LANDER_VERSION = "3.0.0"

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_USER_AGENT = "kgspin-demo-lander-yahoo-rss/3.0.0"
_DEFAULT_LIMIT = 5
_DEFAULT_DOMAIN = "financial"


def yahoo_rss_article_id(*, url: str, for_date: str | None = None) -> str:
    """Return ``yahoo_rss:<YYYY-MM-DD>:<sha8>``.

    ADR-004: no domain baked into the ID. ``for_date`` is the fetch
    day, not the article's publication date — two fetches of the same
    URL on different days produce different ids (normal
    "article re-sampled today" behavior).
    """
    for_date = for_date or _shared.today_iso_utc()
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"yahoo_rss:{for_date}:{digest}"


class YahooRssLander(DocumentFetcher):
    """Real Yahoo Finance RSS fetcher.

    ``identifier`` dict shape:
        ``{"article_id": "yahoo_rss:YYYY-MM-DD:<sha8>"}``

    Required:
    - ``article: dict`` (normal CLI path — pre-discovered RSS entry)

    Optional kwargs:
    - ``output_root: Path | str``
    - ``date: str``
    - ``ticker: str`` — used as per-source identifier directory name
    """

    name = "yahoo_rss"
    version = LANDER_VERSION
    contract_version = DOCUMENT_FETCHER_CONTRACT_VERSION

    def fetch(
        self,
        domain: str,
        source: str,
        identifier: dict[str, str],
        **kwargs: Any,
    ) -> FetchResult:
        article_id = (identifier.get("article_id") or "").strip()
        if not article_id:
            raise FetcherError(
                f"YahooRssLander: identifier must include 'article_id' "
                f"(form 'yahoo_rss:YYYY-MM-DD:<sha8>'). Got: {identifier!r}"
            )
        ticker = (kwargs.get("ticker") or "").strip().upper()
        if ticker and not _TICKER_RE.fullmatch(ticker):
            raise FetcherError(f"YahooRssLander: invalid ticker {ticker!r}")

        article = kwargs.get("article")
        if not isinstance(article, dict):
            raise FetcherError(
                "YahooRssLander: must provide 'article' dict kwarg "
                "(discovery happens in CLI main(), not fetch())"
            )
        url = (article.get("url") or "").strip()
        if not url:
            raise FetcherError("YahooRssLander: article dict has no 'url' field")

        raw_output_root = kwargs.get("output_root")
        if raw_output_root:
            output_root = Path(raw_output_root).expanduser().resolve()
            output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                output_root.chmod(0o700)
            except PermissionError:
                pass
        else:
            output_root = _shared.get_corpus_root()

        date = _shared.validate_date(kwargs.get("date") or _shared.today_iso_utc())

        identifier_dir = ticker or "none"
        safe_leaf = article_id.replace(":", "_")
        raw_path = _shared.default_artifact_path(
            output_root,
            domain=domain,
            source=source,
            identifier=identifier_dir,
            date=date,
            artifact_type="news",
            filename=f"{safe_leaf}.raw.txt",
        )

        fetch_timestamp_utc = iso_utc_now()

        try:
            bytes_written = _shared.stream_to_file(
                _yrss.article_body_bytes_iter(article),
                raw_path,
                source_url=url,
            )
        except _shared.DownloadTooLargeError as e:
            raise FetcherError(f"YahooRssLander: {e}") from e
        except Exception as e:
            raise FetcherError(
                f"YahooRssLander: unexpected error writing article: "
                f"{type(e).__name__}: {e}"
            ) from e

        sha = _shared.sha256_file(raw_path)

        extras = build_source_extras(
            lander_name=self.name,
            lander_version=self.version,
            fetch_timestamp_utc=fetch_timestamp_utc,
            http_status=200,
            extra_fields={
                "article_id": article_id,
                "ticker": ticker or None,
                "source_url": url,
                "source_name": article.get("source_name") or "Yahoo Finance",
                "published_at": article.get("published_at") or "",
                "title": article.get("title") or "",
                "bytes_written": bytes_written,
            },
        )
        return FetchResult(
            pointer=FilePointer(value=str(raw_path)),
            metadata=extras,
            hash=sha,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=LANDER_CLI_NAME,
        description="Fetch Yahoo Finance RSS headlines (one FetchResult per entry).",
    )
    p.add_argument("--ticker", required=True, help="Uppercase ticker, 1-5 chars")
    p.add_argument(
        "--domain",
        default=_DEFAULT_DOMAIN,
        help=f"Corpus domain for registry records (default: {_DEFAULT_DOMAIN!r})",
    )
    p.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    p.add_argument("--date", default=_shared.today_iso_utc())
    p.add_argument("--output-root", default=None)
    p.add_argument("--skip-registry", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = _shared.setup_logging(LANDER_CLI_NAME, verbose=args.verbose)

    # ADR-001: bootstrap config.yaml + bridge to legacy env surface.
    from kgspin_demo_app.config import bootstrap_cli
    bootstrap_cli()

    ticker = args.ticker.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        sys.stderr.write(
            f"[LANDER_INPUT] Invalid ticker {args.ticker!r}. Expected 1-5 ASCII letters.\n"
        )
        return 2
    try:
        date = _shared.validate_date(args.date)
    except ValueError as e:
        sys.stderr.write(f"[LANDER_INPUT] {e}\n")
        return 2
    if not (1 <= args.limit <= 50):
        sys.stderr.write(f"[LANDER_INPUT] --limit must be 1..50; got {args.limit}\n")
        return 2

    if not args.skip_registry and not os.environ.get("KGSPIN_ADMIN_URL", "").strip():
        sys.stderr.write(
            "[LANDER_REGISTRY] KGSPIN_ADMIN_URL not set; refusing to silently skip registration.\n"
        )
        return 8

    try:
        articles = _yrss.query_yahoo_rss(_USER_AGENT, ticker, args.limit, logger)
    except _yrss.YahooRssTimeout as e:
        sys.stderr.write(f"[LANDER_NET] {e}\n")
        return 5
    except _yrss.YahooRssFeedTooLarge as e:
        sys.stderr.write(f"[LANDER_NET] {e}\n")
        return 5
    except _yrss.YahooRssMalformed as e:
        sys.stderr.write(f"[LANDER_NET] {e}\n")
        return 5
    except _yrss.YahooRssError as e:
        sys.stderr.write(f"[LANDER_NET] {e}\n")
        return 5

    if not articles:
        sys.stderr.write(f"[LANDER_FETCH] No Yahoo RSS entries for {ticker}\n")
        return 6

    lander = YahooRssLander()
    registry_client = None
    if not args.skip_registry:
        from kgspin_demo_app.registry_http import HttpResourceRegistryClient
        registry_client = HttpResourceRegistryClient()

    landed = 0
    try:
        for article in articles:
            url = article.get("url") or ""
            if not url:
                continue
            article_id = yahoo_rss_article_id(url=url, for_date=date)
            try:
                result = lander.fetch(
                    domain=args.domain,
                    source="yahoo_rss",
                    identifier={"article_id": article_id},
                    article=article,
                    ticker=ticker,
                    output_root=args.output_root,
                    date=date,
                )
            except FetcherError as e:
                sys.stderr.write(f"[LANDER_FETCH] skipping {url}: {e}\n")
                continue

            if registry_client is not None:
                from datetime import datetime
                extras = result.metadata
                doc_meta = CorpusDocumentMetadata(
                    domain=args.domain,
                    source="yahoo_rss",
                    identifier={"article_id": article_id, "ticker": ticker},
                    fetch_timestamp=datetime.fromisoformat(
                        extras["fetch_timestamp_utc"].replace("Z", "+00:00")
                    ),
                    mime_type="text/plain",
                    bytes_written=extras.get("bytes_written"),
                    etag=None,
                    source_url=extras.get("source_url"),
                    source_extras={k: v for k, v in extras.items()
                                   if k not in {"bytes_written", "source_url"}},
                )
                try:
                    record = registry_client.register_corpus_document(
                        metadata=doc_meta,
                        pointer=result.pointer,
                        actor="fetcher:yahoo_rss",
                    )
                except RuntimeError as e:
                    sys.stderr.write(f"[LANDER_REGISTRY] skipping {url}: {e}\n")
                    continue
                logger.info(f"Registered: {record.id}")
            sys.stdout.write(f"{result.pointer.value}\n")
            landed += 1
    finally:
        if registry_client is not None:
            registry_client.close()

    if landed == 0:
        sys.stderr.write(f"[LANDER_FETCH] All {len(articles)} entries failed to land\n")
        return 7
    logger.info(f"Landed {landed}/{len(articles)} Yahoo RSS entries for {ticker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
