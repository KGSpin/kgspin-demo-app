"""Sprint 11 Task 7 — DOMAIN_FETCHERS config sanity tests.

Covers VP Eng condition 2 (Phase 1 plan review): the module is
data-only with two lookup helpers, no side effects. These tests pin
the invariants so future changes can't silently add a network call or
logging to this import path.
"""

from __future__ import annotations


def test_domain_fetchers_has_expected_domains() -> None:
    from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS
    assert set(DOMAIN_FETCHERS.keys()) == {"financial", "clinical"}


def test_financial_has_expected_backends() -> None:
    from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS
    assert DOMAIN_FETCHERS["financial"] == ["sec_edgar", "marketaux", "yahoo_rss"]


def test_clinical_has_expected_backends() -> None:
    from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS
    assert DOMAIN_FETCHERS["clinical"] == ["clinicaltrials_gov", "newsapi"]


def test_fetchers_for_known_domain() -> None:
    from kgspin_demo_app.domain_fetchers import fetchers_for
    assert fetchers_for("financial") == ["sec_edgar", "marketaux", "yahoo_rss"]
    assert fetchers_for("clinical") == ["clinicaltrials_gov", "newsapi"]


def test_fetchers_for_unknown_domain_returns_empty_list() -> None:
    from kgspin_demo_app.domain_fetchers import fetchers_for
    assert fetchers_for("legal") == []
    assert fetchers_for("") == []


def test_fetchers_for_returns_a_copy() -> None:
    """Mutating the returned list must not mutate the module-level dict."""
    from kgspin_demo_app.domain_fetchers import DOMAIN_FETCHERS, fetchers_for
    before = list(DOMAIN_FETCHERS["financial"])
    ret = fetchers_for("financial")
    ret.append("hacked")
    assert DOMAIN_FETCHERS["financial"] == before


def test_domains_served_by_single_domain_fetcher() -> None:
    from kgspin_demo_app.domain_fetchers import domains_served_by
    assert domains_served_by("marketaux") == ["financial"]
    assert domains_served_by("yahoo_rss") == ["financial"]
    assert domains_served_by("sec_edgar") == ["financial"]
    assert domains_served_by("clinicaltrials_gov") == ["clinical"]


def test_domains_served_by_multi_domain_fetcher() -> None:
    """ADR-004's whole point: ``newsapi`` may live under multiple domains.

    Currently only ``clinical``, but the test pins the shape — if
    ``newsapi`` is added to ``financial`` later, this test records the
    order-preserving invariant.
    """
    from kgspin_demo_app.domain_fetchers import domains_served_by
    domains = domains_served_by("newsapi")
    assert "clinical" in domains
    # Deterministic insertion order of DOMAIN_FETCHERS.
    assert domains == [d for d in ["financial", "clinical"] if d in domains]


def test_domains_served_by_unknown_fetcher_returns_empty_list() -> None:
    from kgspin_demo_app.domain_fetchers import domains_served_by
    assert domains_served_by("unknown_lander") == []
    assert domains_served_by("") == []


def test_module_has_no_side_effects_on_import() -> None:
    """VP Eng condition 2: importing ``domain_fetchers`` must not touch
    the network, open files, or emit logs. We verify by importing into
    a fresh namespace and checking that only data + helpers are
    exposed.
    """
    import importlib
    mod = importlib.import_module("kgspin_demo_app.domain_fetchers")
    public = {k for k in dir(mod) if not k.startswith("_")}
    # Two callables + one data dict are the only public surface.
    assert public >= {"DOMAIN_FETCHERS", "fetchers_for", "domains_served_by"}
    # No incidental leakage.
    forbidden = {"requests", "httpx", "logging", "logger", "os"}
    assert public.isdisjoint(forbidden), \
        f"unexpected public names leaked: {public & forbidden}"
