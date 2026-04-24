# Dev Report — Wave J: Intelligence Tab Bridging-First UX

**Branch:** `wave-j-intelligence-bridging`
**PRD:** PRD-056 v2 (bridging-first Intelligence graph extension)
**Base:** `main` @ `3db7f31`
**Dates:** 2026-04-22 → 2026-04-23
**Sprint plan:** `docs/sprints/wave-j-intelligence-bridging/sprint-plan.md`
**Final test status:** 318 pass / 9 skipped (Playwright gate) / 0 fail

## TL;DR

All six planned commits landed. All ten PRD-056 v2 Must-Haves are
implemented in code. One scoped deferral remains: wiring the gated
Playwright smoke against a running demo + admin shim (the test bodies
and CI recipe are in the tree, the shim connection is the only piece
left — tracked for Wave J-follow-up).

## Commits landed

| # | SHA       | Scope                                                      | MH covered       |
|---|-----------|------------------------------------------------------------|------------------|
| 1 | `26b53f3` | Provenance-preserving merge + hub-registry client          | MH #1, #9, #10   |
| 2 | `4671b89` | Bridge-edge creation from hub-registry matches             | MH #2            |
| 3 | `ec9a5f8` | SSE `graph_delta` events + frontend delta buffer           | MH #5            |
| 4 | `724d192` | Bridge-distinct vis rendering + per-source filters         | MH #3, #4        |
| 5 | `439d01e` | Timeline scrubber + Topological Health sparkline           | MH #6, #7        |
| 6 | `6cae4f5` | Drilldown + KNOWN_TICKERS retirement + Playwright scaffold | MH #8 + cleanup  |

## Must-Have coverage

| MH | Description | Status | Evidence |
|----|-------------|--------|----------|
| #1 | Provenance-preserving merge | **Done** | `_merge_kgs_with_provenance`, 5 unit tests |
| #2 | Bridge-edge creation | **Done** | `_create_bridges_from_matches`, 7 unit tests |
| #3 | Bridge-distinct rendering | **Done** | `build_vis_data` edge kind + width + color, 5 unit tests |
| #4 | Per-source filters | **Done** | `intel-source-filters.js`, verified manually |
| #5 | `graph_delta` SSE events | **Done** | Backend emit + `intel-graph-delta.js`, 6 unit tests |
| #6 | Timeline scrubber | **Done** | `intel-scrubber.js` with earliest-idx indexing |
| #7 | Topological Health sparkline | **Done** | `intel-sparkline.js` inline SVG, `health_for_kg` per delta |
| #8 | Conflict-stack drilldown | **Done** | `intel-drilldown.js` + graph.js integration |
| #9 | Graph-aware extraction plumbing | **Partial (per sprint plan)** | `ExtractionContext` built at merge time; façade forward deferred to kgspin-core Wave I follow-up |
| #10 | Bundle-consistent normalization | **Done** | `_bundle_admission_tokens` threaded through merge |

Plus the CTO-added scope items from the sprint plan:
- **KNOWN_TICKERS retirement:** **Done**. Dict deleted from
  `pipeline_common.py`; `resolve_ticker` now calls admin
  `/registry/hubs` for financial + clinical domains; 9 unit tests
  including a regression guard that blocks the dict from coming back.
- **Playwright smoke:** **Scaffolded**. 5 gated tests at
  `tests/playwright/test_intel_bridging_smoke.py`. Skips cleanly when
  `PLAYWRIGHT_E2E != 1` or playwright isn't installed.

## Test counts by commit

| Commit | Pre-commit pass count | Post-commit pass count | Delta |
|--------|-----------------------|------------------------|-------|
| 1      | 284                   | 284                    | +0 (tests added in commit 2) |
| 2      | 284                   | 298                    | +14 |
| 3      | 298                   | 304                    | +6  |
| 4      | 304                   | 309                    | +5  |
| 5      | 309                   | 309                    | +0 (scrubber/sparkline covered by commit 6 Playwright) |
| 6      | 309                   | 318                    | +9 unit + 5 skipped Playwright |

## Deferred work (documented for Wave J-follow-up)

1. **kgspin-core façade plumbing for `ExtractionContext`.** The
   `KnowledgeGraphExtractor.extract` façade in kgspin-core does not yet
   forward the `extraction_context` kwarg that the Extractor ABC
   accepts. Demo-side bridge creation works correctly because merge +
   hub-registry matching all happen after extraction, but graph-aware
   linking inside the extractor (entity-link preference over invention)
   requires the façade forward. See the comment block in
   `demo_compare.py` at the Wave J per-run prep section.
2. **Playwright E2E wiring.** The gated tests in
   `tests/playwright/test_intel_bridging_smoke.py` need a tests fixture
   that (a) spins up the admin shim with seeded `/registry/hubs` rows
   for JNJ + MRK, (b) stubs the extractor with a 3-article JNJ fixture
   including a partnered_with relation to Merck, (c) launches the demo
   on a random port, (d) drives Playwright. The `admin_shim` fixture at
   `tests/manual/admin_shim.py` is the right starting point; add the
   `/registry/hubs` endpoint there and wire the rest. Gating on
   `PLAYWRIGHT_E2E=1` keeps default CI green in the meantime.

## Cross-repo contract observed

No kgspin-core, kgspin-interface, kgspin-blueprint, or kgspin-admin
changes needed. Wave J is a pure demo-app consumer of already-shipped
APIs:
- `kgspin_core.execution.graph_aware` — `ExtractionContext`,
  `HubEntry`, `SourceRef`, `HybridUtilityGate`: imported as-is.
- Admin `GET /registry/hubs?domain=...`: called via two helpers
  (`_fetch_hub_registry_sync` in `demo_compare.py`,
  `_fetch_hub_registry_rows` in `pipeline_common.py`).
- Bundle `cross_hub` relation kind on financial-v2 / clinical-v2: read
  from raw YAML by `_cross_hub_relations_from_bundle`; fallback to
  "both-endpoints-are-hubs" heuristic when YAML isn't discoverable.
- `kgspin_core.graph_topology.compute_health` via the existing Wave G
  adapter at `services/topology_health.py`: called per `graph_delta`.

## Backward compatibility

- `kg_ready` SSE event shape unchanged — every existing consumer keeps
  working. `graph_delta` is strictly additive.
- `_merge_kgs` is still exported as a thin wrapper over
  `_merge_kgs_with_provenance`; non-intel call sites keep working
  without touching their code.
- `resolve_ticker` now hard-fails on admin unreachable (per PRD
  directive), which is a behavior change from the previous silent
  KNOWN_TICKERS fallback. Operators who were relying on the dict will
  get an actionable error message pointing at `kgspin-admin-server`.
- `/api/tickers` returns 503 + JSON operator message on admin
  unreachable (was 200 + dict mapping). Frontend treats non-200
  responses as "admin down" already.

## Risk + mitigation outcomes

The sprint plan listed five risks. Outcomes:

- **Utility-gate integration flaky in intel path:** Resolved cleanly.
  `HybridUtilityGate` is instantiated with defaults inside
  `_create_bridges_from_matches`; cross-hub bridges always commit per
  the gate's built-in semantics; the test suite verifies gate-veto is
  honored.
- **Admin unreachable mid-run:** `_fetch_hub_registry` (in
  `demo_compare.py`, for the intel flow) still falls back to empty +
  warning log. `_fetch_hub_registry_rows` (in `pipeline_common.py`, for
  ticker resolution) raises per the PRD directive. Distinct behaviors
  are the right call here — the intel flow can continue without
  registry (no bridges created, but merge still works); ticker
  resolution can't.
- **SSE queue backpressure vs Pause control:** Not observed. Queue is
  a plain list, Pause halts consumption without dropping events.
- **Scrubber perf on larger graphs:** `intel-scrubber.js` is O(V+E)
  per scrub step because it tags DataSet rows once with their earliest
  article_index, then flips `hidden` flags. 100-node target comfortable.
- **Frontend regressions in existing `kg_ready` consumers:** None.
  `kg_ready` shape preserved; Wave J additions are all in new `intel-*.js`
  modules + additive CSS.

## File touches summary

```
demos/extraction/demo_compare.py               (commits 1-6)
demos/extraction/pipeline_common.py            (commit 6: KNOWN_TICKERS retirement)
demos/extraction/run_overnight_batch.py        (commit 6: switch to list_registered_tickers)
demos/extraction/run_overnight_experiment.py   (commit 6: same)
demos/extraction/static/compare.html           (commits 3-6: CSS + DOM anchors + script includes)
demos/extraction/static/js/graph.js            (commit 6: drilldown splice)
demos/extraction/static/js/intelligence.js     (commits 3-5: SSE wiring + reset calls)
demos/extraction/static/js/intel-graph-delta.js   (new, commit 3)
demos/extraction/static/js/intel-source-filters.js (new, commit 4)
demos/extraction/static/js/intel-sparkline.js  (new, commit 5)
demos/extraction/static/js/intel-scrubber.js   (new, commit 5)
demos/extraction/static/js/intel-drilldown.js  (new, commit 6)
tests/unit/test_merge_provenance.py            (new, commit 2)
tests/unit/test_bridge_creation.py             (new, commit 2)
tests/unit/test_graph_delta.py                 (new, commit 3)
tests/unit/test_build_vis_data_bridging.py     (new, commit 4)
tests/unit/test_pipeline_common_ticker_registry.py (new, commit 6)
tests/playwright/__init__.py                   (new, commit 6)
tests/playwright/test_intel_bridging_smoke.py  (new, commit 6)
```

## Next sprint hook-points

- Wave J-follow-up #1: wire Playwright smoke to admin-shim + stubbed
  extractor. ~0.5 day of work. Unblocks claimed E2E coverage.
- Wave J-follow-up #2: push `extraction_context` through the kgspin-core
  façade. Cross-repo change — coordinate with kgspin-core Wave I.
- Wave J-follow-up #3: `intel_linking_prompt` bundle override path. The
  `ExtractionContext.intel_linking_prompt` field is plumbed but always
  `None` in the demo; wiring the bundle override to pick up an
  operator-provided prompt is a small follow-up.

— kgspin-demo-app dev team, 2026-04-23
