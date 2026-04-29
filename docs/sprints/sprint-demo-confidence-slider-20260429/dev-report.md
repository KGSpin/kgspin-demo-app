# Sprint dev report — Demo confidence-floor slider (operator-tunable)

**Branch:** `sprint-demo-confidence-slider-20260429`
**Date:** 2026-04-29
**Driver:** CEO directive — the 0.55 floor was hiding mid-confidence
entities from HITL operators (e.g. UNH main entity at 0.5232).

## Summary

Lowered the demo's hardcoded confidence-floor default from `0.55` → `0.5`
and surfaced an operator-tunable slider in the **Settings panel** (not
the main toolbar — keeps the toolbar clean). The floor flows from the
slider through the existing query-arg precedence chain (`query > admin
pipeline_config > hardcoded fallback`) that Sprint 12 Task 8 wired up,
so admin overrides still win when set. `build_vis_data(...)` default
also moved to `0.5` so any code path that bypasses the resolver lands
on the new default.

## Where the Settings control lives

- **HTML element:** `demos/extraction/static/compare.html:1619-1626` —
  range input `#settings-confidence-floor` (min=0, max=1, step=0.05,
  value=0.5) inside the existing `#settings-panel` block, with a live
  numeric label `#settings-confidence-floor-value` next to it.
- **Sync handler:** `demos/extraction/static/js/settings.js:100`
  (`syncConfidenceFloorSetting`, registered as
  `sync-confidence-floor-setting`) updates the label on input.
- **Read-by-runners:** `getSettingsConfidenceFloor()` in
  `settings.js:113` is called by:
  - `demos/extraction/static/js/compare-runner.js:1080` (initial
    `compare`/`compare-clinical` run)
  - `demos/extraction/static/js/slots.js:335` (per-slot refresh runs)

### Note on the JS name collision (avoided)

`graph.js:1533` already defined a `getConfidenceFloor()` reading the URL
param (Sprint 90 Task 10 debug badge). Since `graph.js` loads after
`settings.js`, a same-named function on the new slider would have been
silently overwritten — runners would have read the URL param instead of
the slider. Renamed the new function to `getSettingsConfidenceFloor()`
to avoid the collision; updated `graph.js`'s default `0.55 → 0.5` so
the badge no longer triggers on a vanilla load.

## Persistence approach

**DOM-resident only**, matching the existing settings-panel pattern.
The other Settings controls (`#settings-model-select`,
`#settings-corpus-select`) sync into the main toolbar but do not write
to `localStorage` (no `localStorage` usage anywhere in
`demos/extraction/static`). The slider follows the same pattern: its
value lives in the DOM element across panel toggles within a session,
and is read at run-start by `getSettingsConfidenceFloor()`. No new
persistence layer was invented.

## Re-render behavior

`build_vis_data(...)` runs **server-side** during the SSE stream, so a
slider change does not re-render cached results client-side. The new
floor takes effect on the **next** compare/refresh run started from the
UI — the runners attach `confidence_floor=<slider>` as a query arg, the
server's `_resolve_confidence_floor()` consumes it (precedence: query
arg > admin pipeline_config > hardcoded `0.5`), and downstream
`build_vis_data(...)` calls receive it via the threaded
`confidence_floor` parameter on `run_comparison`, `run_single_refresh`,
`_run_kgen_refresh`, and `_run_clinical_comparison`.

## Surprising findings

1. **`_DEFAULT_CONFIDENCE_FLOOR` had to move up the file.** It was
   previously defined at the original Sprint 12 Task 8 location (line
   ~1899), but the new design uses it as a default arg in helpers
   defined earlier (`_cached_kg_event` ~1124, `_fresh_kg_event` ~1180).
   Default args are evaluated at module import time, so a forward
   reference would have been a `NameError` at import. Moved the
   constant up to line 553 (alongside other top-level demo constants
   like `DEFAULT_CHUNK_SIZE`) — single source of truth, no duplicate.
2. **Two functions named `getConfidenceFloor` would have shipped
   silently broken.** See "name collision" note above. Caught by
   grepping references before committing.
3. **Tests do not depend on the demo default.** The only test file
   referencing `confidence_floor` (`tests/unit/test_prompt_and_params.py`)
   passes `defaults={"confidence_floor": 0.55}` *explicitly* to the
   admin-registry reader — the tests verify the reader's
   "use-the-defaults-you-were-given" semantics, independent of any
   demo-side constant. All 12 params tests still pass.

## Smoke test (programmatic)

```
build_vis_data with 3 entities at confidence 0.6 / 0.4 / 0.52:
  default floor (0.5) → 2 nodes  (drops 0.4)
  floor=0.55          → 1 node   (drops 0.4 and 0.52 — old behavior)
  floor=0.0           → 3 nodes  (keeps all)
```

Confirms the threshold change unblocks the UNH-at-0.5232 case (now
visible at the new default; previously filtered).

## Files changed

- `demos/extraction/demo_compare.py` — constant move + `build_vis_data`
  default + `confidence_floor` parameter threaded through 4 SSE
  generators (`run_comparison`, `run_single_refresh`,
  `_run_kgen_refresh`, `_run_clinical_comparison`) and 4 endpoints
  (`compare`, `refresh_agentic_flash`, `refresh_agentic_analyst`,
  `refresh_discovery`, `compare_clinical`).
- `demos/extraction/static/compare.html` — slider + label in Settings
  panel.
- `demos/extraction/static/js/settings.js` — `syncConfidenceFloorSetting`
  + `getSettingsConfidenceFloor` + action registration.
- `demos/extraction/static/js/compare-runner.js` — adds
  `confidence_floor` query arg on initial run.
- `demos/extraction/static/js/slots.js` — adds `confidence_floor` query
  arg on per-slot refresh.
- `demos/extraction/static/js/graph.js` — default `0.55 → 0.5` (debug
  badge threshold).
