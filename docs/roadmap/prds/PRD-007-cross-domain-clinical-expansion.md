# PRD-007: Cross-Domain Clinical Expansion — News-to-Clinical Linkage

**Status:** In Progress
**Author:** Dev Team (capturing Sprint 11-enabled requirements; market-research depth owned by VP Product)
**Created:** 2026-04-19
**Last Updated:** 2026-04-19
**Milestone:** Cross-domain News & Clinical Correlation (Phase 2)
**Initiative:** Backlog

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 6 | Every clinical-domain demo scenario benefits (NCT trial + correlated news). Smaller reach than the cross-domain PRDs that front the sales cycle (PRD-004/PRD-005) since it lives one layer down — it's the capability those rely on, not the thing prospects see first. |
| **Impact** | 5 | Transformative for the compare demo's cross-domain story: without term-scoped news a clinical extract can only reference tickers, which is incoherent for NCT trials. |
| **Confidence** | 0.7 | Sprint 11 ships the plumbing (domain-agnostic NewsAPI lander, DOMAIN_FETCHERS mapping). Confidence is not 1.0 because the intelligence-tab term-extraction rewrite is explicitly out of scope this sprint (deferred to Sprint 12+) — the search-side logic still needs a matching upgrade. |
| **Effort** | M (2 sprints) | Sprint 11 lands the plumbing + landers; a follow-up sprint rewrites the intelligence-tab term search to consume cross-domain news. |
| **RICE Score** | **(6 × 5 × 0.7) / 2 = 10.5** | |

---

## Goal

Make the compare demo meaningfully cross-domain: clinical extractions
(NCT trials → drug names + conditions) must be able to reach news
sources that are term-scoped rather than ticker-scoped. Today a
clinical extract can only reference financial news via its ticker-
shaped identifier — which is nonsensical for a clinical corpus. We
want a clinical entity extraction to automatically surface related
news articles scoped by drug name, condition name, or indication,
and have those articles participate in the knowledge-graph
construction the same way financial news does for 10-K-anchored
tickers.

## Background

Sprint 07 introduced a `yahoo_news` lander that misbehaved as
"healthcare news via NewsAPI." Sprint 09 made it worse by splitting
into `YahooNewsLander` (name=`newsapi_financial`) and
`HealthNewsLander` (name=`newsapi_health`) — two landers hitting the
same NewsAPI backend, differing only by compile-time domain binding.
This created an anti-pattern the ABC was designed to avoid:
`DocumentFetcher.fetch(domain, source, identifier, **kwargs)` takes
`domain` at runtime.

Sprint 11's backend-named lander refactor (ADR-004) + `DOMAIN_FETCHERS`
config module unblocks cross-domain news. One `NewsApiLander` named
`newsapi` now serves:

- `financial` (term-scoped news for a ticker query)
- `clinical` (term-scoped news for a drug / condition query)

The intelligence-tab's current ticker-only search is explicitly
broken for clinical corpora. This PRD tracks the work to complete
the loop: term extraction + term-scoped news hits + a clinical
intelligence view. Sprint 11 ships the ingestion plumbing; the
search-side rewrite is deferred.

## Requirements

### Must Have (Sprint 11 — plumbing)

1. **Backend-named `newsapi` lander** that accepts `--domain` +
   `--query` and lands CORPUS_DOCUMENT records under the chosen
   domain.
   - Acceptance: ``uv run kgspin-demo-lander-newsapi --query
     "semaglutide" --domain clinical`` registers a clinical-domain
     corpus_document with `source=newsapi`.
2. **`DOMAIN_FETCHERS["clinical"]` contains `newsapi`** so the
   Sprint 10 Refresh UI and the compare-demo reader see it as a
   valid clinical news source.
   - Acceptance: `src/kgspin_demo/domain_fetchers.py` lists
     `"newsapi"` under `"clinical"`.
3. **Refresh UI clinical query derivation**: when the operator hits
   "Refresh All Clinical News" for an NCT trial, the default query
   is derived from the trial's `condition` + top-2 intervention
   names. Operator can override via a text input.
   - Acceptance: `/api/refresh-corpus/news/clinical?nct=NCT<########>`
     auto-fills the query from trial metadata and degrades to the
     raw NCT id if metadata is missing.
4. **Cross-domain test**: one `NewsApiLander` instance, invoked with
   `domain="financial"` and `domain="clinical"`, produces two
   distinct corpus_document records with matching domain fields.
   - Acceptance: `tests/unit/test_cross_domain_news.py` passes green.

### Should Have (Sprint 12+ — search rewrite)

1. **Intelligence-tab term search** replaces ticker-only search with
   a term-scoped path that resolves clinical entities
   (drug / condition / intervention) to `newsapi` corpus_documents
   via substring match on `source_extras.query`.
   - Acceptance: Clicking on an entity in a clinical extract surfaces
     relevant news articles matched on the entity text, not on the
     trial's NCT id.
2. **KG-level cross-domain linkage**: a clinical trial's extracted
   drug entities are joined to any financial-domain news about the
   drug's manufacturer (ticker → company → drug).
   - Acceptance: Deferred to a Sprint 12+ PRD; this PRD pins the
     upstream data plumbing Sprint 11 delivers, not the KG schema
     work downstream.

### Out of Scope (explicit non-goals)

- Full-article content for news sources. Yahoo RSS and Marketaux
  serve title + summary only. Full-text scraping is a separate
  integration not needed for the compare-demo story.
- Provider fallback (e.g. "if Marketaux is down, use Yahoo"). Each
  lander runs independently; the Refresh UI presents them as
  parallel sources.

## Success Criteria

- Clinical compare-demo session: an operator can land news for an
  NCT trial via one click, see the landed articles appear in the
  corpus_document registry with `domain=clinical, source=newsapi`,
  and have a KG extraction include entities from those articles
  alongside entities from the trial text itself.
- No new FETCHER records are registered when a lander serves two
  domains — the `newsapi` record stays single per ADR-004.

## Dependencies

- **ADR-004** (Backend-named landers) — committed Sprint 11 Day 1.
- **Sprint 10 registry reads** — extraction reads corpus from the
  admin registry, not from local disk. Already shipped.
- **Sprint 12+ intelligence-tab rewrite** — blocks the search-side
  Should-Have requirements.

## Changelog

| Date | Change | By |
|---|---|---|
| 2026-04-19 | Created. Per VP Prod 2026-04-17 consultation — captures the Sprint 11-enabled plumbing requirements for news-to-clinical linkage. RICE 10.5. | Dev |
| 2026-04-19 | Sprint 12: the **clinical seed queries migration** (a Must-Have Sprint 12 Task 8 subitem) partially landed — infrastructure (`get_pipeline_params()` helper in `admin_registry_reader`) is shipped + `confidence_floor` uses it as proof-of-pattern. Moving `clinical_seed_queries` from hardcoded `demo_compare.py` lists to `PipelineConfigMetadata.diagnostics.params` is Sprint 13 follow-up work; infrastructure is in place so it's a 1-line call-site edit + an archetypes YAML edit. The intelligence-tab term-extraction rewrite (Should-Have) remains Sprint 13+. | Dev |
