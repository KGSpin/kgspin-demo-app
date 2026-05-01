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

from . import _shared
from .metadata import build_source_extras, iso_utc_now


LANDER_CLI_NAME = "kgspin-demo-lander-sec"
LANDER_VERSION = "3.0.0"  # 2026-04-22: edgartools restore — filing.html() primary doc only + full Company metadata (was 2.1.0 Wave A raw-HTTP submission-bundle)

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_VALID_FILING_TYPES = {"10-K", "10-Q", "8-K"}


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


def _safe(obj, attr: str):
    """Return getattr(obj, attr) or None — edgartools attrs are best-effort."""
    try:
        val = getattr(obj, attr, None)
        if val is None:
            return None
        if isinstance(val, (str, int, float, bool, list, dict)):
            return val
        return str(val)
    except Exception:
        return None


def _safe_address(addr) -> Optional[dict]:
    """Flatten an edgartools Address object to {street1, street2, city, state_or_country, zipcode}."""
    if addr is None:
        return None
    try:
        return {
            "street1": _safe(addr, "street1"),
            "street2": _safe(addr, "street2"),
            "city": _safe(addr, "city"),
            "state_or_country": _safe(addr, "state_or_country") or _safe(addr, "state"),
            "zipcode": _safe(addr, "zipcode") or _safe(addr, "zip"),
        }
    except Exception:
        return None


def _fetch_filing_via_edgartools(
    ticker: str,
    form: str,
    user_agent: str,
) -> tuple[str, dict, dict]:
    """Fetch the most-recent filing via edgartools.

    Returns ``(primary_html, filing_extras, company_extras)``:

    - ``primary_html`` — the primary 10-K/10-Q/8-K document HTML only
      (no exhibits, no XBRL, no graphics). Matches the old pre-Wave-A
      extraction shape.
    - ``filing_extras`` — accession_number, filing_date, filing_url,
      cik (real number, not the ticker), company_name_as_filed,
      period_of_report.
    - ``company_extras`` — canonical name, cik, sic, industry,
      business_category, fiscal_year_end, business/mailing addresses,
      tickers list, filer classifications.

    Raises :class:`FetcherError` / :class:`FetcherNotFoundError`
    mirroring the old raw-HTTP path.
    """
    try:
        import edgar as edgartools
    except ImportError as e:
        raise FetcherError(
            "SecLander: edgartools is required. "
            "Install with: pip install edgartools>=2.0"
        ) from e

    try:
        edgartools.set_identity(user_agent)
    except Exception as e:
        raise FetcherError(
            f"SecLander: edgartools.set_identity failed: {type(e).__name__}: {e}"
        ) from e

    try:
        company = edgartools.Company(ticker)
    except Exception as e:
        raise FetcherError(
            f"SecLander: edgartools.Company({ticker!r}) failed: "
            f"{type(e).__name__}: {e}"
        ) from e

    # Edge case: some delisted/invalid tickers return a Company object with
    # ``not_found`` set. Treat those as not-found, not error.
    if getattr(company, "not_found", False):
        raise FetcherNotFoundError(
            f"No SEC-registered company for ticker {ticker!r}"
        )

    try:
        filings = company.get_filings(form=form)
    except Exception as e:
        raise FetcherError(
            f"SecLander: company.get_filings(form={form!r}) failed: "
            f"{type(e).__name__}: {e}"
        ) from e

    if filings is None or len(filings) == 0:
        raise FetcherNotFoundError(
            f"No {form} filings found for ticker {ticker!r}"
        )

    filing = filings[0]  # most recent

    try:
        html = filing.html()
    except Exception as e:
        raise FetcherError(
            f"SecLander: filing.html() failed for {ticker} {form}: "
            f"{type(e).__name__}: {e}"
        ) from e

    if not html:
        raise FetcherError(
            f"SecLander: filing.html() returned empty for {ticker} {form}"
        )

    filing_extras = {
        "accession": _safe(filing, "accession_number"),
        "filing_date": _safe(filing, "filing_date"),
        "filing_url": _safe(filing, "filing_url") or _safe(filing, "url"),
        "cik": _safe(filing, "cik") or _safe(company, "cik"),
        "company_name_as_filed": _safe(filing, "company"),
        "period_of_report": _safe(filing, "period_of_report"),
    }

    # business_address / mailing_address are edgartools *methods* (not
    # properties) that return an ``Address`` object. Call, don't getattr.
    def _call_address(name: str):
        try:
            meth = getattr(company, name, None)
            return _safe_address(meth() if callable(meth) else meth)
        except Exception:
            return None

    company_extras = {
        "canonical_name": _safe(company, "name") or _safe(company, "display_name"),
        "cik": _safe(company, "cik"),
        "sic": _safe(company, "sic"),
        "industry": _safe(company, "industry"),
        "business_category": _safe(company, "business_category"),
        "fiscal_year_end": _safe(company, "fiscal_year_end"),
        "business_address": _call_address("business_address"),
        "mailing_address": _call_address("mailing_address"),
        "tickers": _safe(company, "tickers"),
        "filer_category": _safe(company, "filer_category"),
        "filer_type": _safe(company, "filer_type"),
        "is_foreign": _safe(company, "is_foreign"),  # property, returns bool
    }

    return html, filing_extras, company_extras


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

        # --- fetch via edgartools ---
        # Returns the primary 10-K document HTML only (no exhibits / XBRL /
        # graphics) plus structured filing + company metadata. This replaces
        # the Wave-A raw-HTTP path that fetched the full SGML submission
        # bundle (24 MB for JNJ 2026-02-11 with 167 concatenated documents).
        fetch_timestamp_utc = iso_utc_now()
        html, filing_extras, company_extras = _fetch_filing_via_edgartools(
            ticker, form, resolved_agent,
        )

        # Write the primary HTML to disk.
        raw_path.write_text(html, encoding="utf-8")
        bytes_written = raw_path.stat().st_size

        # VP Eng test-eval: hash is computed from the bytes actually on disk.
        sha = _shared.sha256_file(raw_path)

        # PRD-004 v5 Phase 5B (D2): canonical plaintext + manifest persisted
        # alongside raw.html. Single producer of the byte-stable plaintext
        # the rag-corpus builder, scenario runners, and lineage UI all read
        # against. Manifest pins normalization_version so downstream consumers
        # detect drift without re-canonicalizing.
        from .canonical import write_canonical_artifacts
        canonical = write_canonical_artifacts(
            raw_path=raw_path,
            raw_bytes=html.encode("utf-8"),
            raw_sha=sha,
            kind="html",
            domain=self.DOMAIN,
            source=self.SOURCE,
            lander_name=self.name,
            lander_version=self.version,
            fetch_timestamp_utc=fetch_timestamp_utc,
        )

        # source_extras: per-source fields that don't fit the first-class
        # CorpusDocumentMetadata slots.
        extras = build_source_extras(
            lander_name=self.name,
            lander_version=self.version,
            fetch_timestamp_utc=fetch_timestamp_utc,
            http_status=200,  # edgartools abstracts HTTP; filing.html() either works or raises
            extra_fields={
                # Filing-level metadata (accession, filing_date, cik, etc.)
                **filing_extras,
                "filing_type": form,
                "source_url": filing_extras.get("filing_url"),
                "bytes_written": bytes_written,
                # Company-level metadata — new in 3.0.0. Ground-truth
                # canonical name, industry classification, addresses, etc.
                "company": company_extras,
                # D2: canonical plaintext provenance — admin's registry
                # (D3 schema soft-add) reads these into the corpus_document row.
                "plaintext_sha": canonical.plaintext_sha,
                "plaintext_bytes": canonical.plaintext_bytes,
                "normalization_version": canonical.normalization_version,
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
