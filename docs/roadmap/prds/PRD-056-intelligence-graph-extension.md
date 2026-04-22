# PRD-056: Intelligence Tab — Bridging-First Graph Extension

**Status:** Draft (v2 — Bridging-First)
**Author:** VP of Product
**Created:** 2026-04-22
**Last Updated:** 2026-04-22
**Milestone:** Demo Completeness — Intelligence Feature (Cross-Repo v1)
**Initiative:** Intelligence Cross-Repo v1 (this PRD + PRD-058 + PRD-059 + PRD-060)

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 9 | Every design partner opens with "why not ChatGPT/Claude with our docs?" and immediately follows with "what happens when new info arrives?" Bridging is the answer to the second question, and it amplifies the PRD-004/PRD-055 answer to the first — once a news article creates a J&J↔Merck bridge, the multi-hop compare flow has bigger questions to show off. |
| **Impact** | 5 | Transformative. "Our graph connects your portfolio across sources" is a pitch no LLM-RAG competitor can give. Hub-to-hub bridges unlock the highest-value multi-hop questions ("has J&J been sued alongside Merck?" "which trials do they co-fund?"). The Topological Health sparkline makes the structural payoff visible in real time. |
| **Confidence** | 0.75 | News landers work; extraction works; topology score works. The uncertainty is on the *selective* part — the utility gate (what to add, what to skip) is a new heuristic we haven't pressure-tested. Graph-aware extraction (PRD-060) is also new code. Stepped down from PRD-055's 0.85 to reflect those unknowns. |
| **Effort** | 4 (L-XL) | Cross-repo — touches demo-app (UI + SSE), blueprint (schema additions), admin (hub-registry endpoint), core (graph-aware extractor + utility gate). 4 PRDs ship together as one initiative. |
| **RICE Score** | **(9 × 5 × 0.75) / 4 = 8.4** | Lower than PRD-055's 19 and PRD-004 v4's 12, but RICE penalizes the effort of properly done cross-repo work. Strategic importance per CEO direction 2026-04-22 overrides pure RICE ordering. |

---

## Goal

Turn the Intelligence tab into a **bridging-first graph-extension experience**: when news lands for a ticker, the graph grows *not* by accumulating more spokes on the same hub, but by discovering and creating **cross-hub bridges** — edges that connect the current hub (JNJ) to other registered hubs (Merck, Pfizer, Abbott, etc.) that news has mentioned. Enrichment (more spokes) still happens but is capped by a utility gate. Factoring (sub-hub spin-off) and promotion (spoke-becomes-hub) are deferred to v2.

The viewer watches the JNJ graph grow with a few well-placed bridges to other companies they already have graphs for — and sees multi-hop questions become answerable that weren't before. The Topological Health sparkline confirms the topology is getting better, not just bigger.

## Background

### The academic frame (see CTO design note 2026-04-22)

Four canonical fan-out moves for a star graph:
- **Enrichment** — more spokes on same hub (Barabási 1999 preferential attachment). Zero direct agentic payoff; just denser local context.
- **Bridging** — new edge hub-to-hub (Granovetter 1973 weak-ties; Burt 1992 structural holes). High agentic payoff — unlocks cross-hub multi-hop.
- **Factoring** — dense spoke-cluster spins off as a sub-hub with a bridge (Gallai 1967 modular decomposition). Medium agentic payoff; maintains navigability at scale.
- **Promotion** — spoke with enough external edges becomes its own hub (hierarchical graph construction). Medium-high over time; seeds future brokering.

PRD-056 v2 commits to **Bridging as the hero, Enrichment capped, Factoring/Promotion deferred**. The RAGSearch benchmark (arXiv:2604.09666, April 2026) provides empirical grounding: graph-RAG wins multi-hop Q&A by 26–32pp over dense-RAG + agentic orchestration, and the mechanistic explanation is that topological bridges carry the load agents cannot induce.

### The five defects in today's Intelligence tab

1. **First-wins dedup loses news signal.** `_merge_kgs` (`demos/extraction/demo_compare.py:4100`) drops news-derived entities silently when a base-KG entity normalizes to the same key.
2. **No provenance on merged output.** Can't distinguish "base graph" from "news extension" in any downstream consumer.
3. **No incremental growth visible in the UI.** Articles batch into one final render; the pitch lands flat.
4. **Extraction is graph-blind.** Each article is extracted in isolation, invents fresh J&J nodes when the base KG already has one. Merge becomes the primary dedup instead of a safety net.
5. **Entity-resolution normalization is bundle-local.** `normalize_entity_text` takes bundle admission tokens as context, and the merge path doesn't pass them consistently.

### Why this is cross-repo

- **Demo-app** owns the UI, the SSE wire format, and the merge.
- **Blueprint** owns bundle YAML schema — cross-hub relation types (`partnered_with`, `competes_with`, `litigated_alongside`, etc.) must be first-class citizens there, not demo-app hacks.
- **Admin** owns the seed/hub registry — the list of "which tickers could be bridged to" needs a cross-bundle aggregation endpoint.
- **Core** owns the extractor — making it graph-aware (accepts base KG + hub registry as context, prefers linking over inventing) is a core-level concern.

Trying to do this demo-app-only with hardcoded hubs and inline relation types would create disposable code; CEO direction 2026-04-22 chose "full cross-repo v1" over "walking-skeleton shipped fast." This PRD ships as **Intelligence Cross-Repo v1** alongside PRD-058 (blueprint), PRD-059 (admin), and PRD-060 (core).

## Requirements

### Must Have

1. **Provenance-preserving merge** (fixes defect #1 + #2)
   - Replace first-wins dedup with union-with-provenance. Every entity and relationship carries `sources: list[SourceRef]` where each `SourceRef` is `{kind, origin, article_id, fetched_at}`.
   - When merging two entities with the same normalized (text, type), retain attributes from both: aliases union, confidence = max, sources = union.
   - When news yields a new relationship involving an existing base entity, the edge is added with news provenance; the endpoint node links to the existing entity (no duplicate creation).
   - Acceptance: zero entity duplicates for identical normalized (text, type); `sources` present on every node/edge in the merged KG.

2. **Bridge creation from hub registry matches** (the hero move)
   - When extraction surfaces an entity that matches a hub in the admin hub-registry (consumed from PRD-059), the merge creates a **bridge edge** between the current hub and that other hub — labeled with the extracted relation type (`partnered_with`, `competes_with`, etc. from the PRD-058 schema), scored with the utility gate (PRD-060), tagged with SourceRef.
   - A matching hub entity that doesn't get a bridge-creating relation is promoted to a first-class spoke on the current hub (not dropped), so the viewer sees "Merck was mentioned but no specific relation extracted."
   - Acceptance: on a JNJ intel run with 5+ news articles that co-mention other portfolio tickers, the merged graph contains **at least one cross-hub bridge edge**. The bridge edge is visually distinct from spokes (requirement #3).

3. **Bridge-distinct visual rendering**
   - Bridge edges (cross-hub): rendered with double-weight stroke, distinct color, and the relation type as a label. Hover shows source + fetched_at.
   - Filing-sourced nodes: solid borders.
   - News-only nodes: dashed borders + source-outlet badge on hover.
   - Hybrid-source nodes: solid borders + "+N news mentions" counter.
   - Acceptance: on a JNJ intel run, the viewer can point at the graph and say "that's a bridge to Merck" without drilling down.

4. **Per-source filter checkboxes**
   - UI gains filter checkboxes for each source present: SEC filings, Marketaux, Yahoo RSS, NewsAPI (+ PubMed for clinical). Unchecking removes that source's contribution from the rendered graph (client-side, no round-trip).
   - Acceptance: unchecking "NewsAPI" drops NewsAPI-only nodes/edges; multi-source nodes remain but lose their NewsAPI badge.

5. **Incremental "watch it grow" SSE stream** (fixes defect #3)
   - `/api/intelligence/{doc_id}` emits `graph_delta` SSE events per article (in addition to the existing `kg_ready` for backward compat). Each delta carries `{article_id, added_entities, added_relationships, bridges_created, merged_with}`.
   - UI animates additions: new nodes fade in; bridges draw with an extra-emphasized highlight; viewer can pause/resume/step.
   - Acceptance: a 5-article run produces 5 visible growth steps. Pause button holds; Step advances one article.

6. **Timeline scrubber**
   - Once the run completes, a scrubber beneath the graph lets the viewer rewind to any intermediate state. Ordered by `fetched_at`; filings anchored to time-zero.
   - Acceptance: scrub-left to time-zero shows only the base KG; scrub-right to end shows the full merged graph. <200ms per step.

7. **Topological Health sparkline** (bridges to PRD-055)
   - Score recomputed at each growth step; tab displays a sparkline above the graph. Bridge creations typically produce visible score jumps; enrichment-only additions produce flat lines.
   - Acceptance: on the JNJ scenario "news mentions Merck partnership" → sparkline shows a clear step-up at the article that created the JNJ↔Merck bridge.

8. **Conflict value stack in drilldown** (implements CEO Q2 ruling)
   - When an entity attribute or relationship has multiple values across sources, the graph viz label shows newest-by-`fetched_at`; the drilldown drawer shows all values stacked with their SourceRefs. Temporal adjudication deferred to PRD-057 (typed metadata schemas).
   - Acceptance: J&J node drilldown on a corpus with current + archived CEO news shows both CEO entries, each tagged with source and fetched_at. Graph label shows the newest.

9. **Graph-aware extraction integration** (fixes defect #4)
   - Intel runs pass the current base KG + the admin hub registry to the extractor (via PRD-060's new extraction-context parameter). The extractor uses this to prefer linking over inventing.
   - Acceptance: extracting an article that mentions "Johnson & Johnson" on a graph that already has "J&J" produces **zero** pre-merge duplicates. Measured by entity-count-diff instrumentation.

10. **Bundle-consistent normalization at the merge site** (fixes defect #5)
    - Merge pipeline passes the base KG's bundle admission-token set as canonical normalization context to `normalize_entity_text`. All news-article entities re-normalized under that set before dedup key computation.
    - Acceptance: "Johnson & Johnson Inc." (SEC) and "Johnson & Johnson" (news) collapse to a single node regardless of the bundle the news extractor used.

### Nice to Have

1. **Source-conflict glyph** — when an edge has multiple values from different sources, a small warning glyph on the edge in the graph viz, drawing the viewer's attention to the drilldown stack.
2. **News-only intel mode** — skip the SEC base extraction; intel runs on a ticker with no prior base KG. Useful for private-company or pre-filing-season demos.
3. **Bridge target suggestions** — when news mentions a ticker not in the hub registry, surface a "Register $MRK as a hub" quick action that adds it (operator-approved) and triggers a follow-up intel run.
4. **Time-windowed news replay** — pick a date range, re-run intel only against articles in that window. "Here's what the graph looked like 3 months ago."
5. **HITL curation integration (PRD-041)** — low-confidence or low-trust-source bridges go to the PRD-041 HITL review queue before being committed to the graph.

## Non-Goals (explicit)

These are NOT in PRD-056 v2 scope:

- **Auto fan-out to unrelated tickers** — news mentioning "Merck" does NOT auto-fetch Merck news. We surface the mention; the operator registers Merck as a hub if they want to, then re-runs intel. Automatic fan-out risks unbounded crawls.
- **Source trust tiers / confidence weighting per origin** — all news sources contribute equal confidence. Trust-weighting is a follow-on PRD.
- **Time-decay of older news** — temporal decay needs a clean time model; PRD-057 (Typed Metadata Extraction) owns the timestamp fields that make decay possible.
- **Factoring + promotion** — dense-hub split-off and spoke-promotion-to-hub are deferred to PRD-061/062 (not yet drafted). v2 commits only to bridging.
- **Cross-domain entity linking** (clinical NCT → financial JNJ when sponsored) — tracked by PRD-007.
- **Global cross-slot intelligence** — intel runs per-ticker, not per-slot, not per-extraction-strategy.

## Technical Design (High-Level)

See companion PRDs for the repo-specific machinery. Demo-app owns:

### Data changes
- `SourceRef` dataclass added to a shared location (likely `kgspin_core.models` so it's reusable by the extractor side of PRD-060). Backward compat: legacy KGs missing `sources` get a synthetic `[{kind: "filing", origin: "legacy", article_id: None, fetched_at: None}]` at load time.
- `_merge_kgs` rewritten as `_merge_kgs_with_provenance`. The old name kept as a thin wrapper that strips provenance, in case any callers still need the legacy shape (there likely aren't any post-rewrite).

### SSE wire format
- New `graph_delta` event kind (additive; the existing `kg_ready` still fires as a final-state checkpoint for backward compat).

### UI
- Lives in `demos/extraction/static/js/intel-*.js` modules from the Wave D/E carve.
- Graph viz uses vis.js; dashed borders / double-weight bridges / source-outlet badges all supported by existing vis-network styling config.
- Timeline scrubber wraps the existing growth-event log client-side.

### Integration with PRD-055 (Topological Health)
- Same `compute_health` from kgspin-core, called per-delta. Sparkline is an array of scores indexed by delta.

> Requires ADR — **ADR-014: KG Provenance + Bridging Policy**. VP of Engineering owns: the `SourceRef` schema, legacy-KG backward-compat rules, and the utility-gate formula (for deciding whether a news-extracted edge has enough structural value to commit to the graph). Cross-references ADR-032 (topology score, kgspin-core) and the new ADR-033 (graph-aware extraction, kgspin-core, PRD-060).

## Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| % of news-source relationships preserved in merged graph (vs current first-wins loss) | ≥95% | Pre-merge vs post-merge relationship-count instrumentation |
| Number of cross-hub bridges created on the JNJ-with-portfolio scenario (5+ portfolio tickers already registered, 5+ news articles with co-mentions) | ≥3 bridges, ≥2 distinct target hubs | Internal benchmark run |
| Topological Health Score delta (bridged vs base-only) | ≥15pp improvement after 5 articles that create at least 1 bridge | Internal benchmark |
| Demo operator self-reported "the graph grows in ways that feel meaningful" rating | ≥4/5 | Post-demo internal NPS |
| Zero regressions in existing /api/intelligence backward-compat consumers | 0 | Existing smoke tests |
| Render perf: timeline scrub step | <200ms | Frontend perf instrumentation |

## Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| PRD-055 (Topological Health) | **Blocks** — sparkline + compute_health | Shipped 2026-04-22 (Wave G) |
| PRD-058 (blueprint cross-hub relation types) | **Blocks** — without the schema additions, bridges can't be labeled with proper relation types | Draft |
| PRD-059 (admin cross-bundle seed-registry endpoint) | **Blocks** — without the registry, there's no list of hubs to resolve against | Draft |
| PRD-060 (core graph-aware extractor + utility gate) | **Blocks** — without graph-aware extraction, the merge still has to do primary dedup | Draft |
| PRD-043 (Topological Seed Anchor Sieve) | Soft — shares `seed_entities` primitive in bundle YAML | Backlog |
| PRD-057 (Typed Per-Type Metadata Extraction) | Soft — will enable temporal conflict adjudication later | Stub |
| ADR-014 (KG Provenance + Bridging Policy) | **Blocks** — VP Eng owns `SourceRef` schema + utility-gate formula | Not yet written |

## Open Questions

1. **Q1 resolved** (2026-04-22): treat `fetched_at` as canonical timeline ordering; filings and news are both timestamped contributions. No domain bias.

2. **Q2 resolved** (2026-04-22 by CEO): show *both* conflicting attribute values, each tagged with its SourceRef. Graph viz label shows newest; drilldown shows the full stack. Proper temporal adjudication deferred to PRD-057.

3. **Q3:** How does the utility gate decide whether a news-extracted edge has enough structural value to commit? Candidates: (a) betweenness-centrality delta on the current graph, (b) fixed whitelist of relation types from PRD-058's schema, (c) hybrid (structural delta + relation-type priority). **Owner:** VP of Engineering (ADR-014).

4. **Q4:** The clinical domain's "news" source is PubMed abstracts. Rename the source filter "Related Literature" in the clinical domain? **Recommend:** yes; source taxonomy is domain-specific. **Owner:** VP of Product, post-ship polish.

5. **Q5:** On news-only intel mode (Nice-to-Have), the sparkline's "base" data point is the first article's score. Recommend: no synthetic zero, first data point is first article. **Owner:** VP of Product.

6. **Q6 resolved** (2026-04-22 by CEO on the A/B extraction question): graph-aware extraction (option B) — parallel fetch + small-pool sequential extraction ordered by `fetched_at`. Implemented in PRD-060.

## Changelog

| Date | Change | By |
|------|--------|-----|
| 2026-04-22 | Created v1 as "fix the merge." Addressed three defects: first-wins dedup, no provenance, no incremental growth. | VP of Product |
| 2026-04-22 | Q2 resolved by CEO (show both conflicting values); tracked PRD-057 for eventual temporal adjudication. | VP of Product |
| 2026-04-22 | **v2 rewrite** — shifts from "fix the merge" to "bridging-first graph extension." Commits to Bridging as the hero move (Granovetter 1973, Burt 1992), caps Enrichment, defers Factoring + Promotion. Two new defects added after code walk (graph-blind extraction, bundle-local normalization). Expanded Must-Haves from 6 to 10 to cover bridging, graph-aware extraction, and bundle-consistent normalization. Ships as part of a cross-repo v1 initiative with PRD-058 (blueprint), PRD-059 (admin), PRD-060 (core). RICE adjusted to reflect L-XL cross-repo effort. | VP of Product |

— Prod
