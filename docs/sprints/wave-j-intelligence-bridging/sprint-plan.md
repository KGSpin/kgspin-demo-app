# Sprint Plan — Wave J: Intelligence Tab Bridging-First UX

**Branch:** `wave-j-intelligence-bridging`
**Base:** `main` (3db7f31)
**PRD:** `docs/roadmap/prds/PRD-056-intelligence-graph-extension.md` (v2)
**CTO assignment:** `/tmp/cto/wave-j-20260423/` (common-preamble + prompt)
**Date:** 2026-04-23

## TL;DR

Ten PRD-056 v2 Must-Haves grouped into six atomic commits. Backend first (merge + hub-registry + bridge creation + SSE), then frontend (rendering + filters + scrubber + sparkline + drilldown). KNOWN_TICKERS retirement and Playwright smoke land in the final commit.

## Cross-repo contract (already shipped)

| Integration | Origin | Consumed via |
|---|---|---|
| `ExtractionContext`, `HubEntry`, `SourceRef`, `HybridUtilityGate` | kgspin-core `kgspin_core.execution.graph_aware` (PRD-060 / 34932e7) | Import + instantiate per-article |
| `GET /registry/hubs?domain=…` | kgspin-admin (PRD-059 / 564e6fc) | New async helper `_fetch_hub_registry()`; HubEntry.from_json |
| `cross_hub` relation_kind on financial-v2 / clinical-v2 | kgspin-blueprint (PRD-058 / 9641757) | Read from extracted relations; drives bridge-edge creation |
| `compute_health` / `health_for_kg` | kgspin-core (Wave G) via `kgspin_demo_app.services.topology_health` | Call per `graph_delta` for sparkline |

## Commit plan

Six atomic commits. Tests green on each before advancing. Partial-progress fallback: if a later commit stalls, prior commits stand on their own.

---

### Commit 1 — Provenance-preserving merge + SourceRef + hub-registry client
**Covers:** MH #1 (provenance merge), MH #9 (graph-aware extraction), MH #10 (bundle-consistent normalization), part of MH #2 (hub-registry plumbing).

**Backend changes (`demos/extraction/demo_compare.py`):**

- New `_merge_kgs_with_provenance(base_kg, overlay_kg, *, admission_tokens=None, overlay_source_ref=None) -> dict`:
  - Union-with-provenance. Every entity + relationship gets `sources: list[SourceRef-as-dict]`.
  - Same normalized `(text, entity_type)` keys → merge: aliases union, `confidence = max`, `sources` union (dedup by `(kind, origin, article_id)`).
  - Legacy KGs missing `sources` receive a synthetic default `[{kind:"filing", origin:"legacy", article_id:None, fetched_at:None}]` at load time.
  - Calls `normalize_entity_text(text, admission_tokens=base_bundle_admission_tokens)` on both sides so "Johnson & Johnson Inc." and "Johnson & Johnson" collapse regardless of source bundle.
  - Overlay relationships referencing an overlay entity that collapses to a base entity are rewritten to point at the base entity id.
  - Returns a dict with `entities`, `relationships`, and carries forward other top-level keys (prefer overlay metadata when present, keep `sources` on every node/edge).
- Keep `_merge_kgs` as a **thin backward-compat wrapper** that delegates to `_merge_kgs_with_provenance`. Callers migrate to the new name in the same commit.
- Add `_fetch_hub_registry(domain: str) -> list[HubEntry]`:
  - Async (uses `asyncio.to_thread` + `urllib.request.urlopen` to match existing admin-call style in `pipeline_common.py`).
  - Calls `GET {KGSPIN_ADMIN_URL}/registry/hubs?domain={domain}`.
  - Deserializes each `hubs[i]` row via `HubEntry.from_json`.
  - Run-lifetime cache keyed by `(domain, ttl_bucket)` inside the intel run.
  - On admin unreachable: log `logger.warning("hub registry unreachable, falling back to empty registry: %s", err)` and return `[]`.
- Build `ExtractionContext` per article in `run_intelligence`:
  - `base_kg=running merged kg`
  - `hub_registry=<cached>`
  - `intel_linking_prompt=<bundle override or None>`
  - `source_ref=SourceRef(kind="news", origin=article.get("outlet") or article.get("source") or "news", article_id=article_id, fetched_at=fetched_at_iso)`
  - Pass via `extraction_context=` kwarg when calling the extractor (kwarg is optional upstream, backward compat preserved).
- Wire `_merge_kgs_with_provenance(... , admission_tokens=<base bundle admission tokens>, overlay_source_ref=<SourceRef>)` at both merge sites (lines ~7756, ~7877).

**Imports:**
```python
from kgspin_core.execution.graph_aware import (
    ExtractionContext, HubEntry, SourceRef, HybridUtilityGate,
)
```

**Tests (`tests/unit/test_merge_provenance.py`, new):**
- Union semantics: same normalized key → aliases union, `confidence = max`, `sources` union.
- SourceRef present on every node/edge after merge.
- Legacy KG without `sources` → synthetic `[filing/legacy]` default at merge time.
- Admission-token normalization: "Johnson & Johnson Inc." + "Johnson & Johnson" collapse when admission_tokens includes `inc`.
- Hub-registry fetch fallback: monkeypatch urlopen → HTTPError → empty list + WARNING log.

**Deferral-safe partial clause from CTO:** if utility-gate integration is flaky in the intel flow, commit the provenance merge by itself. For this sprint we will carry the gate integration via `HybridUtilityGate` inside commit 2; commit 1 only plumbs the context.

---

### Commit 2 — Bridge-edge creation from hub-registry matches
**Covers:** MH #2 (finish).

**Backend (`demos/extraction/demo_compare.py`):**

- New helper `_create_bridges_from_matches(merged_kg, *, current_hub, hub_registry, admission_tokens, gate, source_ref) -> dict`:
  - Walk `merged_kg.entities`. For each entity whose normalized canonical/alias matches a `HubEntry` in `hub_registry` that is **not** the current hub:
    - If the entity participates in a relationship whose `relation_kind == "cross_hub"` (or blueprint schema marks the relation label as cross-hub), record a **bridge edge** between current-hub entity and matched-hub entity. Attach `source_ref` to the bridge edge's `sources`, set `edge["kind"] = "bridge"`, carry the relation label.
    - Else, promote the matched entity to a first-class spoke with SourceRef (no bridge, but the entity is retained with provenance).
  - Bridge edge structure: `{subject_id, predicate, object_id, relation_kind: "cross_hub", kind: "bridge", sources: [source_ref], label, ...}`.
  - Ask `gate.should_commit(edge, merged_kg, emitted_so_far)` before appending a bridge edge; default `HybridUtilityGate()` — cross-hub bridges always commit per gate semantics.
- Called after each article merge inside the article loop. Track `bridges_created` list of bridge-edge payloads (for the `graph_delta` event in commit 3).

**Data model tweak:** bridge edges use `kind:"bridge"` discriminator so the frontend can style distinctly. Non-bridge cross-repo edges remain `kind:"spoke"` (default). This is a pure data addition — existing consumers see the new key as extra metadata.

**Tests (`tests/unit/test_bridge_creation.py`, new):**
- Hub-registry match + `cross_hub` relation → bridge edge created, `kind=="bridge"`, SourceRef attached, relation label preserved.
- Hub-registry match without `cross_hub` relation → matched entity promoted to spoke with SourceRef, no bridge edge.
- No hub-registry match → no bridge, no spoke promotion.
- Gate rejects → edge not emitted.

---

### Commit 3 — `graph_delta` SSE events + incremental frontend animation
**Covers:** MH #5, scaffolding for MH #6.

**Backend:**
- Inside the news-article loop in `run_intelligence`, after each merge + bridge creation, emit:
  ```python
  yield sse_event("graph_delta", {
      "article_id": article_id,
      "article_index": i,
      "added_entities": [...],
      "added_relationships": [...],
      "bridges_created": [...],
      "merged_with": base_entity_ids_touched,
      "fetched_at": article.get("fetched_at"),
      "outlet": article.get("outlet") or article.get("source"),
      "health": <health_for_kg(merged_kg_dict)>,
  })
  ```
- `kg_ready` stays as the final-state checkpoint (backward compat mandatory).

**Frontend (new `demos/extraction/static/js/intel-graph-delta.js`):**
- SSE handler for `graph_delta`: push to in-memory `deltaLog[]` array keyed by `article_index`.
- Apply to the existing vis.js `DataSet` incrementally: `nodes.add(added_entities)` + `edges.add(added_relationships + bridges_created)`, with a fade-in opacity tween (CSS or vis-network `color.opacity`).
- Bridges drawn with an emphasized highlight pulse (2s) on first appearance.
- Control bar injected above `#intelligence-graph`: Pause / Step / Resume. Pause halts delta application (deltas continue to queue); Step applies next queued delta; Resume drains the queue.

**Wire in `intelligence.js`:**
- Register the new handler alongside the existing `kg_ready` handler; `graph_delta` runs pre-finalization, `kg_ready` confirms the final state.

**Tests:**
- Unit: `_run_intelligence_graph_delta_emit` — monkey-patch the SSE emitter and assert `graph_delta` fires once per article with expected keys.
- (Playwright smoke deferred to commit 6.)

---

### Commit 4 — Bridge-distinct vis-network rendering + per-source filters
**Covers:** MH #3, MH #4.

**Frontend (`demos/extraction/static/js/graph.js` + new `intel-rendering.js`):**
- Node styling rules (driven by `sources[].kind`):
  - Filing-only → solid border (default).
  - News-only → dashed border + outlet badge in node label suffix.
  - Hybrid (filing + news) → solid border + `+N news mentions` counter in title/tooltip.
- Edge styling:
  - `kind === "bridge"` → double-weight stroke (`width: 4`), distinct color (`#E67E5B`), edge label = relation type, hover shows the union of `sources`.
  - Regular spokes unchanged.
- Per-source filter row (`#intel-source-filters`): one checkbox per distinct `source.origin` present in the current delta log. Toggling filters hides nodes/edges whose source set is exclusively that origin; multi-source items remain but their badges reflect the active filter.
- Filters run client-side only — no SSE round-trip.

**Templates (`demos/extraction/templates/compare.html`):**
- Add `<div id="intel-source-filters" class="intel-filter-row"></div>` above `#intelligence-graph`.

**Tests:**
- JS parse/smoke: load the module in a headless page fixture; covered by commit 6 Playwright.
- No pytest changes; styling is visual.

---

### Commit 5 — Timeline scrubber + Topological Health sparkline
**Covers:** MH #6, MH #7.

**Frontend:**
- New `demos/extraction/static/js/intel-scrubber.js`:
  - Input `<input type="range" min="0" max="{deltaLog.length}" step="1">` below the graph.
  - State machine: replay from empty-base up to scrub position by re-applying `deltaLog[0..n]`; cache intermediate `vis.DataSet` snapshots by `article_index` for <200ms/step performance on 100-node graphs.
  - Filings anchored at t=0; news articles ordered by `fetched_at` ascending (falls back to arrival order when `fetched_at` missing).
- New `demos/extraction/static/js/intel-sparkline.js`:
  - Renders above `#intelligence-graph` using an inline SVG (no new deps).
  - Data point per `graph_delta` event; reads `delta.health.score`.
  - Hover shows `score / delta / outlet / article_id`.
  - News-only runs: first data point = first article's score (no synthetic zero per PRD Q5).

**Templates:**
- Add `<div id="intel-sparkline"></div>` and `<input id="intel-scrubber">` to `compare.html` near `#intelligence-graph`.

**Tests:**
- No Python unit tests (frontend-only).
- Playwright coverage in commit 6.

---

### Commit 6 — Conflict stack drilldown + KNOWN_TICKERS retirement + Playwright smoke
**Covers:** MH #8, KNOWN_TICKERS retirement, end-to-end smoke.

**Drilldown (in `graph.js` or new `intel-drilldown.js`):**
- When a node/edge has an attribute with multiple values across sources, drawer renders each value as a stacked row with its SourceRef (`{kind, origin, article_id, fetched_at}`).
- Primary label in the graph viz shows newest by `fetched_at`.

**KNOWN_TICKERS retirement (`demos/extraction/pipeline_common.py`):**
- Delete `KNOWN_TICKERS` dict (lines 27–39) and the fallback branch in `resolve_ticker`.
- Replace with `_resolve_via_hub_registry(ticker, domain)` that calls admin `/registry/hubs?domain=...`, scans `ticker`/`canonical_name`/`aliases`, returns `{name, domain, ticker}`. One-shot in-process cache keyed by `(ticker, domain)`.
- Behavior on admin unreachable: raise `AdminServiceUnreachableError` with a message telling the operator to start admin. No silent fallback — PRD says "DELETED."

**Playwright smoke (`tests/playwright/test_intel_bridging.py`, new, gated on `PLAYWRIGHT_E2E=1`):**
- Spin up a stubbed admin + stubbed extractor via existing fixtures.
- Load the demo page, click "Run Intelligence" on JNJ (seeded with 3 fake articles, one co-mentioning Merck with a `partnered_with` relation).
- Assert:
  - 3 `graph_delta` events reach the DOM (check via `window.__deltaLog.length === 3`).
  - Sparkline has ≥3 SVG data points.
  - Source-filter checkboxes appear for each present source origin.
  - At least one edge in the final graph has `data-kind="bridge"`.
  - Scrubber at position 0 shows only the base KG; at max shows the full merged graph.

**Integration test (`tests/integration/test_intel_bridging_flow.py`, new):**
- Mocks admin `/registry/hubs` (JNJ + MRK hubs) and the extractor.
- Runs `run_intelligence` for JNJ across 3 articles; asserts ≥1 bridge edge + correct `graph_delta` sequence.

---

## File-touch summary

**Backend:**
- `demos/extraction/demo_compare.py` — new merge, hub-registry client, bridge creation, `graph_delta` emits (commits 1–3).
- `demos/extraction/pipeline_common.py` — KNOWN_TICKERS retirement (commit 6).

**Frontend (new modules, all under `demos/extraction/static/js/`):**
- `intel-graph-delta.js` (commit 3)
- `intel-rendering.js` (commit 4)
- `intel-scrubber.js`, `intel-sparkline.js` (commit 5)
- `intel-drilldown.js` (commit 6)
- Plus additive edits to `graph.js` and `intelligence.js`.

**Templates:**
- `demos/extraction/templates/compare.html` — source-filter row, sparkline container, scrubber input (commits 4–5).

**Tests:**
- `tests/unit/test_merge_provenance.py` (commit 1)
- `tests/unit/test_bridge_creation.py` (commit 2)
- `tests/integration/test_intel_bridging_flow.py` (commit 6)
- `tests/playwright/test_intel_bridging.py` (commit 6, gated)

## Backward compat checklist

- `_merge_kgs` still exists as a thin wrapper (delegates to new fn, strips `sources` only if caller is truly legacy — we will keep `sources` by default, which is additive).
- `kg_ready` SSE event unchanged; `graph_delta` is purely additive.
- Legacy KGs without `sources` → synthetic default injected at merge time.
- `extraction_context=` kwarg on extractor is optional upstream (PRD-060 contract).

## Non-scope (reaffirmed)

- No changes to extractor algorithms (kgspin-core Wave I scope).
- No admin endpoint shape changes (PRD-059 locked).
- No Wave-G SEC-lander changes.
- No Nice-to-Haves from PRD-056 (source-conflict glyph, news-only intel mode, bridge-target suggestions, time-windowed replay, HITL curation).
- No auto fan-out, trust tiers, time-decay, Factoring, Promotion.

## Risk + mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| Utility-gate integration flaky in intel path | Medium | CTO-approved deferral: commit 1 without gate, gate-aware bridge creation isolated in commit 2. Fall back to `should_commit` returning True for all cross-hub bridges. |
| Admin unreachable mid-run | Medium | Warning log + empty registry fallback (merge still runs). Only KNOWN_TICKERS retirement (commit 6) hard-fails — that's explicit PRD guidance. |
| SSE queue backpressure vs Pause control | Low | Client buffers deltas in a queue; Pause halts consumption, not emission. |
| Scrubber perf on larger graphs | Low | Cache per-step `vis.DataSet` snapshots; PRD target 100 nodes. |
| Frontend regressions in existing `kg_ready` consumers | Low | `kg_ready` unchanged; additive `graph_delta` only. Commit boundaries allow per-commit smoke. |

## Completion criteria

- All 10 Must-Haves implemented across commits 1–6.
- `pytest` green on each commit.
- Playwright smoke green when `PLAYWRIGHT_E2E=1`.
- Branch `wave-j-intelligence-bridging` pushed to remote.
- Dev-report at `docs/sprints/wave-j-intelligence-bridging/dev-report.md`.
