"""NewsAPI.org lander — ``NewsApiLander(DocumentFetcher)``.

Sprint 11 collapses Sprint 09's ``YahooNewsLander`` (name=
"newsapi_financial") and ``HealthNewsLander`` (name="newsapi_health")
into a **single** backend-named lander per ADR-004. ``domain`` is a
runtime argument; the same registered fetcher serves any domain
listed for it in ``DOMAIN_FETCHERS``.

Identifier shape: ``{"article_id": "newsapi:<YYYY-MM-DD>:<sha8>"}``.
Per ADR-004 §1 the domain is NOT encoded in the ID — domain is
carried by the CORPUS_DOCUMENT record.

Env vars:
- ``NEWSAPI_KEY`` (REQUIRED) — read only from env / kwarg; never
  logged or serialized (VP Sec credential-leakage sentinel).
- ``KGSPIN_CORPUS_ROOT`` (optional)
- ``KGSPIN_ADMIN_URL`` (REQUIRED for the CLI)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchResult,
    FetcherError,
    FetcherNotFoundError,
)
from kgspin_interface.resources import (
    CorpusDocumentMetadata,
    FilePointer,
)

from . import _shared
from . import _newsapi_client as _newsapi
from .metadata import build_source_extras, iso_utc_now


LANDER_CLI_NAME = "kgspin-demo-lander-newsapi"
LANDER_VERSION = "3.0.0"

_QUERY_RE = re.compile(r"^[A-Za-z0-9 _\-]{1,100}$")
_USER_AGENT = "kgspin-demo-lander-newsapi/3.0.0"
_DEFAULT_LIMIT = 5
_DEFAULT_DOMAIN = "clinical"  # demo default — overridable via --domain


def newsapi_article_id(*, url: str, for_date: str | None = None) -> str:
    """Return ``newsapi:<YYYY-MM-DD>:<sha8>``.

    ADR-004: no domain baked into the ID. ``for_date`` is the fetch
    day, not the article's publication date.
    """
    for_date = for_date or _shared.today_iso_utc()
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"newsapi:{for_date}:{digest}"


def _query_identifier(query: str) -> str:
    """Filesystem-safe deterministic id derived from the query."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", query.lower()).strip("-")[:40] or "q"
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


class NewsApiLander(DocumentFetcher):
    """NewsAPI.org-backed lander; domain-agnostic per ADR-004.

    ``identifier`` dict shape:
        ``{"article_id": "newsapi:YYYY-MM-DD:<sha8>"}``

    Required one of:
    - ``article: dict`` (normal CLI path)
    - ``url: str``   (fallback — triggers a single-article lookup)

    Optional kwargs:
    - ``api_key: str`` — override ``$NEWSAPI_KEY``
    - ``output_root: Path | str``
    - ``date: str``
    - ``query: str`` — used as per-source identifier directory name
    """

    name = "newsapi"
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
                "NewsApiLander: identifier must include 'article_id' "
                "(form 'newsapi:YYYY-MM-DD:<sha8>')"
            )
        query = (kwargs.get("query") or "").strip()
        if query and not _QUERY_RE.fullmatch(query):
            raise FetcherError(
                f"NewsApiLander: invalid query {query!r} — expected 1-100 "
                "chars, alphanumerics / spaces / _- only"
            )

        api_key = kwargs.get("api_key") or os.environ.get("NEWSAPI_KEY", "").strip()

        article: Optional[dict] = kwargs.get("article")
        if article is None:
            url = (kwargs.get("url") or "").strip()
            if not url:
                raise FetcherError(
                    "NewsApiLander: must provide 'article' dict or 'url' kwarg"
                )
            if not api_key:
                raise FetcherError(
                    "NewsApiLander: NEWSAPI_KEY not provided; cannot fallback-lookup URL"
                )
            try:
                import logging
                hits = _newsapi.query_newsapi(
                    api_key, _USER_AGENT, url, limit=1,
                    logger=logging.getLogger(self.name),
                )
            except _newsapi.NewsApiError as e:
                raise FetcherError(f"NewsApiLander: {e}") from e
            if not hits:
                raise FetcherNotFoundError(
                    f"NewsApiLander: no article for URL {url!r}"
                )
            article = hits[0]

        url = article.get("url") or ""
        if not url:
            raise FetcherError("NewsApiLander: article has no 'url' field")

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

        identifier_dir = _query_identifier(query) if query else "none"
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
                _newsapi.article_body_bytes_iter(article),
                raw_path,
                source_url=url,
            )
        except _shared.DownloadTooLargeError as e:
            raise FetcherError(f"NewsApiLander: {e}") from e
        except Exception as e:
            raise FetcherError(
                f"NewsApiLander: unexpected error writing article: "
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
                "query": query or None,
                "source_url": url,
                "source_name": article.get("source_name") or "",
                "published_at": article.get("published_at") or "",
                "author": article.get("author") or "",
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
        description=(
            "Fetch news via NewsAPI (one FetchResult per article). "
            "Domain-agnostic per ADR-004: pass --domain to register "
            "corpus_document records under the caller's domain."
        ),
    )
    p.add_argument("--query", required=True, help="NewsAPI /v2/everything q string")
    p.add_argument(
        "--domain",
        default=_DEFAULT_DOMAIN,
        help=(
            f"Corpus domain for registry records (default: {_DEFAULT_DOMAIN!r}; "
            "use 'financial' or 'clinical' per DOMAIN_FETCHERS)"
        ),
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

    query = args.query.strip()
    if not _QUERY_RE.fullmatch(query):
        sys.stderr.write(
            f"[LANDER_INPUT] Invalid --query {args.query!r}. "
            "Expected 1-100 chars, alphanumerics / spaces / _- only.\n"
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

    api_key = _shared.require_env_var(
        "NEWSAPI_KEY",
        hint="Get a free key at https://newsapi.org/register, then export NEWSAPI_KEY=...",
    )
    if not args.skip_registry and not os.environ.get("KGSPIN_ADMIN_URL", "").strip():
        sys.stderr.write(
            "[LANDER_REGISTRY] KGSPIN_ADMIN_URL not set; refusing to silently skip registration.\n"
        )
        return 8

    try:
        articles = _newsapi.query_newsapi(api_key, _USER_AGENT, query, args.limit, logger)
    except _newsapi.NewsApiError as e:
        sys.stderr.write(f"[LANDER_NET] {e}\n")
        return 5

    if not articles:
        sys.stderr.write(f"[LANDER_FETCH] No NewsAPI results for query {query!r}\n")
        return 6

    lander = NewsApiLander()
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
            article_id = newsapi_article_id(url=url, for_date=date)
            try:
                result = lander.fetch(
                    domain=args.domain,
                    source="newsapi",
                    identifier={"article_id": article_id},
                    article=article,
                    query=query,
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
                    source="newsapi",
                    identifier={"article_id": article_id, "query": query},
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
                        actor="fetcher:newsapi",
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
        sys.stderr.write(f"[LANDER_FETCH] All {len(articles)} article(s) failed to land\n")
        return 7
    logger.info(
        f"Landed {landed}/{len(articles)} NewsAPI articles "
        f"(domain={args.domain!r} query={query!r})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
