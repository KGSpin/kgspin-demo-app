# ADR-001: Placement of `kg_filters` — vendor in `kgspin-demo`

**Status:** Accepted
**Date:** 2026-04-12
**Deciders:** Dev Team (per Sprint 01 plan for INIT-001)
**Supersedes:** n/a
**Superseded by:** n/a

---

## Context

`demos/extraction/demo_compare.py:83` imports two symbols from `kgenskills.utils.kg_filters`:

- `filter_kg_for_display`
- `compute_schema_compliance`

The `kgenskills.*` namespace no longer exists — it was dissolved during the open-core refactor that produced `kgspin-core`, `kgspin-interface`, and the `kgspin-plugin-*` family. A direct search in `../kgspin-core/src/kgspin_core/` confirms that neither the `utils/kg_filters.py` file nor equivalent symbols exist under the core package. The functions are also not present in `kgspin-interface` or in any plugin repo surveyed.

Without resolution, `demo_compare.py` cannot import at all, which blocks the entire Sprint 01 smoke test and every downstream sprint.

## Decision

**Vendor `kg_filters` locally in this repo under `kgspin_demo.utils.kg_filters`.**

The module will live at `src/kgspin_demo/utils/kg_filters.py` and expose the two required symbols. For Sprint 01, the implementation is a minimal typed stub (see "Implementation" below). Upstreaming to `kgspin-core` is explicitly rejected for now.

## Rationale

Three options were considered:

### Option A — Vendor in `kgspin_demo` (chosen)

**Pros:**
- Zero cross-repo coordination; the sprint stays atomic and closes on this branch alone.
- `kg_filters` is display-layer logic — it filters and scores knowledge-graph dicts specifically for the compare UI. That logic is tightly coupled to the demo's presentation concerns, not to the extraction engine.
- Keeping UI-adjacent code in `kgspin_demo.utils` respects the open-core separation: `kgspin-core` is the extraction engine and should not accumulate display helpers.
- Matches the precedent set by `src/kgspin_demo/services/entity_resolution.py`, which was also vendored locally during the monolith split rather than pushed upstream.

**Cons:**
- If a second consumer of these helpers appears (a future web app, an admin console, another demo), we'll have duplication. Mitigated by the "Conditions for Revisiting" section below.

### Option B — Upstream to `kgspin-core`

**Pros:**
- Single canonical home for the logic.

**Cons:**
- Requires a PR against `kgspin-core`, a code review cycle, and a version bump — breaking the Sprint 01 atomic-branch model.
- Pollutes `kgspin-core`'s public surface with display-layer concerns that have nothing to do with extraction correctness. The function names (`filter_kg_for_display`, `compute_schema_compliance`) make the display-layer coupling explicit.
- There is currently only one consumer. Upstreaming on the promise of future reuse is premature abstraction.

### Option C — Stub that raises NotImplementedError

**Pros:**
- Cheapest path.
- Loud failure when the code path is exercised, preventing silent data corruption.

**Cons:**
- Blocks any test or code path in `demo_compare.py` that calls either function. While Sprint 01's smoke test does not hit these code paths (it only tests boot + port bind), Sprint 02 will, and we'd immediately need to replace the stub.
- Does not let us defer the real implementation cleanly — if anyone runs an extraction by accident, the demo crashes.

**Chosen: Option A with passthrough-style stubs for Sprint 01, with a Sprint 02 task to verify behavior when real data flows through.** The stubs are safe because:
- `filter_kg_for_display` returning input unchanged is behaviorally identical to "no filtering applied" — the downstream scoring reflects raw data instead of filtered data, which produces conservative (pessimistic) compliance numbers, never false positives.
- `compute_schema_compliance` returning a zeroed-out shape satisfies every call site's structural expectations and produces visually-zero compliance reports in the UI, which is obviously-degraded rather than subtly-wrong.

## Function Contracts (derived from call sites, not from memory)

These contracts are derived by reading how `demo_compare.py` calls each function, not by guessing at the lost original. Evidence citations accompany each element.

### `filter_kg_for_display(kg)`

- **Signature:** `def filter_kg_for_display(kg: dict | None) -> dict | None`
- **Parameter `kg`:** a knowledge-graph dict (with at least `entities` and `relationships` keys, based on downstream usage in scoring). May be `None` when the caller has no data for a given extraction slot.
- **Returns:** the same shape as the input. Callers use the return value as a drop-in replacement for the raw kg dict and pass it to `compute_diagnostic_scores`, `run_quality_analysis`, and `compute_schema_compliance`.
- **Evidence:** call sites at [demo_compare.py:1236-1238](../../../demos/extraction/demo_compare.py#L1236-L1238), [:1264-1266](../../../demos/extraction/demo_compare.py#L1264-L1266), [:5010-5012](../../../demos/extraction/demo_compare.py#L5010-L5012). All three sites follow the pattern `f_x = filter_kg_for_display(x) if x else None`, confirming the `None` handling is done by the caller — the function itself can assume non-None input, but the safer contract is to accept `None` and return `None`.
- **Sprint 01 stub behavior:** return the input unchanged.

### `compute_schema_compliance(kg, valid_types)`

- **Signature:** `def compute_schema_compliance(kg: dict, valid_types: set[str] | Iterable[str]) -> dict`
- **Parameter `kg`:** knowledge-graph dict (non-None at call sites).
- **Parameter `valid_types`:** set or iterable of type name strings. Built upstream from a schema definition.
- **Returns:** a dict with the following keys (observed by the f-string formatting at the call site immediately after):
  - `compliance_pct` — numeric; formatted directly into a percentage string
  - `on_schema` — int; used as `x['on_schema']/x['total']` fraction in the display
  - `total` — int; same fraction
  - `off_schema_types` — list/iterable of strings; truthy-checked then joined with `', '.join(...)` so list or tuple of strings satisfies the contract
- **Evidence:** call sites at [demo_compare.py:3941-3943](../../../demos/extraction/demo_compare.py#L3941-L3943) and [:4135-4140](../../../demos/extraction/demo_compare.py#L4135-L4140). Return-shape evidence at [:3946-3948](../../../demos/extraction/demo_compare.py#L3946-L3948).
- **Sprint 01 stub behavior:** return `{"compliance_pct": 0, "on_schema": 0, "total": 0, "off_schema_types": []}`.

## Conditions for Revisiting

Upstream this decision (move `kg_filters` to `kgspin-core` or to `kgspin-interface`) when any of the following become true:

1. **A second consumer emerges.** If a web app, admin console, or another demo surface imports `filter_kg_for_display` or `compute_schema_compliance`, the duplication cost outweighs the coupling cost and the module belongs in a shared package.
2. **The filtering logic gains extraction-semantic rules.** If the filters start making decisions based on pipeline internals (e.g., cross-referencing extractor confidence scores, chunker decisions), that's a signal the logic has drifted from display-layer to engine-layer and should move with it.
3. **Sprint 02 or later reconstructs the real implementation and it references `kgspin_core` internals.** In that case the natural home is `kgspin-core`, not here.

## Implementation

For Sprint 01, the implementation is a typed passthrough stub. A module-level docstring on `kg_filters.py` will:

- Reference this ADR by number.
- State explicitly that the functions are stubs (not the original implementation).
- List the Sprint 02 exit criterion: "Replace stubs with functional implementations before the first end-to-end extraction run. Failure to do so will produce visually-zero compliance numbers and unfiltered graph displays, which is obviously-degraded rather than subtly-wrong."

No public API beyond the two required symbols will be exported.

## Consequences

**Positive:**
- Sprint 01 closes on this branch alone with no upstream dependencies.
- The open-core boundary stays clean — `kgspin-core` is not polluted with display-layer helpers.
- Future upstreaming, if needed, is a straightforward move-and-rename.

**Negative:**
- Two call sites for the logic exist (the stub here, and any real implementation that lands later). Sprint 02 must replace the stubs, not duplicate them.
- Passthrough stubs produce degraded output if run against real data. This is acceptable because the Sprint 01 smoke test does not exercise these code paths — it only verifies `import` and port binding. Sprint 02 planning must include a "replace stubs" task before any extraction runs.

**Neutral:**
- The `kgspin_demo.utils` namespace is introduced. It previously did not exist in this repo. Future utility modules scoped to the demo will live here.
