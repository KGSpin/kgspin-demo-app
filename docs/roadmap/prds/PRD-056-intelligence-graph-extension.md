# PRD-056: Intelligence Tab — Graph Extension via News Extractors

**Status:** Draft
**Author:** VP of Product
**Created:** 2026-04-22
**Last Updated:** 2026-04-22
**Milestone:** Demo Completeness — Intelligence Feature
**Initiative:** Backlog

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 8 | Every design partner asks "what happens as new information arrives?" The Intelligence tab is the answer. Today it exists but doesn't demonstrate the answer. Fixing it surfaces in every First-Call demo that gets past the initial compare view. |
| **Impact** | 5 | Transformative. A *growing* graph visibly differentiates us from LLM+RAG (which starts fresh every query). Converts "static KG" perception into "living knowledge layer." |
| **Confidence** | 0.8 | The news landers (Marketaux, Yahoo RSS, NewsAPI) already work. The extraction pipelines already work. The missing piece is the **fusion layer** — making news-derived nodes/edges first-class, provenanced, and visually distinct. That's mechanically tractable but has UX surface we haven't pressure-tested. |
| **Effort** | 2 (M) | One sprint — provenance-preserving merge + UI surfacing + an incremental-update mode. No new landers, no new pipelines. |
| **RICE Score** | **(8 × 5 × 0.8) / 2 = 16.0** | Clears the bar. |

---

## Goal

Turn the Intelligence tab into the demo artifact it was originally designed to be: a view where the operator starts with the base KG (SEC filing or clinical trial), runs news landers, and watches the graph **grow** with news-derived nodes and edges. Every added node/edge carries provenance back to the originating article. The viewer can see which parts of the graph are "canonical knowledge" (filings) and which are "signal overlay" (news), and can time-scrub through when each addition landed.

This is not a new feature. It is the **correct** implementation of the feature we claimed to ship. Today's behavior is broken in three ways (see Background).

## Background

### What works today
- News landers (Marketaux, Yahoo RSS, NewsAPI, PubMed-style clinical) correctly fetch articles and land them in admin.
- Per-article extraction runs through the same KGSpin pipelines used for filings.
- A `run_intelligence` endpoint exists at `/api/intelligence/{doc_id}` that streams progress via SSE.
- A `_merge_kgs` function exists that combines the base SEC KG with per-article news KGs.

### What's broken
The code paths exist but the *semantics* are wrong. Three concrete defects:

1. **First-wins dedup loses news signal.** `_merge_kgs` (demo_compare.py:4100) does first-wins merge on `(entity_type, normalized_text)`. If news says "J&J acquired Abiomed in 2022" and the SEC KG already has a J&J entity, the news entity is dropped — *and with it the Abiomed relation context*. We keep the base entity's attributes; the news-derived confidence, aliases, and article-specific context vanish.
2. **No provenance on merged output.** The merged KG dict has no `source` field on entities or relationships. Downstream viewers can't tell which edges came from the 10-K vs Yahoo RSS vs a PubMed abstract. The UI has no way to render "base graph" differently from "news overlay."
3. **No incremental growth visible in the UI.** The current flow runs all news sources in one batch, produces one final merged graph, and renders it. The viewer sees the end state. They don't see the graph *grow*. The pitch — "this is a living knowledge layer" — lands flat.

The RAGSearch paper (arXiv:2604.09666) argues topology is the load-bearing property. Topology is also *time-dependent*: as news streams in, new bridge entities appear, and the multi-hop reach of the graph improves. PRD-056 makes that evolution visible.

## Requirements

### Must Have

1. **Provenance-preserving merge**
   - Replace first-wins dedup with **union-with-provenance**. Every entity and relationship carries a `sources: list[SourceRef]` field where each `SourceRef` is `{kind: "filing" | "news", origin: str, article_id: str | None, fetched_at: ISO-8601}`.
   - When news yields an entity that matches a base entity (same normalized text + type), the merged entity **retains attributes from both** — aliases union, confidence = max, sources = union.
   - When news yields a *new* relationship involving an existing base entity (e.g., news has `(J&J)—acquired→(Abiomed)`, base has only J&J), the relationship is added as a new edge with news provenance. The subject node links to the existing J&J entity.
   - Acceptance: running the intel pipeline on JNJ with 5+ news articles produces a merged graph where ≥20% of relationships have `sources[0].kind == "news"`. Zero entity duplicates for the same normalized (text, type) pair.

2. **Visual distinction in the graph viz**
   - Nodes sourced from filings render with **solid borders** and the existing color palette.
   - Nodes sourced only from news render with **dashed borders** + a small news-outlet badge on hover.
   - Nodes with **both** sources render with solid borders + a small "+N news mentions" counter badge.
   - Relationships follow the same convention: solid edges for filing-sourced, dashed for news-sourced, double-weight for both.
   - Acceptance: on a JNJ intel run, the viewer can tell at a glance which parts of the graph were present before news landed and which parts were added.

3. **Per-source source filters**
   - UI gains checkboxes: "SEC filings," "Marketaux," "Yahoo RSS," "NewsAPI" (+ "PubMed" for clinical). Unchecking a source removes its contribution from the rendered graph in real time (client-side filter on the provenance field).
   - Acceptance: unchecking "NewsAPI" in the intel tab drops all NewsAPI-only nodes/edges; nodes with `sources` from multiple origins remain but lose their NewsAPI badge.

4. **Incremental "watch it grow" mode**
   - The `/api/intelligence/{doc_id}` stream emits `graph_delta` SSE events per article, not just a final merged `kg_ready`. Each delta event carries `{article_id, added_entities: [...], added_relationships: [...], merged_with: [entity_ids]}`.
   - UI animates additions: new nodes fade in, new edges draw with a brief highlight. User can pause/resume or step through article-by-article.
   - Acceptance: an intel run over 5 articles produces 5 visible growth steps. Pause button holds the current state; Step button advances one article.

5. **Timeline scrubber**
   - Once the run completes, a timeline scrubber appears beneath the graph. Scrubbing left removes latest additions; scrubbing right restores them. The scrub is ordered by article `fetched_at` (news) with filing contributions anchored to time-zero.
   - Acceptance: scrub-left to time-zero shows only the filing-derived graph; scrub-right to end shows the full merged graph. The transition is smooth (<200ms per step).

6. **Topological Health over time** *(bridges to PRD-055)*
   - The Topological Health Score is recomputed at each growth step. The tab displays a small sparkline above the graph showing how the score changed as news landed.
   - Acceptance: JNJ with 5 news articles shows a sparkline with 6 points (base + 5 deltas). The score typically rises (more bridge entities, better multi-hop reach). When it drops (news added isolated gossip), the sparkline dip is the teaching moment.

### Nice to Have

1. **Source-conflict surfacing** — when news contradicts filings (e.g., filing says revenue = $X, news says revenue = $Y), flag the conflicting relationships in the graph with a warning glyph. Conflict detection is a predicate-level diff, not semantic — we surface it, we don't adjudicate.
2. **News-only intel mode** — let the operator run intel on a ticker with no prior base KG (skip the SEC extraction). Useful for private-company or early-stage demos where filings don't exist.
3. **Related-ticker fan-out** — when news extraction surfaces a new ticker/company not in the base KG (e.g., Abiomed mentioned in a J&J article), offer a "Run intel on $ABMD" quick action.
4. **Time-windowed news replay** — let the operator pick a date range and re-run the intel flow only against articles in that window. Demo use case: "here's what the graph looked like 3 months ago."

## Technical Design (High-Level)

### Data changes
- `SourceRef` dataclass added to `kgspin_core/models` (or wherever KG entity/relation schema lives). Backward compat: old KGs missing `sources` get a synthetic `[{kind: "filing", origin: "legacy", article_id: None, fetched_at: None}]` at load time.
- `_merge_kgs` in demo_compare.py rewritten as `_merge_kgs_with_provenance`. Old function kept as a thin wrapper that calls the new one and strips provenance (for any existing callers that don't need it — though there may be none after this sprint).

### SSE wire format changes
- New `graph_delta` event kind. Additive to the existing event set; no breaking changes to the current `kg_ready` / `step_complete` events.
- The final `kg_ready` event remains for backward compat; clients can ignore it or use it as a "final state" checkpoint.

### UI changes
- All in `demos/extraction/static/js/intel-*.js` module(s) carved in Wave D. No compare-slot impact.
- New vis.js styling for dashed borders / source badges (leverage vis-network's existing node/edge styling config).
- Timeline scrubber is a thin wrapper around the existing growth-event log; no new state store.

### Integration with PRD-055
- Same `compute_health` function, called per-delta. The sparkline is just an array of scores indexed by delta.

> Requires ADR — **ADR-013: KG Provenance Model**. VP of Engineering owns the `SourceRef` schema, backward-compat story for legacy KGs, and the entity-merge rules when provenance varies.

## Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| % of news-source relationships preserved in merged graph (vs. current first-wins loss) | ≥95% | Instrument `_merge_kgs_with_provenance`: log pre-merge vs post-merge relationship counts |
| Demo operator self-reported "the graph grows visibly" satisfaction | ≥4/5 | Post-demo internal NPS |
| Topological Health Score delta (news-extended vs base-only) on the JNJ demo run | ≥10pp improvement | Internal benchmark: run intel on JNJ with 5 articles, compare scores |
| Zero regressions in existing /api/intelligence consumers | 0 | Existing Playwright smoke + manual compare-tab verification |
| Render perf: timeline scrub step | <200ms | Frontend perf instrumentation |

## Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| PRD-055 (Topological Health) | **Blocks** — the sparkline requires the score API | In Progress (Wave G) |
| ADR-013 (KG Provenance Model) | **Blocks** — need the `SourceRef` schema pinned | Not yet written |
| Existing news landers (Marketaux, Yahoo RSS, NewsAPI, PubMed) | Soft — already present, need regression-test coverage | Present |
| Wave D/E JS module carve | Soft — intel UI changes land in the carved modules | Complete |

## Open Questions

1. **Q1:** How should we treat news articles that arrive *before* the filing (e.g., analyst reports published before the 10-K)? Today "filing = base, news = overlay" is the mental model. If news can predate the filing, the timeline scrubber logic breaks. **Recommend:** treat `fetched_at` as the canonical ordering, not `source.kind`. Filings and news are both timestamped contributions; the timeline is just time. **Owner:** VP of Engineering (ADR-013).

2. **Q2:** ~~Does the merge policy differ for conflicting attributes...?~~ **Resolved 2026-04-22 by CEO:** show **both** values. Both are valid observations from different sources until PRD-057 (Typed Per-Relation Metadata Extraction) lets us extract richer per-source context (e.g., `tenure_start_date` on an `is_executive` relation) that would let us adjudicate temporally. Until then, render all conflicting values as a list in the node/edge drilldown, each tagged with its `SourceRef`. The graph viz displays the newest-by-`fetched_at` as the canonical label to keep the visualization readable; drilldown always shows the full list. **Tracks to:** PRD-057 in kgspin-core (metadata schemas per entity/relation type, opportunistically populated during extraction).

3. **Q3:** The incremental "watch it grow" animation could feel too slow for a 20-article intel run. Do we batch deltas (e.g., one growth step per 3 articles) or keep per-article granularity? **Recommend:** per-article by default; operator-configurable batch size in the UI. **Owner:** VP of Product, pending demo-day feedback.

4. **Q4:** The clinical domain's "news" source is PubMed abstracts — these are not news, they're scientific literature. Should we rename the source filter "Related Literature" in the clinical domain? **Recommend:** yes; source taxonomy is domain-specific. **Owner:** VP of Product, post-ship polish.

5. **Q5:** What happens to the Topological Health Score sparkline when the intel run is news-only (no base filing)? The "base score" would be 0. **Recommend:** first data point is the score after first article; no synthetic zero. **Owner:** VP of Product.

## Changelog

| Date | Change | By |
|------|--------|-----|
| 2026-04-22 | Created — addresses the three defects in the current Intelligence tab implementation (first-wins dedup, no provenance, no incremental growth). Grounded in arXiv:2604.09666's finding that topology evolves with new information. | VP of Product |
| 2026-04-22 | Q2 resolved by CEO: show both conflicting attribute values (each tagged with its SourceRef) rather than picking one. Proper attribute-level adjudication deferred to PRD-057 (Typed Per-Relation Metadata Extraction) in kgspin-core. | VP of Product |

— Prod
