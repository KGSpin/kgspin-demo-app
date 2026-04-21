"""Sprint 11 Task 7 — ``kgspin-demo register-fetchers`` tests.

Updated from Sprint 09's 4-lander assertions to the ADR-004 5-lander
catalog (``sec_edgar``, ``clinicaltrials_gov``, ``marketaux``,
``yahoo_rss``, ``newsapi``).

Also covers the new ``--deprecate-old`` helper's graceful-degrade
behavior (VP Eng condition 4) against an admin that returns 405 for
the PATCH attempt.
"""

from __future__ import annotations

import logging

from kgspin_interface.registry_client import ResourceKind


EXPECTED_FETCHER_IDS = {
    "sec_edgar",
    "clinicaltrials_gov",
    "marketaux",
    "yahoo_rss",
    "newsapi",
}


def test_register_all_against_fake_idempotent() -> None:
    """Running register_all twice produces identical state (5 entries,
    not 10). Admin's canonical id derivation + fake's 409 collapse make
    duplicate registration a no-op."""
    from kgspin_demo_app.cli.register_fetchers import register_all
    from tests.fakes.registry_client import FakeRegistryClient

    fake = FakeRegistryClient()

    ids1 = register_all(fake)
    assert len(ids1) == 5

    listed = fake.list(ResourceKind.FETCHER)
    assert len(listed) == 5

    ids2 = register_all(fake)
    assert ids2 == ids1
    assert len(fake.list(ResourceKind.FETCHER)) == 5


def test_register_all_emits_expected_lander_ids() -> None:
    from kgspin_demo_app.cli.register_fetchers import register_all
    from tests.fakes.registry_client import FakeRegistryClient

    fake = FakeRegistryClient()
    ids = register_all(fake)

    assert len(ids) == 5
    all_fetchers = fake.list(ResourceKind.FETCHER)
    fetcher_ids = {r.metadata.get("spec", {}).get("fetcher_id") for r in all_fetchers}
    assert fetcher_ids == EXPECTED_FETCHER_IDS


def test_register_all_uses_demo_packager_actor() -> None:
    from kgspin_demo_app.cli.register_fetchers import register_all
    from tests.fakes.registry_client import FakeRegistryClient

    fake = FakeRegistryClient()
    register_all(fake, actor="demo:packager")
    for resource in fake.list(ResourceKind.FETCHER):
        assert resource.provenance.registered_by == "demo:packager"


def test_cli_main_exits_8_when_admin_url_unset(monkeypatch) -> None:
    from kgspin_demo_app.cli.register_fetchers import main
    monkeypatch.delenv("KGSPIN_ADMIN_URL", raising=False)
    assert main([]) == 8


def test_expected_fetcher_ids_match_domain_fetchers() -> None:
    """Cross-check: _expected_fetcher_ids() equals the union of
    DOMAIN_FETCHERS values. This is the invariant ADR-004 §3 rests on.
    """
    from kgspin_demo_app.cli.register_fetchers import _expected_fetcher_ids
    assert set(_expected_fetcher_ids()) == EXPECTED_FETCHER_IDS


def test_catalog_key_matches_lander_name() -> None:
    """Each catalog entry's key must equal the lander class's ``name``
    attribute — ``register_all`` aborts otherwise. Pin this.
    """
    from kgspin_demo_app.cli.register_fetchers import (
        _build_fetcher_metadata, _expected_fetcher_ids,
    )
    for fid in _expected_fetcher_ids():
        md = _build_fetcher_metadata(fid)
        assert md.spec.fetcher_id == fid


def test_deprecate_old_graceful_degrade_on_405(httpx_mock, caplog) -> None:
    """VP Eng condition 4: if admin returns 405 (no PATCH route yet),
    the helper logs per-ID + does not crash.
    """
    import httpx as _httpx  # noqa: F401 (httpx_mock fixture pulls httpx)

    from kgspin_demo_app.cli.register_fetchers import (
        _deprecate_old_ids, _SPRINT_09_DEPRECATED_IDS,
    )

    admin_url = "http://127.0.0.1:8750"
    for rid in _SPRINT_09_DEPRECATED_IDS:
        httpx_mock.add_response(
            method="PATCH",
            url=f"{admin_url}/resources/{rid}",
            status_code=405,
        )

    caplog.set_level(logging.INFO)
    results = _deprecate_old_ids(
        admin_url, _SPRINT_09_DEPRECATED_IDS, logging.getLogger("test"),
    )

    assert len(results) == len(_SPRINT_09_DEPRECATED_IDS)
    for rid, status, note in results:
        assert rid in _SPRINT_09_DEPRECATED_IDS
        assert status == 405
        # Evergreen phrasing per admin team 2026-04-19: pins the
        # Sprint 03 reference, not a handover-memo filename that goes
        # stale when the status-mutation endpoint lands.
        assert "status mutation" in note
        assert "admin Sprint 03" in note


def test_deprecate_old_records_success_on_200(httpx_mock, caplog) -> None:
    """If admin PATCHes successfully, helper records (id, 200, 'marked
    DEPRECATED') for each ID.
    """
    from kgspin_demo_app.cli.register_fetchers import (
        _deprecate_old_ids, _SPRINT_09_DEPRECATED_IDS,
    )

    admin_url = "http://127.0.0.1:8750"
    for rid in _SPRINT_09_DEPRECATED_IDS:
        httpx_mock.add_response(
            method="PATCH",
            url=f"{admin_url}/resources/{rid}",
            status_code=200,
            json={"id": rid, "status": "deprecated"},
        )

    caplog.set_level(logging.INFO)
    results = _deprecate_old_ids(
        admin_url, _SPRINT_09_DEPRECATED_IDS, logging.getLogger("test"),
    )
    for rid, status, note in results:
        assert status == 200
        assert note == "marked DEPRECATED"
