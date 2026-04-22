"""ClinicalTrials.gov lander — ``ClinicalLander(DocumentFetcher)``.

Sprint 09 REQ-007: migrated from Sprint 07's sidecar-writing CLI to a
``DocumentFetcher`` subclass. ``fetch()`` returns a ``FetchResult``;
the CLI wraps that into ``CorpusDocumentMetadata`` + registers via
admin's HTTP registry.

Env vars:
- ``CLINICAL_TRIALS_API_KEY`` (OPTIONAL) — lifts public rate limit.
- ``KGSPIN_CORPUS_ROOT`` (optional)
- ``KGSPIN_ADMIN_URL`` (REQUIRED for the CLI) — fail-loud if unset.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import ClassVar, Optional

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


LANDER_CLI_NAME = "kgspin-demo-lander-clinical"
LANDER_VERSION = "2.1.0"  # Wave A: FetchConfig dual-method adoption

_NCT_RE = re.compile(r"^NCT[0-9]{8}$")
_CTGOV_V2_BASE = "https://clinicaltrials.gov/api/v2"


class ClinicalFetchConfig(FetchConfig):
    """Typed config for :class:`ClinicalLander`."""

    nct: str
    output_root: Path | None = None
    date: str | None = None
    api_key: str | None = None


@retry(
    retry=retry_if_exception_type(requests.HTTPError),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
)
def _get_study(
    nct: str, *, api_key: Optional[str], timeout: int = 30,
) -> requests.Response:
    url = f"{_CTGOV_V2_BASE}/studies/{nct}"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        # Credentials go in headers, NEVER in URL params (Sprint 07 VP Sec).
        headers["x-api-key"] = api_key
    resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
    if resp.status_code == 404:
        raise FetcherNotFoundError(f"ClinicalTrials.gov has no study {nct}")
    resp.raise_for_status()
    return resp


class ClinicalLander(DocumentFetcher):
    """ClinicalTrials.gov trial fetcher — dual-method (Wave A / interface 0.8.1).

    Typed path: ``lander.fetch(nct="NCT12345678")``.
    Wire path: ``lander.fetch_by_id({"nct": "NCT12345678"})``.

    The base class validates the wire-format dict against
    :class:`ClinicalFetchConfig` and delegates to ``fetch(**parsed_kwargs)``.
    """

    name = "clinicaltrials_gov"
    version = LANDER_VERSION
    contract_version = DOCUMENT_FETCHER_CONTRACT_VERSION
    fetch_config_cls: ClassVar[type[FetchConfig]] = ClinicalFetchConfig

    DOMAIN: ClassVar[str] = "clinical"
    SOURCE: ClassVar[str] = "clinicaltrials_gov"

    def fetch(
        self,
        *,
        nct: str,
        output_root: Path | None = None,
        date: str | None = None,
        api_key: str | None = None,
    ) -> FetchResult:
        nct = nct.strip().upper()
        if not _NCT_RE.fullmatch(nct):
            raise FetcherError(
                f"ClinicalLander: invalid NCT id {nct!r} "
                f"(expected NCT + 8 digits)"
            )

        resolved_key = (
            api_key
            or os.environ.get("CLINICAL_TRIALS_API_KEY", "").strip()
            or None
        )

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
            identifier=nct,
            date=resolved_date,
            artifact_type="trial",
            filename="raw.json",
        )

        fetch_timestamp_utc = iso_utc_now()
        source_url = f"{_CTGOV_V2_BASE}/studies/{nct}"

        try:
            resp = _get_study(nct, api_key=resolved_key)
            etag = resp.headers.get("etag") or resp.headers.get("ETag")
            http_status = resp.status_code
            bytes_written = _shared.stream_to_file(
                resp.iter_content(chunk_size=_shared.STREAM_CHUNK_BYTES),
                raw_path,
                source_url=source_url,
            )
        except FetcherNotFoundError:
            raise
        except _shared.DownloadTooLargeError as e:
            raise FetcherError(f"ClinicalLander: {e}") from e
        except requests.HTTPError as e:
            raise FetcherError(
                f"ClinicalLander: HTTP {e.response.status_code if e.response else '??'} "
                f"from {source_url}"
            ) from e
        except Exception as e:
            raise FetcherError(
                f"ClinicalLander: unexpected error: {type(e).__name__}: {e}"
            ) from e

        sha = _shared.sha256_file(raw_path)
        extras = build_source_extras(
            lander_name=self.name,
            lander_version=self.version,
            fetch_timestamp_utc=fetch_timestamp_utc,
            http_status=http_status,
            extra_fields={
                "nct_id": nct,
                "source_url": source_url,
                "etag": etag,
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
        description="Fetch a ClinicalTrials.gov study and register with admin.",
    )
    p.add_argument("--nct", required=True, help="NCT identifier (NCT + 8 digits)")
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

    if not args.skip_registry and not os.environ.get("KGSPIN_ADMIN_URL", "").strip():
        sys.stderr.write(
            "[LANDER_REGISTRY] KGSPIN_ADMIN_URL not set; refusing to silently "
            "skip registration. Set the env var to your admin instance.\n"
        )
        return 8

    lander = ClinicalLander()
    try:
        result = lander.fetch(
            nct=args.nct,
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
        logger.exception("Unexpected error in ClinicalLander.fetch")
        sys.stderr.write(f"[LANDER_FETCH] {type(e).__name__}: {e}\n")
        return 7

    if not args.skip_registry:
        from datetime import datetime
        from kgspin_demo_app.registry_http import HttpResourceRegistryClient
        client = HttpResourceRegistryClient()
        try:
            extras = result.metadata
            doc_meta = CorpusDocumentMetadata(
                domain="clinical",
                source="clinicaltrials_gov",
                identifier={"nct": args.nct.upper()},
                fetch_timestamp=datetime.fromisoformat(
                    extras["fetch_timestamp_utc"].replace("Z", "+00:00")
                ),
                mime_type="application/json",
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
                    actor="fetcher:clinicaltrials_gov",
                )
            except RuntimeError as e:
                sys.stderr.write(f"[LANDER_REGISTRY] {e}\n")
                return 9
            logger.info(f"Registered: {record.id}")
        finally:
            client.close()

    sys.stdout.write(f"{result.pointer.value}\n")
    logger.info(
        f"Landed {args.nct.upper()}: "
        f"{result.metadata.get('bytes_written', '?')} bytes, "
        f"sha256={(result.hash or '')[:16]}... → {result.pointer.value}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
