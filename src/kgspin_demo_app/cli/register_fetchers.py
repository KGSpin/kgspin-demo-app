"""``kgspin-demo register-fetchers`` — idempotent lander registration CLI.

Wave A (CTO Q3): ``fetchers/registrations.yaml`` in the sibling
``kgspin-demo-config`` repo is the authoritative per-instance domain →
fetcher mapping. This CLI iterates over that YAML (resolved lazily by
:mod:`kgspin_demo_app.domain_fetchers` via ``KGSPIN_DEMO_CONFIG_PATH``)
and registers one FETCHER record per unique backend-named lander.

Registers exactly one record per unique ID (e.g. ``newsapi`` registers
ONCE even though it appears under two domains). Per ADR-004 §2 the
FETCHER record is domain-agnostic; the per-domain mapping lives in
``kgspin-demo-config``.

Actor: ``demo:packager`` (per Sprint 09 convention).

Exit codes:
- 0  all landers registered (or the --deprecate-old run completed,
     possibly with graceful-degrade log lines for unsupported admin)
- 2  invalid input
- 8  KGSPIN_ADMIN_URL not set
- 9  registry call failed
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from typing import Protocol

import httpx

from kgspin_interface import (
    DOCUMENT_FETCHER_CONTRACT_VERSION,
    FetcherMetadata,
)
from kgspin_interface.resources import InvocationSpec

from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS


# --- Lander catalog --------------------------------------------------------
#
# Mapping from backend-named lander ID → (module_path, class_name,
# description). ADR-004: lander IDs are backend names; the domain(s) a
# lander serves come from fetchers/registrations.yaml in
# kgspin-demo-config (read via DOMAIN_FETCHERS), not from this table.

_LANDER_CATALOG: dict[str, tuple[str, str, str]] = {
    "sec_edgar": (
        "kgspin_demo_app.landers.sec",
        "SecLander",
        "SEC EDGAR 10-K / 10-Q / 8-K filings fetcher",
    ),
    "clinicaltrials_gov": (
        "kgspin_demo_app.landers.clinical",
        "ClinicalLander",
        "ClinicalTrials.gov v2 API study fetcher",
    ),
    "marketaux": (
        "kgspin_demo_app.landers.marketaux",
        "MarketauxLander",
        "Marketaux /v1/news/all ticker-scoped financial-news fetcher",
    ),
    "yahoo_rss": (
        "kgspin_demo_app.landers.yahoo_rss",
        "YahooRssLander",
        "Yahoo Finance public RSS feed fetcher (no credentials)",
    ),
    "newsapi": (
        "kgspin_demo_app.landers.newsapi",
        "NewsApiLander",
        "NewsAPI.org /v2/everything term-scoped fetcher (domain-agnostic)",
    ),
}

# Sprint 09 fetcher IDs retired by Sprint 11 (ADR-004). --deprecate-old
# attempts to mark these DEPRECATED in admin; graceful-degrades if
# admin doesn't yet support status mutation.
# ``edgar`` is retired in favor of ``sec_edgar`` (backend-named); the
# two newsapi_* IDs are the duplicate-backend pair ADR-004 explicitly
# corrects.
_SPRINT_09_DEPRECATED_IDS: tuple[str, ...] = (
    "edgar",
    "newsapi_financial",
    "newsapi_health",
)


class _ClientLike(Protocol):
    """Duck-typing interface for ``register_fetchers`` — lets tests
    substitute a fake. Only ``register_fetcher`` is required for the
    default path; ``--deprecate-old`` additionally uses the admin URL
    via a direct ``httpx.Client`` call."""

    def register_fetcher(self, metadata, actor: str): ...


def _expected_fetcher_ids() -> list[str]:
    """Unique fetcher IDs across all domains in ``DOMAIN_FETCHERS``.

    Preserves ``_LANDER_CATALOG`` insertion order so operators see a
    stable registration sequence (sec → clinical → marketaux →
    yahoo_rss → newsapi under the default config).
    """
    wanted: set[str] = set()
    for ids in DOMAIN_FETCHERS.values():
        wanted.update(ids)
    return [fid for fid in _LANDER_CATALOG if fid in wanted]


def _build_fetcher_metadata(fetcher_id: str) -> FetcherMetadata:
    """Introspect the lander class behind ``fetcher_id`` for metadata.

    ``capabilities`` is the fetcher ID itself — admin uses this for
    capability queries. ADR-004 notes that if admin wants a richer
    capabilities field (e.g. the list of supported domains), that's a
    cross-repo ADR, not this sprint.
    """
    if fetcher_id not in _LANDER_CATALOG:
        raise RuntimeError(
            f"register-fetchers: {fetcher_id!r} is referenced by "
            f"DOMAIN_FETCHERS but not in _LANDER_CATALOG. Add an entry."
        )
    module_path, class_name, description = _LANDER_CATALOG[fetcher_id]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    lander_id = getattr(cls, "name", "")
    if lander_id != fetcher_id:
        raise RuntimeError(
            f"register-fetchers: catalog key {fetcher_id!r} doesn't match "
            f"lander.name {lander_id!r}. Fix the catalog or the class attr."
        )
    version = getattr(cls, "version", "3.0.0")
    contract_version = getattr(cls, "contract_version", DOCUMENT_FETCHER_CONTRACT_VERSION)

    spec = InvocationSpec(
        fetcher_id=fetcher_id,
        base_type="DocumentFetcher",
        contract_version=contract_version,
        module_path=f"{module_path}:{class_name}",
        extras={"lander_version": version},
    )
    return FetcherMetadata(
        spec=spec,
        capabilities=(fetcher_id,),
        owner="kgspin-demo",
        description=description,
    )


def register_all(client: _ClientLike, *, actor: str = "demo:packager") -> list[str]:
    """Register every unique fetcher referenced by ``DOMAIN_FETCHERS``.

    Idempotent: admin's 409 path returns the existing Resource without
    raising. Re-runs are a no-op.
    """
    ids: list[str] = []
    for fetcher_id in _expected_fetcher_ids():
        metadata = _build_fetcher_metadata(fetcher_id)
        record = client.register_fetcher(metadata=metadata, actor=actor)
        ids.append(record.id)
    return ids


# ---------------------------------------------------------------------------
# --deprecate-old helper (VP Eng condition 4 + Sprint 11 plan Task 4)
# ---------------------------------------------------------------------------


def _deprecate_old_ids(
    admin_url: str,
    ids: tuple[str, ...],
    logger,
    actor: str = "demo:packager",
) -> list[tuple[str, int, str]]:
    """Attempt to mark each legacy ID as ``DEPRECATED`` in admin.

    Per VP Eng condition 4 + handover memo: log the admin response
    status per ID; graceful-degrade on 400/404/405 (admin may not yet
    support resource status mutation).

    Returns a list of ``(id, http_status, note)`` tuples so the caller
    can include per-ID results in the dev report's migration section.

    The endpoint we try is ``PATCH /resources/<id>`` with
    ``{"status":"deprecated"}``. If admin exposes a different shape,
    this function still won't crash — the non-2xx response is logged
    and the caller decides what to do.
    """
    results: list[tuple[str, int, str]] = []
    with httpx.Client(base_url=admin_url, timeout=10.0) as client:
        for rid in ids:
            headers = {"X-Actor": actor}
            body = {"status": "deprecated"}
            try:
                resp = client.patch(f"/resources/{rid}", json=body, headers=headers)
            except httpx.RequestError as e:
                note = f"admin unreachable: {type(e).__name__}"
                logger.error(
                    "[DEPRECATE_OLD] id=%s status=ERR note=%s", rid, note,
                )
                results.append((rid, -1, note))
                continue

            status = resp.status_code
            if status == 200 or status == 204:
                note = "marked DEPRECATED"
                logger.info(
                    "[DEPRECATE_OLD] id=%s status=%d note=%s", rid, status, note,
                )
            elif status == 404:
                note = "not found (already deleted, or never registered)"
                logger.warning(
                    "[DEPRECATE_OLD] id=%s status=%d note=%s", rid, status, note,
                )
            elif status in (400, 405, 501):
                # 400 = bad request (admin may reject "status" field).
                # 405 = method not allowed (no PATCH route yet — admin
                #       currently rejects PATCH/PUT/DELETE on resources;
                #       admin team confirmed 2026-04-19 that this is
                #       expected today).
                # 501 = not implemented.
                # All three mean graceful-degrade. Phrased evergreen per
                # admin-team feedback: once admin Sprint 03 ships ADR-002
                # (Bundle Activation Policy), these should flip to 200.
                note = (
                    "status mutation pending admin Sprint 03 "
                    "(ADR-002 Bundle Activation Policy); "
                    "admin currently rejects PATCH/PUT/DELETE on resources"
                )
                logger.warning(
                    "[DEPRECATE_OLD] id=%s status=%d note=%s", rid, status, note,
                )
            else:
                note = f"unexpected admin response: {resp.text[:200]}"
                logger.error(
                    "[DEPRECATE_OLD] id=%s status=%d note=%s", rid, status, note,
                )
            results.append((rid, status, note))
    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kgspin-demo register-fetchers",
        description=(
            "Register demo DocumentFetcher landers with admin's registry. "
            "Catalog derived from DOMAIN_FETCHERS (see ADR-004)."
        ),
    )
    p.add_argument(
        "--deprecate-old",
        action="store_true",
        help=(
            "After registering current landers, attempt to mark the Sprint 09 "
            "domain-bound IDs (newsapi_financial, newsapi_health) as DEPRECATED. "
            "Graceful-degrades if admin doesn't support status mutation — see "
            "docs/handovers/2026-04-17-admin-lander-id-migration.md."
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    import logging
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger = logging.getLogger("kgspin-demo-register-fetchers")

    # ADR-001: bootstrap config.yaml + bridge to legacy env surface.
    from kgspin_demo_app.config import bootstrap_cli
    bootstrap_cli()

    admin_url = os.environ.get("KGSPIN_ADMIN_URL", "").strip()
    if not admin_url:
        sys.stderr.write(
            "[REGISTER_FETCHERS] KGSPIN_ADMIN_URL not set. "
            "Point at your admin instance (default: http://127.0.0.1:8750).\n"
        )
        return 8

    from kgspin_demo_app.registry_http import HttpResourceRegistryClient
    client = HttpResourceRegistryClient()
    try:
        try:
            ids = register_all(client)
        except RuntimeError as e:
            sys.stderr.write(f"[REGISTER_FETCHERS] {e}\n")
            return 9
    finally:
        client.close()

    for rid in ids:
        sys.stdout.write(f"{rid}\n")
    logger.info(f"Registered {len(ids)} fetchers: {ids}")

    if args.deprecate_old:
        logger.info(
            f"[DEPRECATE_OLD] Attempting to mark Sprint 09 IDs DEPRECATED: "
            f"{list(_SPRINT_09_DEPRECATED_IDS)}"
        )
        _deprecate_old_ids(admin_url.rstrip("/"), _SPRINT_09_DEPRECATED_IDS, logger)

    return 0


if __name__ == "__main__":
    sys.exit(main())
