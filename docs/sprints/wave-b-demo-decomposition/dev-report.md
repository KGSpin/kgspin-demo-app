# Wave B — Demo Decomposition — Dev Report

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-22
**Branch:** `wave-b-demo-decomposition`
**Baseline:** `main` @ `19682ef` (post-Wave-A merge)
**Interface contract:** `kgspin-interface 0.8.1` (unchanged by this sprint)

---

## TL;DR

This is the **partial carve** the preamble called out as the acceptable
outcome. The Python side of `demo_compare.py` (10,017 LOC at start) is
down to **8,198 LOC** (-1,819 LOC, -18 %); the dual-flow Liskov-arity
violation the audit flagged is fixed; 14 new modules landed; **all 204
tests pass** on every commit. The JS/HTML carve of `compare.html` is
**deferred** — the sprint budget prioritised Python per the CTO's
fallback directive.

5 commits on `wave-b-demo-decomposition`:

```
5c30a4a refactor(demo): carve routes/feedback.py — 6 feedback CRUD endpoints
eaa8c33 refactor(demo): carve routes/corpus.py — 6 refresh-corpus endpoints
d79cb72 refactor(demo): carve routes/runs.py — 10 run-history endpoints
a4aac16 refactor(demo): carve extraction/{kgen,agentic,clinical}.py + unify 5-tuple returns
0c14b39 refactor(demo): carve sse/cache/bundle_resolve/prompts/analysis out of demo_compare.py
```

Each commit is one logical area, tests green, clean history.

---

## What moved where — precise map (for Wave C tests migration)

### New modules + line counts

| Module | LOC | What |
|---|---:|---|
| `demos/extraction/sse/events.py` | 15 | `sse_event()` SSE frame builder |
| `demos/extraction/cache/run_log.py` | 322 | `GeminiRunLog` + 4 subclasses (`Modular`, `KGen`, `Intel`, `ImpactQA`), + singleton instances |
| `demos/extraction/bundle_resolve.py` | 115 | `_get_bundle`, `_bundle_id`, `_split_bundle_id`, `_get_gliner_backend`, `purge_caches()` |
| `demos/extraction/prompts.py` | 270 | `build_quality_analysis_prompt` |
| `demos/extraction/analysis.py` | 79 | `run_quality_analysis` (LLM runner for the prompt above) |
| `demos/extraction/extraction/kgen.py` | 60 | `_run_kgenskills` (zero-LLM KGSpin dispatch) |
| `demos/extraction/extraction/agentic.py` | 157 | `_run_agentic_flash`, `_run_agentic_analyst` |
| `demos/extraction/extraction/clinical.py` | 133 | `_run_clinical_gemini_full_shot`, `_run_clinical_modular` |
| `demos/extraction/routes/runs.py` | 410 | `/api/gemini-runs`, `/api/modular-runs`, `/api/kgen-runs`, `/api/intel-runs`, `/api/impact-qa-runs` (list + detail), plus `_sort_run_files_by_timestamp` / `_latest_config_key` |
| `demos/extraction/routes/corpus.py` | 281 | `/api/refresh-corpus/sec`, `/clinical`, `/yahoo-rss`, `/marketaux`, `/newsapi`, `/news/{domain}`, plus `_derive_clinical_query_from_nct` |
| `demos/extraction/routes/feedback.py` | 215 | `/api/feedback/false_positive`, `/false_negative`, `/true_positive`, `/retract`, `/bulk_retract`, `/list` |

Total new code: **2,057 LOC** across 11 modules + 3 `__init__.py` files.

### Function-level "what moved where" table

Every callable the Wave C tests team might need to re-anchor:

| Symbol | Was in | Now in | Notes |
|---|---|---|---|
| `sse_event` | `demo_compare` | `sse.events` | re-exported from `demo_compare` via `from sse.events import sse_event` |
| `GeminiRunLog`, `ModularRunLog`, `KGenRunLog`, `IntelRunLog`, `ImpactQARunLog` | `demo_compare` | `cache.run_log` | re-exported |
| `_run_log`, `_modular_run_log`, `_kgen_run_log`, `_intel_run_log`, `_impact_qa_run_log` | `demo_compare` | `cache.run_log` | singletons, re-exported |
| `_get_bundle`, `_bundle_id`, `_split_bundle_id`, `_get_gliner_backend` | `demo_compare` | `bundle_resolve` | re-exported |
| `_bundle_cache`, `_init_lock`, `_CACHED_BUNDLE`, `_CACHED_GLINER_BACKEND` | `demo_compare` | `bundle_resolve` | module state; `purge_cache` endpoint now calls `bundle_resolve.purge_caches()` |
| `build_quality_analysis_prompt` | `demo_compare` | `prompts` | lazy-imports `compute_diagnostic_scores` from `demo_compare` to break cycle |
| `run_quality_analysis` | `demo_compare` | `analysis` | lazy-imports `_load_valid_entity_types` from `demo_compare` |
| `_run_kgenskills` | `demo_compare` | `extraction.kgen` | re-exported |
| `_run_agentic_flash`, `_run_agentic_analyst` | `demo_compare` | `extraction.agentic` | re-exported |
| `_run_clinical_gemini_full_shot`, `_run_clinical_modular` | `demo_compare` | `extraction.clinical` | re-exported; **5-tuple return unified** — see §2 below |
| `gemini_runs`, `gemini_run_detail`, `modular_runs`, `modular_run_detail`, `kgen_runs`, `kgen_run_detail`, `intel_runs`, `intel_run_detail`, `impact_qa_runs`, `impact_qa_run_detail` | `demo_compare` | `routes.runs` | registered via `app.include_router(runs.router)` |
| `_sort_run_files_by_timestamp`, `_latest_config_key` | `demo_compare` | `routes.runs` | only call sites moved too |
| `refresh_corpus_sec`, `_clinical`, `_yahoo_rss`, `_marketaux`, `_newsapi`, `refresh_all_domain_news` | `demo_compare` | `routes.corpus` | registered via `app.include_router(corpus.router)` |
| `_derive_clinical_query_from_nct` | `demo_compare` | `routes.corpus` | only call site moved too |
| `submit_false_positive`, `submit_false_negative`, `submit_true_positive`, `retract_feedback`, `bulk_retract_feedback`, `list_feedback` | `demo_compare` | `routes.feedback` | registered via `app.include_router(feedback.router)` |

### Test-facing facade preserved

All five tests under `tests/unit/test_demo_compare_llm_endpoints.py` use
`demo_compare.app`, `demo_compare._get_bundle`, `demo_compare._get_bundle_predicates`,
`demo_compare._kg_cache` — those attributes are all still reachable on
`demo_compare` (the module re-imports them from the new locations).
Wave C migration can either keep the `demo_compare.X` paths or
cut over to the new module paths; both work.

---

## 1. Facade-import pattern

Every extracted module is accompanied by a `from <new module> import ...`
line in `demo_compare.py` so the public surface stays intact. This was
chosen deliberately for the partial carve — it lets us move 1,800 LOC
without touching a single call site inside `demo_compare`. The trade-off
is that `demo_compare` still holds ~8k LOC of pipeline orchestrators
that reference those imports; Wave C's full package layout (with a
proper `app.py`) can retire the facade in one pass.

Lazy (function-local) imports are used for two specific cases where a
module-load cycle would form:

- `prompts.build_quality_analysis_prompt` → lazy `compute_diagnostic_scores`
  from `demo_compare`.
- `analysis.run_quality_analysis` → lazy `_load_valid_entity_types`
  from `demo_compare`.
- `extraction/agentic`, `extraction/clinical`, `routes/*` → lazy
  `_pipeline_ref_from_strategy`, `_get_registry_client`,
  `build_vis_data`, `_cache_lock`, `_kg_cache`, `_run_lander_subprocess`,
  `_get_feedback_store`, `_get_bundle*`, `_NEWS_SOURCES_BY_DOMAIN` from
  `demo_compare`.

Each lazy import is annotated with a one-line comment explaining why.
Wave C should either hoist these helpers into a shared module or keep
them in `demo_compare` (now slim enough to be the core app module).

---

## 2. Dual-flow unification — 5-tuple contract

**The audit's Liskov violation is fixed.**

- `_run_clinical_modular` previously returned a **4-tuple**
  `(kg, 0, elapsed, 0)`.
- `_run_agentic_analyst` returned a **5-tuple**
  `(kg, h_tokens, l_tokens, elapsed, errors)`.

Clinical modular now returns a 5-tuple
`(kg, 0, 0, elapsed, 0)` — h_tokens and l_tokens are both 0 until the
clinical ExtractionResult surfaces per-stage LLM token counts. The
single call site in `_run_clinical_comparison` was updated to unpack
the new shape (`mod_tokens = mod_h_tokens + mod_l_tokens`).

`_run_agentic_flash` and `_run_clinical_gemini_full_shot` already agreed
on a 5-tuple `(kg, tokens, elapsed, errors, truncated)` shape — no
change needed.

---

## 3. Partial-progress fallback — what's NOT in this sprint

Per the CTO's preamble rule 9 (Wave B's highest-risk work, land partial
before broken), the following are **deferred to Wave C**:

### 3a. JS carve of `compare.html` (9,843 LOC)

Not started. Prioritising Python (higher architectural value per the
CTO's scope section 1) consumed the session budget. The file is
structurally unchanged from Wave A — the 9 modular component files
the CTO scoped (`static/js/state.js`, `slots.js`, `sse.js`, `graph.js`,
`compare-runner.js`, `intelligence.js`, `impact.js`, `settings.js`,
`domain-switch.js`) can be carved in a follow-up sprint without
touching Python.

Risk of leaving it: compare.html keeps its 192 inline `on*=` handlers
and 286 unscoped JS globals. Not a **regression** — just an unfinished
cleanup.

### 3b. Orchestrator-generator SSE functions

These seven async-generator orchestrators stay in `demo_compare.py`:

| Symbol | LOC | Why not moved |
|---|---:|---|
| `run_comparison` | ~1040 | touches ~40 helpers inside `demo_compare`; moving requires hoisting ~15 more helpers (vis, cache keys, pipeline config, feedback prompts) |
| `_run_kgen_refresh` | ~260 | same |
| `run_single_refresh` | ~470 | same |
| `_run_clinical_comparison` | ~440 | same |
| `run_intelligence` | ~800 | same + has its own cluster of intel-specific helpers |
| `run_impact` | ~230 | same |
| `_run_lander_subprocess` | ~140 | called by `routes.corpus`; moving it requires the registry-client + lander-subprocess contract to move too |

Wave C candidate: create `demos/extraction/pipelines/{compare,refresh,clinical,intelligence,impact}.py` and cut this block out with its own helper module. Each file becomes the obvious home for the matching @app.get thin-route in `demo_compare`. Expected reduction: ~3,400 LOC.

### 3c. Feedback orchestrators

`auto_flag_graph` (~500 LOC) and `auto_discover_tp` (~340 LOC) remain in
`demo_compare`. Same reason as the pipeline generators — they reach into
`_get_bundle`, `_get_bundle_predicates`, `_extract_resolve_target`, and
several prompt builders that aren't worth hoisting piecemeal. These
belong in Wave C's `routes/feedback_auto.py` or `feedback/auto.py`.

### 3d. Remaining routes still in `demo_compare`

| Route | Why stayed |
|---|---|
| `/api/compare/{doc_id}` + `/api/compare-clinical/{doc_id}` | thin wrappers but call `run_comparison` / `_run_clinical_comparison` directly |
| `/api/refresh-agentic-flash`, `/api/refresh-agentic-analyst`, `/api/refresh-discovery` | call `run_single_refresh` / `_run_kgen_refresh` |
| `/api/cancel-multistage`, `/api/scores`, `/api/refresh-analysis`, `/api/compare-qa` | use `_kg_cache`, `_cache_lock`, `_modular_cancel_events` — Wave C's pipelines module will bring these with it |
| `/api/intelligence`, `/api/refresh-intel`, `/api/impact`, `/api/why-this-matters`, `/api/impact/lineage`, `/api/impact/reproducibility` | call `run_intelligence` / `run_impact` |
| `/api/slot-cache-check`, `/api/purge-cache`, `/api/bundle-options`, `/api/extraction-schema`, `/api/prompt-template`, `/api/domains`, `/api/clinical-trials`, `/api/test-sse`, `/api/model-pricing`, `/api/tickers`, `/api/slots`, `/api/bundle/predicates`, `/api/document/text` | misc queries — Wave C can bundle into `routes/misc.py` |
| `/api/feedback/auto_flag`, `/api/feedback/auto_discover_tp` | the two big LLM orchestrators — §3c |

### 3e. Wave-A shim removals

Wave A left `ticker = doc_id` one-liners in 27 handlers. Wave B removed
those in the route handlers it moved (runs, corpus, feedback); the
shims remaining in `demo_compare.py` all belong to routes that are
still there (§3d). Wave C should remove them alongside the route
migration.

---

## 4. What's 'baked in' for Wave C to assume

- `sse.events.sse_event` is the **only** SSE frame builder. Any new
  code that emits SSE should import from here. The facade-re-export in
  `demo_compare` is for legacy compatibility; new callers should use
  `from sse.events import sse_event` directly.
- `cache.run_log.*RunLog` are the only cache classes. The
  `_PIPELINE_LOGS` registry in `demo_compare` still references them
  (via the facade); Wave C can move that dict into `cache/__init__.py`
  as a public export.
- `bundle_resolve.purge_caches()` is the **only** sanctioned way to
  clear the in-memory bundle cache. The `/api/purge-cache` handler
  already calls it; any future purge path should too.
- `extraction/*` returns the 5-tuple shape documented in
  `extraction/__init__.py`'s docstring. Any new extractor should match
  that contract.
- `routes/*` each export a `router = APIRouter()`; `demo_compare`
  mounts them after `app = FastAPI(...)`. Wave C can fold this into an
  `app.py` that includes every router.

---

## 5. Tests

### Pass

**All 204 collectable tests pass** on the final commit (`5c30a4a`),
and passed on every intermediate commit in the branch.

Tested after each individual extraction:

- Commit 1 (tier-1 extractions — sse/cache/bundle_resolve/prompts/analysis): 204 ✓
- Commit 2 (extraction/*): 204 ✓
- Commit 3 (routes/runs): 204 ✓
- Commit 4 (routes/corpus): 204 ✓
- Commit 5 (routes/feedback): 204 ✓

### Pre-existing fail-to-collect (unchanged from Wave A)

Same three files as Wave A — `kgspin_core` circular import, not a
Wave B regression:

- `tests/unit/services/test_pipeline_config_ref_dispatch.py`
- `tests/unit/test_demo_compare_registry_reads.py`
- `tests/unit/test_pipeline_config_ref.py`

---

## 6. File-size trajectory

```
baseline (Wave A HEAD):  demo_compare.py  10,017 LOC
after tier-1 (sse/cache/bundle/prompts/analysis):  9,348 LOC  (-669)
after extraction/*:                                9,066 LOC  (-282)  +unify 5-tuple
after routes/runs:                                 8,671 LOC  (-395)
after routes/corpus:                               8,380 LOC  (-291)
after routes/feedback:                             8,198 LOC  (-182)

Total reduction:                                   1,819 LOC  (-18.2%)
compare.html                                       9,843 LOC  (unchanged — deferred)
```

---

## 7. Commits (detail)

### 0c14b39 — tier-1 extractions

Pure-logic modules: `sse/events.py`, `cache/run_log.py`,
`bundle_resolve.py`, `prompts.py`, `analysis.py`. Every mutable module
state (`_bundle_cache`, `_init_lock`, `_CACHED_BUNDLE`,
`_CACHED_GLINER_BACKEND`) moved with `_get_bundle`; a new
`bundle_resolve.purge_caches()` function replaces the `global` statement
in the `/api/purge-cache` handler.

### a4aac16 — extraction/{kgen,agentic,clinical} + 5-tuple unify

Dispatchers moved; Liskov-arity drift fixed. Clinical modular:
`(kg, 0, elapsed, 0)` → `(kg, 0, 0, elapsed, 0)`. Caller updated in
`_run_clinical_comparison` at the single unpack site.

### d79cb72 — routes/runs (10 endpoints)

APIRouter pattern established. `demo_compare.app.include_router(runs.router)`
mounted immediately after the routes-import block. Handlers reach back
into `demo_compare` for `build_vis_data`, `_cache_lock`, `_kg_cache`,
`_CPU_COST_PER_HOUR`, `DEFAULT_CHUNK_SIZE` via function-local imports.

### eaa8c33 — routes/corpus (6 endpoints)

Same APIRouter pattern. `_TICKER_RE`, `_NCT_RE`, `_NEWS_QUERY_RE` are
defined locally in `routes/corpus.py` (they're still needed by other
routes in `demo_compare`, so the originals stay too — one of the few
duplications, worth it for module independence).

### 5c30a4a — routes/feedback (6 endpoints)

Simple CRUD endpoints moved. Big auto_flag / auto_discover_tp
orchestrators left for Wave C per partial-fallback guidance.

---

## 8. Ready for Wave C? Yes.

The architecture is in a stable intermediate state:

- `demo_compare.py` is still a valid FastAPI app — every route still
  registers, every SSE generator still works, every cache class still
  resolves.
- The new modules have clean public surfaces; Wave C's test-migration
  can re-anchor imports onto them without touching runtime behavior.
- The big SSE orchestrators (`run_comparison`, `run_intelligence`, …)
  are the obvious next-carve target; the helpers they reach into
  (`build_vis_data`, `_cache_save`, `_cache_lookup`, prompt builders,
  vis-rendering) are clustered enough that a `pipelines/` package
  extract is tractable — estimate 2-3k LOC movement per pass.
- Dual-flow unification means the `PipelineCoordinator` abstraction
  the audit §8a recommended now has a stable return-shape contract to
  build against.

— Dev team
