"""Domain → backend-lander mapping for the demo.

Per Sprint 11 ADR-004 + VP Eng condition 2 (Phase 1 plan review): this
module is strictly data + two lookup helpers. No network, no logging, no
side effects beyond what an imported Python literal does. Behaves like a
dict-shaped YAML file. Sprint 13+ candidate to migrate to top-level YAML
once the mapping stabilizes.

Keys here are backend-named fetcher IDs (matching each lander's ``name``
attribute + admin's FETCHER record id). A fetcher ID may appear under
multiple domains — that's the whole point of ADR-004.
"""

from __future__ import annotations


DOMAIN_FETCHERS: dict[str, list[str]] = {
    "financial": ["sec_edgar", "marketaux", "yahoo_rss"],
    "clinical": ["clinicaltrials_gov", "newsapi"],
}


def fetchers_for(domain: str) -> list[str]:
    """Return the fetcher IDs that serve ``domain``.

    Returns an empty list for unknown domains — callers decide whether
    that's an error in their context.
    """
    return list(DOMAIN_FETCHERS.get(domain, ()))


def domains_served_by(fetcher_id: str) -> list[str]:
    """Return the domains that reference ``fetcher_id`` in ``DOMAIN_FETCHERS``.

    Order is deterministic (insertion order of ``DOMAIN_FETCHERS``).
    Returns an empty list for unknown fetcher IDs.
    """
    return [d for d, ids in DOMAIN_FETCHERS.items() if fetcher_id in ids]
