# PRD-062: Sealable Proposition Surface (Provenance-Grounded Curation UI)

**Status:** Draft
**Author:** VP of Product
**Created:** 2026-04-24
**Last Updated:** 2026-04-24
**Milestone:** Demo Completeness — Trust & Curation
**Initiative:** Standalone (no cross-repo initiative — single UX surface)
**Supersedes:** PRD-041 (HITL Feedback UI) — see §Changelog for rationale

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 7 | Every demo operator. Every future customer who needs trust boundaries before committing facts to a production graph. Touches every extraction flow (compare, intel, multi-hop). |
| **Impact** | 4 | Converts KGSpin's determinism + span-provenance from an abstract engineering claim into a demoable trust artifact. Click-to-highlight is a 1-second dopamine hit for prospects, not a chore. Multiplier for the PRD-004 v4 "LLM vs KG" pitch. Not transformative alone, but compounds. |
| **Confidence** | 0.75 | UX is new surface. Tiered-trust policy depends on Wave-I utility gate (shipped) + a new `trust_tier` field on extraction outputs (to add). Span-provenance is already in place in kgspin-core. |
| **Effort** | 2 (M) | Core-side `trust_tier` field + demo-app queue UI + span-click-to-highlight + seal/purge actions + MCP-tool surface (nice-to-have). |
| **RICE Score** | **(7 × 4 × 0.75) / 2 = 10.5** | Meaningful priority. Ranks between PRD-056 v2 (bridging, 8.4) and PRD-055 (topology health, 19.1). |

---

## Goal

Turn KGSpin's span-provenanced extractions into a **tiered trust product** — every entity and relationship carries a trust tier, and the operator sees only the extractions that deserve their attention. Sealed propositions commit to the graph; purged propositions feed negative-training signal back to the Prophet loop (PRD-001). A span-click-to-highlight interaction grounds every seal decision in the exact source text, making trust *demonstrable* rather than *assumed*.

This PRD **supersedes PRD-041 (HITL Feedback UI)**. PRD-041 framed feedback as an after-the-fact flag ("something went wrong, flag it for the tuner"). PRD-062 reframes it as a pre-commit curation layer ("review the high-risk propositions before they enter the graph"). The feedback-to-tuner loop from PRD-041 is preserved as a downstream consequence of Purge actions.

## Background

### Why the reframe

Two moments surfaced this requirement:

1. **The UHC mixed-case-filer bug (2026-04-24 investigation).** Running discovery pipelines on UHC produced 374 entities, 0 relationships, and a hub that was `"UNKNOWN"`. The operator staring at the demo couldn't tell which of the 374 entities was load-bearing vs. garbage. A seal queue would have surfaced the 5–10 high-risk bridge-candidates for review instead of a wall of 374 items.

2. **The Nexus / KronosDeret thread (2026-04-24 publicist review).** The two-stage contradiction scan → user-accept-seal pattern in Nexus is the right architectural place to let a small amount of LLM non-determinism live. The trust boundary is at the **point the user seals**, not the point the model writes. KGSpin's span-provenance is perfectly positioned to exploit this pattern — we already have the evidence; we just need the UI.

### Why tiered trust, not seal-everything

For a 300-entity document the operator can't click through every extraction. Realistic breakdown for JNJ's 10-K with Wave-I graph-aware extraction in place:

| Trust tier | Approx count | Policy |
|---|---|---|
| **Auto-accept** (registry-match, high gate score, no conflicts) | ~250 | Commit to graph immediately. Visible in the graph, not in the queue. |
| **Requires-seal** (cross-hub bridge, novel entity, boundary ambiguous) | ~30 | Surface in queue. Seal commits; ignore leaves proposed. |
| **Flagged-for-review** (conflict detected, gate uncertain, low confidence) | ~10 | Surface in queue with warning. Seal requires explicit override. |

**~40 clicks, not 300+.** Operator cost is tractable. Signal-to-noise is inverted — the 40 items worth looking at are the 40 the UI shows.

## Requirements

### Must Have

1. **`trust_tier` field on every extraction (core-side)**
   - Kgspin-core emits `trust_tier: Literal["auto_accept", "requires_seal", "flagged_for_review"]` on every entity + relationship in an ExtractionResult.
   - Computed from:
     - Wave-I utility-gate output (already emitted via `HybridUtilityGate`).
     - Master-data match status (Verified / Prophet-Proposed per PRD-001 reconciliation).
     - Cross-hub relation flag (`kind == "bridge"` → always ≥ requires_seal).
     - Conflict detection (multiple values across sources → flagged_for_review).
     - Confidence band (below `entity_confidence_floor × 1.2` → ≥ requires_seal).
   - Acceptance: every extraction output carries a `trust_tier` value. Default backward-compat: legacy extractions load as `auto_accept`.

2. **Seal queue UI in `/compare` and `/intel` views**
   - New sidebar panel lists all `requires_seal` + `flagged_for_review` items for the current run.
   - Grouped by (a) bridge edges, (b) novel entities, (c) conflicts, (d) boundary-ambiguous spans.
   - Counter badge shows total unresolved items per category.
   - Acceptance: on a JNJ intel run, the queue shows ≤50 items across all categories; scrollable; filterable by category.

3. **Span-click-to-highlight in source document**
   - Click a proposition in the queue → the source document opens in a side panel with the exact span highlighted and scrolled into view.
   - Highlighted span shows character offsets + surrounding sentence context (±1 sentence).
   - If the proposition is a relationship, both endpoint entity spans are highlighted simultaneously.
   - Acceptance: click latency <200ms; span always visible after scroll; highlight persists until operator navigates away.

4. **Seal / Purge actions with one-click commit**
   - **Seal** — commit proposition to the graph. Updates entity/relationship `trust_tier` to `user_sealed`. Surfaces on graph viz with a distinct visual (solid stroke, sealed-glyph).
   - **Purge** — drop proposition from the graph. Records reason (dropdown: wrong entity type, hallucinated, incorrect span, wrong relation). Writes a negative-training-signal record for PRD-001 Prophet next-epoch.
   - **Retract** — previously-sealed or purged items can be unset within the session (not after commit to persistent storage).
   - Acceptance: seal and purge actions are one-click (no form); the visual state on the graph updates instantly.

5. **Conflict-resolution drilldown (bridges PRD-056 v2)**
   - When a `flagged_for_review` item is a conflicting-attribute case (two sources claim different values for the same entity attribute), the drawer shows both values side-by-side with SourceRef, evidence spans, and fetched_at timestamps.
   - Seal action on conflicts selects which value to canonicalize; Purge drops both.
   - Acceptance: J&J CEO conflict (Duato filing vs. Gorsky archived news) renders with both values + timestamps; single-click canonicalization.

6. **Negative-training-signal emission**
   - Every Purge writes a record to an append-only log: `{proposition_hash, reason, source_span, timestamp, bundle_hash, extraction_method}`.
   - Log is Prophet-readable (PRD-001 consumer).
   - Log retention policy: keep all; training re-runs can cite specific purged records as hard-negatives.
   - Acceptance: purge log at `~/.kgspin/purge_log/<doc_id>.jsonl` (or admin-registered resource); each record is schema-valid.

7. **`kgspin.unsealed_propositions(doc_id)` MCP tool** *(was Nice-to-Have in the ex-VP's draft; promoted here)*
   - External-agent-queryable tool surface via KGSpin's MCP server (see kgspin-mcp repo).
   - Returns list of propositions in state `requires_seal` or `flagged_for_review` for a given document.
   - Allows external curation layers (Nexus, LangGraph memory, custom agent stores, internal customer tooling) to consume KGSpin's deterministic extractions and seal them in their own trust model.
   - Acceptance: MCP tool accessible via the existing kgspin-mcp surface; documented schema; integration test against a local MCP client.

### Nice to Have

1. **Bulk-seal by category** — "seal all 8 cross-hub bridges" as one action. Reviewed as a group, sealed as a group.
2. **Semantic grouping of related purges** — if operator purges "operates_in" on three different entities in the same session, suggest the extraction rule may be wrong; hand to the VP-Eng-assistant agent (PRD-041 item #5 preserved) for rule review.
3. **Seal-confidence inference** — as the operator seals N propositions of type X without purging any, auto-promote low-confidence-type-X propositions from `requires_seal` to `auto_accept` for the remainder of the session. Exit Option: turn off in settings.
4. **Export sealed propositions as a gold-set increment** — for domain experts reviewing KGSpin extractions, produce an Oumi-compatible gold JSONL of sealed propositions. Feeds PRD-001 Prophet as high-confidence labels.
5. **Keyboard-driven seal flow** — J/K to navigate, S to seal, P to purge, R to retract. Power-user mode for review at scale.

## Non-Goals

- **No autosealing based on external-model consensus** — KGSpin's determinism is the asset; we don't want a silent ML-as-sealer path that erodes the trust boundary.
- **No "flag as incorrect and let the tuner figure it out" path** — that was PRD-041's framing; PRD-062 replaces it with Purge + explicit negative signal.
- **No free-form edit of extracted values** — if an entity is wrong, Purge and the Prophet-next-epoch fixes it. No inline text editing — breaks determinism and lineage.
- **No Nexus-specific integration** — the MCP tool surface is generic; any curation-layer consumer (Nexus, agents, customer tooling) uses the same API.

## Technical Design (High-Level)

### Core-side changes (kgspin-core)
- Extend `ExtractionResult` with per-entity + per-relationship `trust_tier` field.
- New helper `classify_trust_tier(extraction, utility_gate_output, master_data_match, confidence) -> TrustTier`. Called inside `_graph_aware_postprocess` (Wave I).
- Backward-compat: legacy results default to `auto_accept`.

### Demo-app changes
- New JS module `demos/extraction/static/js/seal-queue.js`.
- New HTML surface in `compare.html` — queue sidebar.
- Span-click-to-highlight wiring to the existing document-explorer tab.
- Purge-log writer in `demo_compare.py` (new endpoint `POST /api/seal-queue/action`).

### MCP tool (kgspin-mcp)
- New tool definition `kgspin.unsealed_propositions(doc_id)` exposed via the existing kgspin-mcp server.
- Calls into kgspin-core to enumerate propositions with `trust_tier in {requires_seal, flagged_for_review}`.

> Requires ADR — **ADR-015: Tiered Trust Model + Seal Semantics**. VP of Engineering owns: trust-tier classification thresholds, utility-gate integration, backward-compat rules, MCP tool API shape.

## Success Metrics

| Metric | Target | Measurement Method |
|---|---|---|
| Per-doc seal-queue size (JNJ 10-K benchmark) | ≤50 items | Internal benchmark run |
| Seal decision time (median, one-click flow) | <5 seconds | Frontend telemetry |
| Span-click-to-highlight latency p95 | <200ms | Frontend perf instrumentation |
| Demo-operator-rated "trust the graph" score (post-demo NPS) | ≥4/5 | Post-demo survey |
| Purge log records consumed by PRD-001 Prophet next-epoch | ≥95% | Cross-PRD pipeline test |

## Dependencies

| Dependency | Type | Status |
|---|---|---|
| PRD-056 v2 (SourceRef + conflict drilldown) | Soft — conflict UI extends PRD-056's drilldown pattern | Wave J partial, commits 1+2 shipped |
| PRD-055 (Topological Health) | Soft — `trust_tier` is orthogonal but rendered in the same UI surface | Shipped |
| Wave I utility gate (kgspin-core) | **Blocks** — `trust_tier` consumes gate output | Shipped (34932e7) |
| kgspin-mcp repo | **Soft blocks** — MCP tool surface Nice-to-Have | Active |
| ADR-015 (Tiered Trust Model) | **Blocks** — VP Eng-owned thresholds | Not yet written |

## Open Questions

1. **Which utility-gate signals gate which trust tier?** Current Wave-I gate is binary (commit/reject). Trust-tier is 3-way. Need a mapping function. **Recommend:** committed + registry-match + high-confidence → `auto_accept`; committed + bridge OR committed + novel → `requires_seal`; committed + conflict-detected OR committed + low-confidence → `flagged_for_review`. **Owner:** VP of Engineering (ADR-015).

2. **What's the canonical storage for purge-log records?** Append-only JSONL per doc, or an admin-registered `purge_event` resource kind? **Recommend:** admin-registered resource — lineage-consistent with bundle + gold-set lineage. Enables cross-session audit. **Owner:** VP of Engineering.

3. **Should Seal actions write through to admin's persistent graph store?** If yes, we're building production-grade curation persistence (Phase 2 product). If no, seal state is per-session only. **Recommend:** per-session for v1; persistent store is Phase 2. **Owner:** VP of Product.

4. **MCP tool shape — `unsealed_propositions(doc_id)` vs. broader `propositions(doc_id, filter_trust_tier)`?** Broader is more flexible but larger surface. **Recommend:** start narrow (unsealed only); widen if customer use-case demands. **Owner:** VP of Product.

5. **Does PRD-062 supersede PRD-041 in full, or only partially?** PRD-041's "Little LLM Agent" for suggested FPs is a separate feature — should it survive as a Nice-to-Have here, or move to a new PRD? **Recommend:** supersede PRD-041 fully; fold the Little-LLM-Agent into a future PRD-063 ("FP Suggestion Agent") so PRD-062 stays focused. **Owner:** VP of Product.

## Changelog

| Date | Change | By |
|---|---|---|
| 2026-04-24 | Created. Supersedes PRD-041. Triggered by (a) UHC mixed-case-filer UX gap surfaced during extraction debugging, (b) Nexus / KronosDeret publicist-thread architectural framing ("User-Seal gating as trust boundary"). Reframes from "after-the-fact feedback" (PRD-041) to "pre-commit tiered curation" (PRD-062). Folds in span-click-to-highlight as UX anchor; adds MCP tool surface for external curation-layer consumers. | VP of Product |

— Prod
