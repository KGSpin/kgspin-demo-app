# ADR-004: Backend-named landers with runtime `domain` argument

**Status:** ACCEPTED
**Date:** 2026-04-17
**Deciders:** Dev Team, under VP Eng BLOCKER directive during Sprint 11 plan review
**Supersedes:** n/a — additive to [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md)
**Superseded by:** n/a
**Related PRDs:** PRD-004 (KG vs LLM Comparison), PRD-005 (Domain Onboarding), PRD-007 (Cross-domain Clinical Expansion — new this sprint), PRD-043 (Topological Seed Anchor Sieve)

---

## RICE

| Factor | Value | Rationale |
|---|---|---|
| Reach | 8 | Every current + future lander; every domain onboarding flow; admin's FETCHER-record contract semantics. |
| Impact | 4 | Gets the ABC's runtime-domain design right. Wrong name → lander proliferation O(landers × domains); right name → O(landers). |
| Confidence | 0.9 | Direct read of `kgspin_interface.DocumentFetcher.fetch(domain, source, identifier, **kwargs)` — the ABC already accepts domain as runtime input; we're just naming landers consistent with that. |
| Effort | S | One sprint (Sprint 11) to rename 2 files, add 1 new file, update `register-fetchers`, codify in ADR. |
| **Score** | **(8 × 4 × 0.9) / 1 = 28.8** | |

---

## Context

Sprint 09 (REQ-007) landed the `DocumentFetcher` ABC across demo's 4
landers per [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md). The ABC
signature is:

```python
def fetch(self, domain: str, source: str, identifier: dict[str, str], **kwargs) -> FetchResult
```

`domain` is **explicitly a runtime argument** — the interface was designed
so one fetcher implementation can be invoked against any domain its
backend supports. But Sprint 09's lander-naming choices ignored that
design intent:

- `YahooNewsLander.name = "newsapi_financial"` — NewsAPI backend, hardcoded
  financial domain in the class name.
- `HealthNewsLander.name = "newsapi_health"` — **same NewsAPI backend**,
  hardcoded clinical domain.

Two classes, one backend, differ only by compile-time domain binding.
This collapses the ABC's runtime-domain design and sets up O(landers ×
domains) growth: adding a new domain that wants NewsAPI requires forking
the lander again.

Sprint 11's concrete trigger is **cross-domain news for clinical
extractions**. Clinical corpora (NCT trials) have drug names +
conditions, not tickers — a term-scoped NewsAPI query is the right
source. Under the Sprint 09 naming, that means a third fork
(`newsapi_clinical`). Three forks of the same backend is where the
pattern visibly breaks.

A second, aesthetic issue: `YahooNewsLander` was a Sprint 07 misnomer —
the class hit NewsAPI, never Yahoo. Sprint 11 reintroduces a real Yahoo
Finance RSS backend alongside Marketaux; aligning names to backends
resolves that too.

## Decision

Demo landers are named for their **backend**. Domain is passed at
`fetch()` call time and recorded on the `CORPUS_DOCUMENT` record, never
on the `FETCHER` record.

### 1. Naming rule

```
lander.name == <canonical-backend-id>
```

Examples (Sprint 11 canonical set):

| Lander class      | `name` / `fetcher_id` | Backend                     |
|-------------------|-----------------------|-----------------------------|
| `SecLander`       | `sec_edgar`           | SEC EDGAR                   |
| `ClinicalLander`  | `clinicaltrials_gov`  | clinicaltrials.gov API      |
| `MarketauxLander` | `marketaux`           | Marketaux `/v1/news/all`    |
| `YahooRssLander`  | `yahoo_rss`           | Yahoo Finance RSS feed      |
| `NewsApiLander`   | `newsapi`             | NewsAPI.org `/v2/everything` |

**Explicitly NOT:**

- `yahoo_news`, `health_news`, `news_financial`, `finance_scraper` — all
  collapse either the backend or the domain distinction. Nothing in a
  lander's name should reference the domain it happens to serve.
- Domain qualifiers like `newsapi_financial` — **anti-pattern**, see §3.

### 2. `domain` is a runtime argument

One lander registers **once** with admin via `register-fetchers`. When
invoked:

```python
# financial path
lander.fetch(domain="financial", source="newsapi", identifier={...}, query="AAPL earnings")

# clinical path — SAME LANDER INSTANCE, SAME fetcher_id IN ADMIN
lander.fetch(domain="clinical", source="newsapi", identifier={...}, query="semaglutide")
```

Each call produces a distinct `CORPUS_DOCUMENT` record that carries its
own `domain` field. The upstream FETCHER record stays one-to-one with the
backend — there is exactly one `newsapi` FETCHER record in admin, full
stop.

### 3. Domain-to-landers mapping lives in demo config

`src/kgspin_demo/domain_fetchers.py`:

```python
DOMAIN_FETCHERS: dict[str, list[str]] = {
    "financial": ["sec_edgar", "marketaux", "yahoo_rss"],
    "clinical":  ["clinicaltrials_gov", "newsapi"],
}
```

This is a **demo-repo concern** — the registry doesn't need to know which
domains use which landers. Two consumers read it:

1. `register-fetchers` CLI (registers the union of all listed landers).
2. Sprint 10's Refresh UI (knows which landers to fire for a given
   domain).

Strictly data + two lookup helpers (`fetchers_for`,
`domains_served_by`). No network, no logging, no side effects. Sprint 13+
candidate to migrate to YAML once the mapping stabilizes.

### 4. Future work: cross-repo `capabilities` field

If admin later wants the registry itself to know "which domains can this
fetcher serve?" — e.g. for a dashboard or cross-team discovery — that's a
`capabilities: list[str]` field on `FetcherMetadata` in the interface
package, requiring a separate cross-repo ADR. Not in scope for ADR-004.

## Alternatives Considered

### Alternative 1: Keep Sprint 09's per-domain lander classes

- **Pros:** No migration. No admin-side handover. Existing tests untouched.
- **Cons:** Forks per domain (O(L × D) growth). Collapses the ABC's
  runtime-domain design. Every new domain with a news need → another
  NewsAPI fork. And the `yahoo_news` misnomer persists.
- **Why rejected:** The problem is structural; not fixing it means every
  future domain costs O(L) extra landers. Sprint 11 is the cheapest
  point to correct.

### Alternative 2: Keep per-domain classes but have them delegate

I.e. `NewsApiFinancialLander` and `NewsApiClinicalLander` both call into a
shared `_NewsApiBackend` helper.

- **Pros:** Preserves Sprint 09 names; no admin handover needed.
- **Cons:** Moves the duplication inward — same number of FETCHER records
  in admin, same O(L × D) growth of class files. Solves nothing
  structurally.
- **Why rejected:** Half-measure that preserves the anti-pattern at the
  admin boundary.

### Alternative 3: Admin registers the domain:lander mapping as capabilities

Add a `capabilities` / `domains` field on `FetcherMetadata` now.

- **Pros:** Single source of truth across the cluster.
- **Cons:** Cross-repo ADR, interface-package release, admin tests +
  migration. Blocks Sprint 11's scope on two upstream repos.
- **Why rejected:** Right direction, wrong sprint. Ship the demo-side
  convention first (cheap, self-contained); elevate to the registry if
  another consumer asks for it.

## Consequences

### Positive

- **One lander, many domains.** Adding a new domain's news feed is a
  one-line `DOMAIN_FETCHERS` edit + (if the backend is new) one new
  lander. O(D) growth on the demo side, O(backends) on the admin side.
- **Naming matches intent.** `newsapi` registers once as the NewsAPI
  fetcher. Reader who sees the name doesn't need to know the
  Sprint-09-era domain history.
- **Sprint 11 unblocks cross-domain news** (PRD-007) with no additional
  lander class. Clinical extractions can reach news via `newsapi` without
  a `newsapi_clinical` fork.
- **Admin's FETCHER registry stays domain-agnostic** — matches how the
  ABC was designed, avoids cross-repo schema churn later.

### Negative

- **Breaking change for operators** who scripted against
  `kgspin-demo-lander-yahoo` or `kgspin-demo-lander-health-news`. Mitigated
  by one-sprint CLI shim (Sprint 11 Task 5) that prints an actionable
  migration message.
- **Admin registry migration.** Two Sprint 09 records
  (`newsapi_financial`, `newsapi_health`) need status flip to
  `DEPRECATED`, then hard-delete in Sprint 12. Handled by
  `register-fetchers --deprecate-old` with graceful degradation if admin
  doesn't support status mutation yet (see 2026-04-17 handover memo).
- **Multi-domain FETCHER contract is new.** Admin's Sprint 09 tests
  implicitly assumed 1:1 domain:fetcher. Sprint 11 handover memo flags
  this before the change lands so admin can update their side.

## The anti-pattern this corrects

Historical record — **do NOT reintroduce this pattern**:

```python
# Sprint 09 (anti-pattern)
class YahooNewsLander(DocumentFetcher):
    name = "newsapi_financial"  # <-- backend + domain glued together
    # ... fetches from https://newsapi.org/v2/everything
    #     domain is effectively hardcoded via the name

class HealthNewsLander(DocumentFetcher):
    name = "newsapi_health"     # <-- duplicate backend, different domain
    # ... same newsapi.org backend, different name
```

```python
# Sprint 11 (corrected)
class NewsApiLander(DocumentFetcher):
    name = "newsapi"            # <-- backend only; domain comes via fetch()
    # ... fetches from https://newsapi.org/v2/everything
    #     domain is a fetch() argument, recorded on corpus_document
```

If a future sprint feels tempted to "specialize" a lander for a domain,
stop: either the backend is genuinely different (new lander, new
backend-named name) or the specialization belongs in a fetch-time kwarg
(e.g. a domain-aware query builder).

---

## Cross-references

- [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md) — DocumentFetcher
  ABC + admin registry topology (still ACCEPTED; ADR-004 is additive).
- REQ-007 (kgspin-admin's coordination memo driving Sprint 09).
- REQ-005 (kgspin-core's extraction-reads-registry rule).
- Sprint 09 dev report — origin of the anti-pattern this ADR corrects.
- Sprint 11 plan — execution of the rename.
- `docs/handovers/2026-04-17-admin-lander-id-migration.md` — notifies
  admin team of the FETCHER-record migration.

— Dev Team
