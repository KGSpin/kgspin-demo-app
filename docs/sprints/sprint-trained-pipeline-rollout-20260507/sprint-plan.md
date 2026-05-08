# Trained Pipeline Rollout — Sprint Plan (kgspin-demo-app, repo 5 of 5)

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-05-07
**Sprint slug:** `sprint-trained-pipeline-rollout-20260507`
**Branch (eventual EXECUTE):** `sprint-trained-pipeline-rollout-20260507` cut from `main`
**Mode this round:** PLAN (sprint plan only; do not execute yet)

Cross-repo context: `/tmp/cto/sprint-trained-pipeline-rollout-20260507/_shared-context.md`.
This repo's role: ship the side-by-side UI that lets a demo-watcher pick `fan_out` and `fan_out_trained` against the **same** AAPL 10K passage and visually compare extractions, with a small numeric diff panel.

---

## 1. Goal

Surface `fan_out_trained` as a first-class option in the existing /compare slot UI so a presenter can stage `fan_out` and `fan_out_trained` side-by-side on AAPL 10K, see the entity sets + per-type counts + per-slot latency, and read a small "what changed" diff (only-in-A / only-in-B / agreed) under the slots. When a bundle has no `entity_recognition_model` field registered, the trained slot must surface the typed `MissingDomainModelError` from kgspin-core as a clear red panel — never a silent fallback to the heuristic path.

The compare UI already supports three independent pipeline slots driven by a per-slot dropdown (`compare.html` lines 2103–2225, wired through `static/js/slots.js:89` `PIPELINE_META`). Most of this sprint is additive: one new dropdown option, one new branch in the strategy whitelist, one error-rendering reason key, and one new "diff" widget on the existing #compare-content section. We do **not** invent a parallel comparison surface.

---

## 2. Reconnaissance findings (drives the rest of the plan)

### 2.1 The compare UI is already a slot-comparison surface

`demos/extraction/static/compare.html` defines three identical slot panels (`slot-0` / `slot-1` / `slot-2`). Each has its own pipeline `<select>`, bundle `<select>`, run button, graph container, and stats — see lines 2100–2264. Pipeline metadata is centralized in `demos/extraction/static/js/slots.js:89-95` (the `PIPELINE_META` table). The demo presenter already plays slots against each other manually (the entire `/compare` page exists for that exercise). **The "side-by-side comparison UI" the goal asks for is therefore a small extension of this surface, not a new tab.**

### 2.2 Pipeline strategy is a closed whitelist on the wire

`demos/extraction/demo_compare.py:1851` defines `CANONICAL_PIPELINE_STRATEGIES = ("fan_out", "discovery_rapid", "discovery_deep", "agentic_flash", "agentic_analyst")`. `_canonical_pipeline_name` (1866) maps these to the hyphenated admin pipeline name. Anything outside the whitelist returns 400 (`InvalidPipelineStrategyError`, 1860). The SSE entry point `/api/refresh-discovery/{doc_id}` (2126) accepts `strategy=` directly. **Adding `fan_out_trained` is a one-tuple-entry change here — the rest of the routing plumbs through unchanged because the canonical→admin name mapping is the simple `_`→`-` replace.**

### 2.3 Slot failures already render as red overlays

`demos/extraction/static/js/graph.js:857` defines `renderSlotFailure(slotIdx, reason, message, errorType)`, called both by the live SSE error handler and the cached-replay path (`slots.js:257`). The reason→copy table is `LLM_FAILURE_COPY` at `graph.js:830`. **We add one new key — `'missing_domain_model'` — and the existing rendering path handles it with no other changes.** The SSE event shape that `slots.js` consumes is `{step, pipeline, reason, message, recoverable, error_type}` (see `_run_kgen_refresh` ~ line 7065 in `demo_compare.py` for the existing emit pattern).

### 2.4 The default bundle has no model field today

`bundles/domains/financial-v22d.yaml` is the demo's default financial bundle. It has no `entity_recognition_model` block (verified). kgspin-blueprint adds the optional field in this sprint; kgspin-admin registers v0.4.0; kgspin-core consumes it from `bundle.entity_recognition_model` and raises `MissingDomainModelError` on misses. **For this sprint we assume blueprint+admin+core all land before us and that running `fan_out_trained` against `financial-v22d` succeeds.** Until they do, our smoke test specifically exercises the error path (which is independently valuable demo coverage).

### 2.5 AAPL 10K is already the demo's anchor ticker

The demo fetches AAPL via `_try_corpus_fetch` (SEC EDGAR live fetcher with on-disk cache at `~/.kgspin/corpus/financial/sec_edgar/AAPL/...`). The CDE smoke fixture (`kgspin-domain-morphology/data/runs/2026-04-30-cde-smoke/smoke_results.json`) uses 200-char passages drawn from the same AAPL 10K — **we re-use one of those passages** as the canned demo input rather than auto-chunking the full 10K, because the goal is "see the extraction-quality difference at a glance," not "stress-test chunking." See §4.3 for the chosen passage.

### 2.6 Prophet / LLM cost on `fan_out`

`fan_out` is labelled "0 tokens" in the existing pipelines-help.html (line 79–87) but the shared context describes it as "heuristic / LLM dispatch via Prophet." In the codebase, `_run_kgenskills` runs the KGSpin (zero-LLM) extractor via `KnowledgeGraphExtractor.run_pipeline()`; Prophet callouts (when present) are gated by per-bundle config and typically batch a few classification calls per chunk. For our chosen ~600-token AAPL passage, the empirical Prophet usage from prior runs has been ≤ 2 calls × ≤ 500 input tokens each = **~1k tokens / run** — under $0.005 at Haiku-class pricing. See §9 for the full estimate.

---

## 3. UI design

### 3.1 Slot dropdown — new option

Add `fan_out_trained` to the existing "Fan-out" `<optgroup>` in all three slot dropdowns (`compare.html` lines 2105–2115, 2160–2170, 2215–2225). Visible label: **"Signal Fan-out (trained)"** and subtitle "Domain-trained model · 0 tokens". Color: keep `#5ED68A` (KGSpin green) so the user reads "this is still a deterministic KGSpin pipeline, just one with a learned model behind the entity-recognition step." (We deliberately do not introduce a new color — the demo already telegraphs zero-LLM = green, and trained ≠ LLM.)

We update `PIPELINE_META` in `static/js/slots.js:89` accordingly:

```js
'fan_out_trained':   { label: 'Signal Fan-out (trained)', subtitle: 'Domain-trained · 0 tokens',
                       backend: 'kgenskills', strategy: 'fan_out_trained',
                       isKgspin: true, color: '#5ED68A',
                       capability: 'Fan-out', helpAnchor: 'fan-out-trained' },
```

### 3.2 The existing slot panels are the comparison view

The presenter sets:
- slot-0 → `fan_out` + `financial-v22d`
- slot-1 → `fan_out_trained` + `financial-v22d`
- slot-2 → free for `agentic_analyst` or empty

Each slot already shows: pipeline label, subtitle, latency (in `slot-N-stats` after `renderGraph`), graph, and per-slot legend. The only thing missing is a "what changed between slot-0 and slot-1" widget — that's §3.3.

### 3.3 New "diff" panel — small, lives under the slots

Add a single new section under `#compare-content` (after `#diagnostic-scores` at line 2275), id `#trained-diff-panel`, hidden by default. It auto-shows when both a `fan_out`-strategy slot and a `fan_out_trained`-strategy slot have completed successfully (regardless of which slot index they occupy — we detect by strategy, not slot index).

Layout (description, no mockup needed — it's three counters + three short lists):

```
+--- Trained vs. Heuristic Diff (financial-v22d, AAPL 10K passage P-1) -----+
|                                                                            |
| Per-type counts                                                            |
|  COMPANY:    fan_out=12   fan_out_trained=14   Δ +2                        |
|  PERSON:     fan_out= 4   fan_out_trained= 5   Δ +1                        |
|  PRODUCT:    fan_out= 7   fan_out_trained= 6   Δ -1                        |
|  ...                                                                       |
|                                                                            |
| Set diff (by surface form, normalized casefold + whitespace)               |
|  Only in fan_out          ( 3): "Apple", "iPhone", "FY24"                  |
|  Only in fan_out_trained  ( 5): "Apple Inc.", "iPhone 15", ...             |
|  Agreed                   (18): "Tim Cook", "California", ...              |
|                                                                            |
+----------------------------------------------------------------------------+
```

Aggregation runs entirely client-side from the two slots' cached `kg.entities[]` arrays — no new API. The "Agreed / Only-in-X" set diff is a casefold + whitespace-normalize on the entity surface text, scoped within the same entity type. (Cross-type collisions — e.g. "Apple" as ORG vs PRODUCT — are NOT folded into the same bucket; that would mis-state agreement.)

### 3.4 Help page — new section

Add `#fan-out-trained` section to `demos/extraction/static/pipelines-help.html` after the existing `#fan-out` section (line 79). Two paragraphs: (a) what it does (uses the bundle's `entity_recognition_model` for the entity-recognition step within fan-out; everything else identical), (b) what happens when the model is missing (typed error, fail-fast, no silent fallback). Add it to the in-page TOC at line 53.

---

## 4. Tasks

Ordered by dependency. Time estimates are dev-hours of focused work; tests are net-new test counts.

### Task 1 — Whitelist `fan_out_trained` on the wire

- **File:** `demos/extraction/demo_compare.py` line 1851 (CANONICAL_PIPELINE_STRATEGIES) — add `"fan_out_trained"` to the tuple.
- **Files (sweep):** `demos/extraction/run_abc_comparison.py`, `run_kgen_smell_test.py`, `routes/corpus.py` — verify the strategy whitelist is centralized through `_canonical_pipeline_name` (it already is per recon §2.2). No change needed if so; if any local copy of the tuple exists, sync it (currently zero copies).
- **Tests:** 1 unit test `tests/test_pipeline_strategy_whitelist.py::test_fan_out_trained_canonicalizes` confirming `_pipeline_ref_from_strategy("fan_out_trained")` returns `PipelineConfigRef(name="fan-out-trained", version="v1")`.
- **Estimate:** 30 min, +1 test.

### Task 2 — JS slot metadata + dropdown options

- **Files:**
  - `demos/extraction/static/js/slots.js:89` — add `'fan_out_trained'` entry to `PIPELINE_META` (see §3.1).
  - `demos/extraction/static/js/settings.js:20` — add the friendly label mapping.
  - `demos/extraction/static/compare.html` — add `<option value="fan_out_trained">Signal Fan-out (trained)</option>` to the Fan-out optgroup in all three slots (lines 2110, 2165, 2220).
- **Tests:** none (HTML-static); §4.5 smoke covers it E2E.
- **Estimate:** 20 min.

### Task 3 — Render `MissingDomainModelError` as a slot failure

- **Files:**
  - `demos/extraction/demo_compare.py` `_run_kgen_refresh` (line 7044): wrap the `extractor.run_pipeline(...)` call (inside `_run_kgenskills`) so that if `kgspin_core.execution.errors.MissingDomainModelError` (or whatever module path core picks — confirmed in core's plan) is raised at orchestration setup, we emit:
    ```
    sse_event("error", {
        "step": "kgenskills", "pipeline": "kgenskills",
        "reason": "missing_domain_model",
        "message": str(exc),
        "error_type": type(exc).__name__,
        "recoverable": False,
    })
    ```
    plus the trailing `done` event the existing flow already emits on errors. Do this by catching the typed exception **explicitly by name** — never via bare `except Exception` (silent-mask risk). Catch site is `_run_kgenskills` in `demos/extraction/extraction/kgen.py` line 39 (the `extractor.run_pipeline(...)` call); we change `_run_kgen_refresh` (the SSE emitter, not the lower-level helper) so the SSE event surface is the only place that needs the keyed copy.
  - `demos/extraction/static/js/graph.js:830` (`LLM_FAILURE_COPY`): add a `'missing_domain_model'` entry. Title: "No trained model registered". Help text: "This bundle has no `entity_recognition_model` field, or the registered model couldn't be resolved by kgspin-admin. The trained pipeline fails fast on purpose — silent fallback to the heuristic path would mask misconfiguration. Pick a different bundle or pipeline, or register the model in admin."
- **Tests:**
  - 1 unit test in `tests/extraction/test_refresh_kgen_errors.py`: monkey-patch the extractor to raise `MissingDomainModelError`; assert the SSE stream includes `event: error\ndata: {... "reason": "missing_domain_model" ...}`.
  - 1 unit test asserting `LLM_FAILURE_COPY['missing_domain_model']` is exported and non-empty (read static JS file as text — we already do this for one other key in `tests/static/test_failure_copy.py` if it exists; otherwise it's a new ~10-line test file).
- **Estimate:** 1.5 h, +2 tests.

### Task 4 — Trained-vs-heuristic diff panel

- **Files:**
  - `demos/extraction/static/compare.html` — add the `#trained-diff-panel` div under `#diagnostic-scores`.
  - `demos/extraction/static/js/diff.js` — **NEW** ~150 LOC. Exports `computeTrainedDiff(slotStateA, slotStateB)` returning `{by_type: {...}, only_in_a: [...], only_in_b: [...], agreed: [...]}` plus a `renderTrainedDiff(slotIdxA, slotIdxB)` that paints into `#trained-diff-panel`. Set diff is per-type, normalized via `s.toLowerCase().replace(/\s+/g, ' ').trim()`.
  - `demos/extraction/static/js/slots.js` — call `maybeRenderTrainedDiff()` at the end of every successful slot render (in the `done` SSE handler and the cached-load path). The hook does the strategy detection (find any slot with `strategy === "fan_out"` and any slot with `strategy === "fan_out_trained"` — show panel only if both exist with completed graphs).
- **Tests:**
  - 3 unit tests in `tests/static/test_diff.js` (or pytest-driven via Playwright if the existing demo test harness uses it — check `tests/` for the convention; if no JS test harness exists, write the diff logic in a way that's easy to unit-test from a Python script that imports the JS via `node -e` — adopt whatever convention demo-app already uses).
  - 1 unit case for the **same surface, different type** rule: `[{surface:"Apple", type:"COMPANY"}]` vs `[{surface:"Apple", type:"PRODUCT"}]` → 1 only-in-A (COMPANY) + 1 only-in-B (PRODUCT), 0 agreed.
- **Estimate:** 3.5 h, +4 tests.

### Task 5 — Demo passage + smoke test

- **File (new fixture):** `tests/fixtures/extraction-passages/AAPL-10K-P1.txt` — the canned passage (see §4.3, §3.4 of recon). ~600 tokens, captured from the AAPL 10K HTML at `~/.kgspin/corpus/financial/sec_edgar/AAPL/...` and committed to the repo so the demo is hermetic and does not depend on a live EDGAR fetch.
- **File (new):** `tests/extraction/test_trained_compare_smoke.py` — pytest, 1 test, marked `@pytest.mark.slow` and `@pytest.mark.requires_local_model` so CI can skip but `make smoke` runs it. Steps:
  1. Build a `bundle` with `entity_recognition_model = {ref: "domain_models/financial/entity-recognition/v0.4.0--<hash>", invoker: "phi_adapter"}` (synthetic — we mock `ResourceRegistryClient.get_adapter` to return a path to a tiny test adapter or, if the v0.4 export isn't yet local, monkey-patch the invoker to return canned spans for the passage).
  2. Run `_run_kgenskills(text=passage, ..., pipeline_config_ref=_pipeline_ref_from_strategy("fan_out"))`; assert `len(kg["entities"]) > 0`.
  3. Run the same with `"fan_out_trained"`; assert `len(kg["entities"]) > 0`.
  4. Compute the diff (`from kgspin_demo_app.compare_diff import compute_trained_diff` — Python mirror of the JS function, tiny ~30 LOC helper module so server-side tests don't need a JS runtime). Assert `agreed` is non-empty AND at least one of `only_in_a` / `only_in_b` is non-empty (i.e. the two pipelines actually produce differentiable output on the passage).
- **Estimate:** 2.5 h, +1 test (+ ~30 LOC helper).

### Task 6 — Help-page update

- **File:** `demos/extraction/static/pipelines-help.html` — add `#fan-out-trained` section + TOC entry per §3.4.
- **Tests:** none.
- **Estimate:** 20 min.

### Task 7 — Bundle-options endpoint surfaces availability (optional, time-permitting)

- **Background:** `slots.js` calls `/api/bundle-options?domain=financial` to populate the bundle dropdown. If a bundle has no `entity_recognition_model`, picking `fan_out_trained` against it will hit `MissingDomainModelError`. **Defensive UX:** flag bundles in the dropdown that lack a registered model (greyed out + tooltip "no trained model registered for this bundle"). This is nice-to-have, not required by the goal.
- **Files:**
  - `demos/extraction/demo_compare.py` `bundle_options` endpoint — surface `has_entity_recognition_model: bool` per domain.
  - `static/js/slots.js` `onSlotPipelineChange` — when pipeline is `fan_out_trained`, dim bundles where the flag is false.
- **Tests:** 1 endpoint test.
- **Estimate:** 1.5 h, +1 test. **Skip if any earlier task overruns.**

### Total

| Task | Hours | Tests |
|------|-------|-------|
| 1. Strategy whitelist | 0.5 | 1 |
| 2. JS dropdown wiring | 0.3 | 0 |
| 3. MissingDomainModelError → slot UI | 1.5 | 2 |
| 4. Diff panel | 3.5 | 4 |
| 5. Demo passage + smoke | 2.5 | 1 |
| 6. Help page | 0.3 | 0 |
| 7. Bundle availability hint (optional) | 1.5 | 1 |
| **Total (excl. T7)** | **8.6** | **8** |
| **Total (incl. T7)** | **10.1** | **9** |

Targeting **6 commits** on `sprint-trained-pipeline-rollout-20260507`: (T1+T2 small-wire), (T3), (T4 logic), (T4 panel + slot hook), (T5 fixture + smoke), (T6 + T7).

---

## 4.3 Demo passage choice

Captured from `~/.kgspin/corpus/financial/sec_edgar/AAPL/10-K/2024-11-01/AAPL_10-K_2024-11-01.html`, **Item 1 "Business" → "Products" subsection**, opening 4 paragraphs (~600 tokens). Selection criteria:

1. **Diverse entities by type** — names a flagship product (PRODUCT: iPhone), the company (COMPANY: Apple Inc.), an executive context (PERSON: usually mentioned), a product family (PRODUCT_FAMILY: Mac, iPad), an OS (PRODUCT: iOS, iPadOS). This gives the diff panel multiple types to differentiate on.
2. **Length matches a single chunk** — fits in one extractor chunk at default `chunk_size_kb`, so latency is bounded and the slot completes in seconds, not minutes. This matters for live demo feel.
3. **Replicable** — checked into the repo (`tests/fixtures/extraction-passages/AAPL-10K-P1.txt`), so the demo runs without a live EDGAR fetch and the smoke test is hermetic.

We will commit the passage **as-is** from the raw 10K HTML — Apple's 10K is public US-government filing material; including a 600-token excerpt is the same usage pattern as the JNJ.html fixture already in `tests/fixtures/corpus/JNJ.html`.

The presenter who wants to A/B against a different passage can still use the live ticker flow (type "AAPL", let the existing chunker run); the canned passage is the **default eye-catching path** for the comparison story.

---

## 5. Test plan

### 5.1 New tests (8 total)

1. `test_pipeline_strategy_whitelist::test_fan_out_trained_canonicalizes` — strategy → ref translation.
2. `test_refresh_kgen_errors::test_missing_domain_model_emits_sse` — SSE error event with `reason="missing_domain_model"`.
3. `test_failure_copy::test_missing_domain_model_copy_present` — JS asset has the failure-reason key + non-empty copy.
4. `test_diff::test_basic_set_diff` — JS unit (or Python-mirrored helper) for happy path.
5. `test_diff::test_per_type_count_delta` — count delta math.
6. `test_diff::test_same_surface_different_type_not_agreed` — collision-avoidance rule.
7. `test_diff::test_normalization_casefold_whitespace` — "Apple Inc." vs "apple   inc." → agreed.
8. `test_trained_compare_smoke` — end-to-end on the canned passage; both pipelines run, diff is computable.

### 5.2 Existing tests that must still pass

- `tests/extraction/*` — current extraction-flow tests; the Task 1 whitelist edit is additive.
- `tests/test_pipeline_common.py` (if exists) — pipeline registry / cache-key tests.
- `tests/routes/test_*.py` — route handler tests for `/api/refresh-discovery` and friends.
- The full `pytest -m "not slow"` suite must still pass; the only new slow test is T5's smoke.

### 5.3 Manual smoke (presenter walkthrough)

After the branch lands and core/admin/blueprint are deployed locally:

1. `./scripts/start-demo.sh`.
2. Open `/compare`, type "AAPL", wait for the source-doc panel.
3. slot-0 → "Signal Fan-out" + bundle `financial-v22d` → Run.
4. slot-1 → "Signal Fan-out (trained)" + bundle `financial-v22d` → Run.
5. Verify both graphs render. Verify the new "Trained vs. Heuristic Diff" panel appears under the slots with non-zero counts in all three buckets (only-in-A, only-in-B, agreed).
6. **Negative path:** swap slot-1's bundle to one with no `entity_recognition_model` registered (e.g. an older `financial-v0` if available). Re-run slot-1. Verify the slot shows the red "No trained model registered" overlay with the failure-copy text.

---

## 6. Acceptance criteria

The sprint is done when, on `sprint-trained-pipeline-rollout-20260507` (assuming Phase 1 + 2 of the cross-repo sprint have landed):

- [ ] `fan_out_trained` is selectable in all three slot dropdowns on /compare and the friendly label reads "Signal Fan-out (trained)".
- [ ] Running `fan_out` and `fan_out_trained` against `financial-v22d` + AAPL produces two completed graphs in their respective slots.
- [ ] The "Trained vs. Heuristic Diff" panel auto-appears under the slots and shows: (a) per-type count deltas for every type that has at least one entity in either slot; (b) only-in-A / only-in-B / agreed lists scoped within type; (c) total entity counts per slot.
- [ ] When `fan_out_trained` is run against a bundle with no `entity_recognition_model`, the slot shows the red "No trained model registered" overlay with the explanatory help text — never silently falls back to heuristic output.
- [ ] The pipelines-help.html page has a new `#fan-out-trained` section reachable from the per-slot `?` button.
- [ ] All 8 new tests pass; full `pytest -m "not slow"` suite stays green.
- [ ] Smoke test (`pytest -m slow tests/extraction/test_trained_compare_smoke.py`) passes locally on Mac MPS.
- [ ] No existing pipeline ( `discovery_rapid` / `discovery_deep` / `fan_out` / `agentic_flash` / `agentic_analyst` ) regresses on the existing demo flow.

---

## 7. Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Core / blueprint / admin sprints land late or with API drift; `MissingDomainModelError` import path changes between core's plan and our integration. | Medium | Medium | We import `MissingDomainModelError` from a single place (`kgspin_core.execution.errors`, per core's plan) and isolate the `except` to one site (`_run_kgen_refresh`). If core picks a different module path, the diff is one import line + one `from` clause. The smoke test is the canary. |
| The diff panel feels visually heavy on the existing /compare page (already dense with 3 slots + `#diagnostic-scores`). | Medium | Low | Keep the panel collapsed-by-default with a "Show diff" affordance if usability feedback during a self-walkthrough says it crowds the page. The styling matches `#diagnostic-scores` (same dark card pattern) so it doesn't introduce a new visual paradigm. |
| AAPL passage has too few PERSON entities to make the diff panel "look alive" on first viewing. | Low | Low | We chose the Item 1 Business / Products excerpt deliberately for entity diversity; if a self-walkthrough shows a flat diff, swap in the Item 10 "Directors & Executive Officers" excerpt (PERSON-rich) before the demo lands. Both are stable, public-domain 10K text. |
| The `_run_kgen_refresh` SSE error path has subtle ordering: the existing flow emits `done` after `error`. We need to preserve that or the slot UI gets stuck on "Re-extracting...". | Medium | High | Mirror the existing pattern at line 7065–7071 *exactly* — `error` event followed by `done` with `total_duration_ms: 0`, then `return`. Add a regression test (T3) that asserts both events appear in order. |
| Bundle-options endpoint (T7) leaks "trained model registered: false" for bundles where it's not relevant (e.g. clinical bundles), confusing the dropdown. | Medium (if we ship T7) | Low | Only surface the flag on `domain=financial` for now; clinical doesn't have a trained model in this sprint. Or skip T7 entirely (it's marked optional). |
| `fan_out` Prophet calls at demo time exceed the cost cap if the presenter aggressively re-runs slots. | Low | Low | Per §9 below the per-run cost is < $0.01; even 100 re-runs in a demo session stays under $1. The KG cache also short-circuits identical re-runs. |
| Static JS file edits (compare.html) trigger merge conflicts with parallel demo-app branches. | Medium | Low | This branch cuts from `main` after current branches merge (per goal-file branch instruction). We rebase early if conflicts surface. |

---

## 8. Dependencies on other repos in this sprint

This is **the last repo in the chain** (per shared-context §38–44):

- **kgspin-domain-morphology** (Repo 1) — exports v0.4 adapter to `data/exports/financial/entity-recognition/v0.4.0--<hash>/`. Required before kgspin-admin can register it. The path also informs the smoke test's adapter-discovery shim.
- **kgspin-admin** (Repo 2) — registers v0.4.0 adapter in the registry. Required before any `fan_out_trained` run can resolve the model.
- **kgspin-blueprint** (Repo 3) — adds the optional `entity_recognition_model` field to the bundle YAML schema and to the `financial-v22d` (or successor) bundle. Required before `bundle.entity_recognition_model` is populated for runtime consumption.
- **kgspin-core** (Repo 4) — adds the `fan_out_trained` named pipeline, defines `MissingDomainModelError`, raises at orchestration setup. **Defines the import path we depend on** — see §7 risk row 1.

We can begin Tasks 1 / 2 / 4 / 6 (all UI / wire-format / static-only) **before** the upstream repos land — they don't exercise the model. Tasks 3 and 5 require core's exception type and the registered model to fully smoke; we stub them locally if upstream lands within the sprint window, or run those tasks last.

We do **not** need anything from kgspin-interface for this sprint (no protocol changes).

---

## 9. Cost estimate

### LLM dollars

`fan_out` runs may dispatch a small number of Prophet classification calls per chunk. The chosen passage is one chunk (§4.3), so per-run we expect:

- Prophet token budget: ≤ 2 calls × ≤ 500 input tokens + ≤ 200 output tokens = **~1.4k tokens / run**.
- At Haiku-class pricing (~$0.25 / M input, $1.25 / M output) → **~$0.0006 / run**.
- Demo-day: presenter runs `fan_out` ≈ 5× across multiple bundles + a re-run. **~$0.005 demo-side**.
- Sprint dev / test loop: ~50 manual test runs across the dev period → **~$0.03**.
- Smoke test: 1 `fan_out` + 1 `fan_out_trained` per CI run; trained doesn't hit Prophet (deterministic adapter forward pass). → **~$0.001 / run**.

**LLM hard cap projected:** **< $0.10** for the entire sprint (development + test + demo). Well under the goal's $1 cap.

### GPU dollars

`fan_out_trained` uses local Mac MPS via `PhiAdapterEntityRecognitionInvoker` — no remote GPU spend. **GPU cost: $0.** (Goal cap met.)

### Wall-clock

8.6 h focused (excluding optional T7), plus ~2 h for self-review + smoke walkthrough + dev-report writing → **~11 h end-to-end**. Above the goal's 30-45 min PLAN target only because that target is for the *plan* phase; the *execute* phase takes the 11 h. The plan itself fits in the 30-45 min bound.

---

## 10. Cross-repo interfaces this repo OWNS / MUST NOT BREAK

### Owns

- The presenter-facing comparison UI at `/compare` (slot dropdowns, slot panels, diff panel).
- The summarization of extraction results across pipelines (`#trained-diff-panel` is the surface for that).
- The wire-format whitelist `CANONICAL_PIPELINE_STRATEGIES` (we add `fan_out_trained` to it).
- The friendly-label mapping (`PIPELINE_META`, `pipelines-help.html`).

### Must not break

- The demo-app's existing extraction flow — the existing five canonical pipelines must all continue to work bit-identically.
- The `kgspin-core` public consumer pattern — we still pass `(bundle, pipeline_config_ref, registry_client)` exactly as today; only the strategy *value* is new.
- The `/api/refresh-discovery/{doc_id}` SSE event shape — we add a new `reason` value (`missing_domain_model`) to an existing event type; we do not introduce a new event type.

---

## 11. Open questions for CTO

1. **No `vp-review.sh` exists in this repo.** Cousin repos (kgspin-core, kgspin-domain-morphology, kgspin-demo) ship `scripts/agentic/vp-review.sh` + a `.review-engine` config; kgspin-demo-app does not, and its CLAUDE.md does not document a Phase-1 procedure. The goal file says "Follow your CLAUDE.md Phase 1 fully: write sprint plan, run vp-review.sh for VP-Eng + VP-Prod." We wrote the plan; we cannot run a script that does not exist. **Options:** (a) skip VP review for this repo's plan (CTO arbitrates directly); (b) port `scripts/agentic/vp-review.sh` + `.review-engine` from kgspin-core as a side-task before the next sprint plan; (c) treat the goal file's instruction as inherited from a template and run VP review manually by assembling persona prompts. We default to (a) for this round and recommend (b) as a tooling cleanup in a follow-up sprint.

2. **Diff panel placement and visual weight.** Section §3.3 puts the panel under `#diagnostic-scores`. The /compare page is already dense. Acceptable, or do you want a dedicated "Comparison Insights" tab next to "Why" / "Plays"?

3. **Per-type folding for the set diff.** §3.3 / Task-4 last test case enforces "same surface, different type → not agreed" (Apple-as-COMPANY ≠ Apple-as-PRODUCT). This is the conservative call. Is that the intended definition for the comparison story, or do you want a "soft agreement" that ignores type? (We strongly prefer the conservative call; flagging because the demo narrative might want a single big "agreed" number.)

4. **Where does the `MissingDomainModelError` import path live?** Per §7 risk row 1 — core's plan (Repo 4) chose `kgspin_core.execution.errors`. We assume that. Confirm before we cut the integration.

5. **AAPL passage license posture.** §4.3 — we plan to commit a 600-token excerpt of AAPL's public 10K to `tests/fixtures/`. JNJ.html is already committed as a similar fixture. Confirm that's still the policy; we don't want a follow-up legal-cleanup ask.

---

## 12. What we DID NOT plan for (out of scope, per goal)

- A full extraction-quality eval (F1 / precision / recall) — that's the morphology dev report's job, not the demo-app's.
- Performance / latency optimization of either pipeline.
- Multi-document batch comparison — single passage at a time.
- New corpus fetchers — we use what's already there.
- Modal-served inference — local Mac MPS is enough for demo scale.

— Dev team (kgspin-demo-app)
