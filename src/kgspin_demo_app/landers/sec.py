"""SEC EDGAR lander — ``SecLander(DocumentFetcher)``.

Sprint 09 REQ-007: migrated from Sprint 07's sidecar-writing CLI to a
``DocumentFetcher`` subclass. ``fetch()`` returns a ``FetchResult``;
the CLI ``main()`` wraps that into ``CorpusDocumentMetadata`` and
registers via ``HttpResourceRegistryClient`` — no ``metadata.json``
sidecar, admin's registry is authoritative.

Env vars:
- ``SEC_USER_AGENT`` (REQUIRED) — SEC compliance. "Your Name your@email.com"
- ``KGSPIN_CORPUS_ROOT`` (optional) — default ``~/.kgspin/corpus/``
- ``KGSPIN_ADMIN_URL`` (REQUIRED for the CLI) — fail-loud if unset.
  The ``fetch()`` method itself doesn't require admin; only the CLI's
  register step does (per the memo's Connection model).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import ClassVar, Literal, Optional
from xml.etree import ElementTree

import requests
from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    DocumentFetcher,
    FetchConfig,
    FetchResult,
    FetcherError,
    FetcherNotFoundError,
)
from kgspin_interface.resources import (
    CorpusDocumentMetadata,
    FilePointer,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import _shared
from .metadata import build_source_extras, iso_utc_now


LANDER_CLI_NAME = "kgspin-demo-lander-sec"
LANDER_VERSION = "2.1.0"  # Wave A: FetchConfig dual-method adoption

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_VALID_FILING_TYPES = {"10-K", "10-Q", "8-K"}

_SEC_BROWSE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"


class SecFetchConfig(FetchConfig):
    """Typed config for :class:`SecLander`.

    ``extra="forbid"`` (inherited) — unknown keys in a wire-format
    identifier dict raise ``pydantic.ValidationError`` at parse time.
    """

    ticker: str
    form: Literal["10-K", "10-Q", "8-K"] = "10-K"
    output_root: Path | None = None
    date: str | None = None
    user_agent: str | None = None


@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(5),
)
def _get_with_retry(
    url: str,
    *,
    user_agent: str,
    params: Optional[dict] = None,
    stream: bool = False,
    timeout: int = 30,
) -> requests.Response:
    headers = {"User-Agent": user_agent}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout, stream=stream)
    resp.raise_for_status()
    return resp


def _fetch_filing_atom(
    ticker: str,
    filing_type: str,
    user_agent: str,
) -> tuple[str, str, Optional[str]]:
    """Hit EDGAR's Atom feed; return (raw_url, accession, updated_iso)."""
    params = {
        "action": "getcompany",
        "CIK": ticker,
        "type": filing_type,
        "dateb": "",
        "owner": "include",
        "count": 10,
        "output": "atom",
    }
    resp = _get_with_retry(_SEC_BROWSE_URL, user_agent=user_agent, params=params)

    root = ElementTree.fromstring(resp.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise FetcherNotFoundError(
            f"No {filing_type} filings found for ticker {ticker!r}"
        )

    link_elem = entry.find("atom:link", ns)
    link = link_elem.attrib["href"] if link_elem is not None else ""
    if not link:
        raise FetcherError(f"EDGAR Atom entry for {ticker} has no href link")

    category = entry.find("atom:category", ns)
    accession = category.attrib.get("term", "") if category is not None else ""

    updated_elem = entry.find("atom:updated", ns)
    updated = updated_elem.text if updated_elem is not None else None

    if link.endswith("-index.htm"):
        raw_url = link.replace("-index.htm", ".txt")
    else:
        raw_url = link
    return raw_url, accession, updated


class SecLander(DocumentFetcher):
    """SEC EDGAR filing fetcher — dual-method (Wave A / interface 0.8.1).

    Typed path: ``lander.fetch(ticker="JNJ", form="10-K")``.
    Wire path: ``lander.fetch_by_id({"ticker": "JNJ", "form": "10-K"})``.

    The base class validates the wire-format dict against
    :class:`SecFetchConfig` and delegates to ``fetch(**parsed_kwargs)``.
    """

    name = "sec_edgar"
    version = LANDER_VERSION
    contract_version = DOCUMENT_FETCHER_CONTRACT_VERSION
    fetch_config_cls: ClassVar[type[FetchConfig]] = SecFetchConfig

    DOMAIN: ClassVar[str] = "financial"
    SOURCE: ClassVar[str] = "sec_edgar"

    def fetch(
        self,
        *,
        ticker: str,
        form: Literal["10-K", "10-Q", "8-K"] = "10-K",
        output_root: Path | None = None,
        date: str | None = None,
        user_agent: str | None = None,
    ) -> FetchResult:
        # --- identifier validation ---
        ticker = ticker.strip().upper()
        form = form.strip().upper()  # type: ignore[assignment]
        if not _TICKER_RE.fullmatch(ticker):
            raise FetcherError(
                f"SecLander: invalid ticker {ticker!r} "
                f"(expected 1-5 ASCII letters)"
            )
        if form not in _VALID_FILING_TYPES:
            raise FetcherError(
                f"SecLander: invalid form {form!r} "
                f"(expected one of {sorted(_VALID_FILING_TYPES)})"
            )

        # --- auth resolution ---
        # EDGAR_IDENTITY is the pre-existing convention used by the rest
        # of the demo repo (README, demo_ticker.py, pipeline_common.py).
        # SEC_USER_AGENT was introduced in Sprint 07 by mistake and is
        # retained as a fallback so Sprint 07-era scripts keep working.
        resolved_agent = (
            user_agent
            or os.environ.get("EDGAR_IDENTITY", "").strip()
            or os.environ.get("SEC_USER_AGENT", "").strip()
        )
        if not resolved_agent:
            raise FetcherError(
                "SecLander: EDGAR_IDENTITY not provided. "
                "SEC compliance requires 'Your Name your@email.com' in the "
                "User-Agent. Pass user_agent=... or set EDGAR_IDENTITY "
                "(or the legacy SEC_USER_AGENT fallback)."
            )

        # --- path resolution ---
        if output_root is not None:
            resolved_root = Path(output_root).expanduser().resolve()
            resolved_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                resolved_root.chmod(0o700)
            except PermissionError:
                pass
        else:
            resolved_root = _shared.get_corpus_root()

        resolved_date = _shared.validate_date(date or _shared.today_iso_utc())

        raw_path = _shared.default_artifact_path(
            resolved_root,
            domain=self.DOMAIN,
            source=self.SOURCE,
            identifier=ticker,
            date=resolved_date,
            artifact_type=form,
            filename="raw.html",
        )

        # --- fetch ---
        fetch_timestamp_utc = iso_utc_now()
        try:
            raw_url, accession, updated = _fetch_filing_atom(
                ticker, form, resolved_agent,
            )
        except FetcherNotFoundError:
            raise  # surface as-is
        except requests.HTTPError as e:
            raise FetcherError(
                f"SecLander: EDGAR Atom feed HTTP error for {ticker} {form}: "
                f"{e.response.status_code if e.response else '??'}"
            ) from e

        try:
            resp = _get_with_retry(raw_url, user_agent=resolved_agent, stream=True)
            etag = resp.headers.get("etag") or resp.headers.get("ETag")
            http_status = resp.status_code

            bytes_written = _shared.stream_to_file(
                resp.iter_content(chunk_size=_shared.STREAM_CHUNK_BYTES),
                raw_path,
                source_url=raw_url,
            )
        except _shared.DownloadTooLargeError as e:
            raise FetcherError(f"SecLander: {e}") from e
        except requests.HTTPError as e:
            raise FetcherError(
                f"SecLander: failed to download {raw_url}: "
                f"HTTP {e.response.status_code if e.response else '??'}"
            ) from e
        except Exception as e:
            raise FetcherError(f"SecLander: unexpected error: {type(e).__name__}: {e}") from e

        # VP Eng test-eval: hash is computed from the bytes actually written
        # to disk, not an in-memory buffer. sha256_file reads the file.
        sha = _shared.sha256_file(raw_path)

        # source_extras: per-source fields that don't fit the first-class
        # CorpusDocumentMetadata slots (bytes_written, etag, source_url
        # are already first-class).
        extras = build_source_extras(
            lander_name=self.name,
            lander_version=self.version,
            fetch_timestamp_utc=fetch_timestamp_utc,
            http_status=http_status,
            extra_fields={
                "cik": ticker,  # EDGAR accepts ticker as CIK-alias
                "accession": accession,
                "filing_type": form,
                "source_url": raw_url,
                "etag": etag,
                "bytes_written": bytes_written,
                "sec_updated": updated,
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
        description="Fetch a SEC EDGAR filing and register with admin's registry.",
    )
    p.add_argument("--ticker", required=True, help="Stock ticker (1-5 ASCII letters)")
    p.add_argument("--filing", default="10-K", choices=sorted(_VALID_FILING_TYPES))
    p.add_argument("--date", default=_shared.today_iso_utc(),
                   help="Fetch date (YYYY-MM-DD; default: today UTC)")
    p.add_argument("--output-root", default=None,
                   help="Override KGSPIN_CORPUS_ROOT for this invocation.")
    p.add_argument("--skip-registry", action="store_true",
                   help=argparse.SUPPRESS)  # internal-only; used by tests
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = _shared.setup_logging(LANDER_CLI_NAME, verbose=args.verbose)

    # ADR-001: load config.yaml and bridge its values into the legacy
    # env-var surface the rest of this module still reads. No-op when
    # config.yaml is missing would defeat the bootstrap contract, so the
    # helper exits non-zero on first run with actionable guidance.
    from kgspin_demo_app.config import bootstrap_cli
    bootstrap_cli()

    # --- admin env-var enforcement (fail loud before any network) ---
    if not args.skip_registry and not os.environ.get("KGSPIN_ADMIN_URL", "").strip():
        sys.stderr.write(
            "[LANDER_REGISTRY] KGSPIN_ADMIN_URL not set; refusing to silently "
            "skip registration. Set the env var to your admin instance "
            "(default: http://127.0.0.1:8750).\n"
        )
        return 8

    # --- fetch ---
    lander = SecLander()
    try:
        result = lander.fetch(
            ticker=args.ticker,
            form=args.filing,
            output_root=args.output_root,
            date=args.date,
        )
    except FetcherNotFoundError as e:
        sys.stderr.write(f"[LANDER_FETCH] {e}\n")
        return 6
    except FetcherError as e:
        sys.stderr.write(f"[LANDER_FETCH] {e}\n")
        return 5
    except Exception as e:
        logger.exception("Unexpected error in SecLander.fetch")
        sys.stderr.write(f"[LANDER_FETCH] {type(e).__name__}: {e}\n")
        return 7

    # --- register with admin ---
    if not args.skip_registry:
        from kgspin_demo_app.registry_http import HttpResourceRegistryClient
        from datetime import datetime
        client = HttpResourceRegistryClient()
        try:
            extras = result.metadata
            doc_meta = CorpusDocumentMetadata(
                domain="financial",
                source="sec_edgar",
                identifier={"ticker": args.ticker.upper(), "form": args.filing},
                fetch_timestamp=datetime.fromisoformat(
                    extras["fetch_timestamp_utc"].replace("Z", "+00:00")
                ),
                mime_type="text/html",
                bytes_written=extras.get("bytes_written"),
                etag=extras.get("etag"),
                source_url=extras.get("source_url"),
                source_extras={k: v for k, v in extras.items()
                               if k not in {"bytes_written", "etag", "source_url"}},
            )
            try:
                record = client.register_corpus_document(
                    metadata=doc_meta,
                    pointer=result.pointer,
                    actor="fetcher:sec_edgar",
                )
            except RuntimeError as e:
                sys.stderr.write(f"[LANDER_REGISTRY] {e}\n")
                return 9
            logger.info(f"Registered: {record.id}")
        finally:
            client.close()

    # stdout gets the artifact path — downstream pipeline tooling consumes this
    sys.stdout.write(f"{result.pointer.value}\n")
    logger.info(
        f"Landed {args.ticker.upper()} {args.filing}: "
        f"{extras.get('bytes_written', '?')} bytes, sha256={(result.hash or '')[:16]}... "
        f"→ {result.pointer.value}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
