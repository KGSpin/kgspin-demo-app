# LLM Call-Site Inventory — kgspin-demo

**Status:** Audit snapshot (2026-04-20)
**Author:** Dev Team
**Scope:** Current state of every LLM invocation point in this repo, measured against the fleet-wide ADR-002 (Centralized LLM Alias Registry) target.
**Audit commit:** `llm-call-site-audit-20260420` branch.

The ADR-002 target is: every call site accepts `llm_alias` (preferred) or direct `llm_provider` + `llm_model` overrides, with strict precedence (explicit arg → repo config default → startup error on null). No hardcoded provider/model at any call site.

LLM invocations in this repo all go through `kgspin_core.agents.backends` — either `create_backend("<provider>", model=...)` (factory) or direct class instantiation (e.g., `GeminiBackend()`). No direct imports of `openai`, `anthropic`, `google.genai`, `mistralai`, or `ollama` anywhere in source. The Gemini SDK ships via `kgspin-core[gemini]` extra (`pyproject.toml`). `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY` are the only LLM env vars read.

Default Gemini model constant: `DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"` at `demos/extraction/demo_compare.py:406`. Query-param validation whitelist: `VALID_GEMINI_MODELS = {"gemini-2.5-flash-lite", "gemini-2.5-flash"}` (line 405) — only two models are accepted by the running server.

---

## Section 1 — Every call site

"Selection mechanism" reports current behavior: how the provider and model are determined at the moment the call fires. "Override today?" reports whether a caller (HTTP client, CLI user, or internal caller) can change the provider/model without editing source.

| # | Call site | Invocation type | What it does | Current selection mechanism | Accepts override today? | Work to reach ADR-002 target |
|---|---|---|---|---|---|---|
| **Extraction strategy wrappers (delegate to `kgspin_core` `run_pipeline`)** | | | | | | |
| 1 | `demos/extraction/demo_compare.py:7615` — `_run_agentic_flash()` | method (pipeline-stage) | Agentic Flash — full-corpus-in-one-prompt LLM extraction. Delegates to `KnowledgeGraphExtractor.run_pipeline(backend=…)` with `execution_strategy="agentic_flash"`. | Hardcoded provider `"gemini"`; `model` kwarg (default `DEFAULT_GEMINI_MODEL`). `backend = create_backend("gemini", model=model)` at line 7645. | PARTIAL — `model` is overridable; provider is hardcoded to Gemini. | Accept `llm_alias` / `llm_provider` / `llm_model` kwargs; resolve via alias registry; replace hardcoded `"gemini"` with resolved provider. |
| 2 | `demos/extraction/demo_compare.py:7671` — `_run_agentic_analyst()` | method (pipeline-stage) | Agentic Analyst — macro-chunked multi-stage LLM extraction. `execution_strategy="agentic_analyst"`. | Same as #1: `create_backend("gemini", model=model)` at line 7703. | PARTIAL — model overridable; provider hardcoded. | Same as #1. Additionally, ADR-002 §7 mandates per-stage granularity inside multi-stage strategies — today the same `backend` instance is reused for every internal turn of `agentic_analyst`, because core's `run_pipeline` accepts a single `backend` param. Plumbing for per-stage LLM selection is an upstream kgspin-core concern; this demo call site would need to pass a dict/stage-map once core supports it. |
| 3 | `demos/extraction/demo_compare.py:7732` — `_run_clinical_gemini_full_shot()` | method (pipeline-stage) | Clinical domain Agentic Flash. | `create_backend("gemini", model=model)` at line 7757. | PARTIAL — same as #1. | Same as #1. |
| 4 | `demos/extraction/demo_compare.py:7775` — `_run_clinical_modular()` | method (pipeline-stage) | Clinical domain Agentic Analyst. | `create_backend("gemini", model=model)` at line 7799. | PARTIAL — same as #1. | Same as #2. |
| 5 | `demos/extraction/demo_compare.py:7563` — `_run_kgenskills()` | method (pipeline-stage) | KGSpin deterministic extraction. Uses GLiNER (NER model, **not** an LLM) — zero LLM tokens. Included per "bias toward over-inclusion" because it still flows through `run_pipeline(backend=…)`. | `backend = _get_gliner_backend()` (module singleton) at line 7587. | N/A — not an LLM call. | None for ADR-002 scope. Separate concern: GLiNER labels are hardcoded in `_get_gliner_backend` (see #15). |
| **Direct `GeminiBackend()` instantiations (no provider/model arg, no override path)** | | | | | | |
| 6 | `demos/extraction/demo_compare.py:2593` — `_run_compare_qa()` inside `compare_qa()` endpoint (line 2544, POST `/api/compare-qa/{ticker}`) | api → method | Per-pipeline Q&A against each cached KG, plus a cross-pipeline qualitative comparison. Two `.complete()` calls (lines 2609, 2649). | `backend = GeminiBackend()` at line 2595 — no kwargs. Model defaults to whatever `GeminiBackend.__init__` picks (a core-side default). | NO — no `model` param on endpoint, no kwarg on `GeminiBackend()`. | Accept `llm_alias` in request body; pass resolved `(provider, model)` to `create_backend(…)` instead of `GeminiBackend()`. |
| 7 | `demos/extraction/demo_compare.py:2791` — `_ask_wtm()` inside `why_this_matters()` endpoint (line 2719, GET `/api/why-this-matters/{ticker}`) | api → method | "Why This Matters" — runs two LLM answers (with-KG vs without-KG) for a user question. `.complete()` at lines 2804, 2816. | `backend = GeminiBackend()` at line 2793 — no kwargs. | NO — endpoint query params are `domain`, `question`, `pipeline`; no model. | Add `model` / `llm_alias` query param; thread to `_ask_wtm`; pass to `create_backend`. |
| 8 | `demos/extraction/demo_compare.py:3903` — `auto_flag_graph()` endpoint (POST `/api/feedback/auto_flag`) | api | AI auto-detects likely-bad entities/edges in a KG for HITL confirmation. | `backend = GeminiBackend()` at line 3945. | NO — request body carries `nodes`, `edges`, `document_id`, `entity_types`; no model/alias field. | Accept `llm_alias` field in request body; pass to `create_backend`. |
| 9 | `demos/extraction/demo_compare.py:4400` — `auto_discover_tp()` endpoint (POST `/api/feedback/auto_discover_tp`) | api | AI gold-data selector — identifies entities/relationships most likely correct. | `backend = GeminiBackend()` at line 4428. | NO — same pattern as #8. | Same as #8. |
| 10 | `demos/extraction/demo_compare.py:5468` — `run_quality_analysis()` | method | Compares 2-way or 3-way extraction quality (KGS vs agentic-flash vs agentic-analyst). Called from the main `run_comparison()` SSE pipeline (`demo_compare.py:5520`). `.complete()` at line 5494. | `backend = GeminiBackend()` at line 5489 — no kwargs. | NO — function takes KGs and stats, no model param. The surrounding `run_comparison()` *does* accept `model`, but it is not propagated here. | Add `model` / `llm_alias` param to `run_quality_analysis`; thread from `run_comparison`. |
| 11 | `demos/extraction/demo_compare.py:8857` — `run_impact()` → `_ask_both()` inner helper at line 8931 (SSE endpoint `/api/impact/{ticker}` at line 2706) | api → method | Impact analysis — for each of N questions, issues with-KG and without-KG answers. Two `.complete()` calls per question (lines 8943, 8957). | `backend = GeminiBackend()` at line 8933. | NO — endpoint takes `{ticker}` only; no model param threaded through. | Add `model` / `llm_alias` query param; thread to `_ask_both`. |
| 12 | `demos/extraction/demo_compare.py:9080` — `_run_impact_quality_analysis()` | method | Post-impact summary: LLM judges with-KG vs without-KG answer quality. `.complete()` at line 9130. | `backend = GeminiBackend()` at line 9084 — no kwargs. | NO — function takes a results list only. | Add `model` / `llm_alias` param; thread from `run_impact`. |
| **FastAPI endpoint surface (provider/model selection layer — call the extraction wrappers above)** | | | | | | |
| 13 | `demos/extraction/demo_compare.py:2199` — `compare_clinical()` (GET `/api/compare-clinical/{nct_id}`) | api | Clinical-domain compare route; SSE streams KGSpin + Full Shot + Multi-Stage extraction. | `model` query param (default `DEFAULT_GEMINI_MODEL`); validated against `VALID_GEMINI_MODELS` at line 2223. Threads to `_run_clinical_comparison` → #3, #4. | PARTIAL — `model` overridable; provider implicitly Gemini. | Accept `llm_alias` query param; resolve and pass provider+model down. |
| 14 | `demos/extraction/demo_compare.py:2356` — `compare()` (GET `/api/compare/{ticker}`) | api | Financial-domain main compare route; SSE streams KGSpin + Agentic Flash + Agentic Analyst. | `model` query param validated at line 2361 → passed to `run_comparison()` → #1, #2. | PARTIAL — same as #13. | Same as #13. |
| 15 | `demos/extraction/demo_compare.py:2388` — `refresh_agentic_flash()` (GET `/api/refresh/agentic-flash/{ticker}`) | api | Re-runs Agentic Flash for a ticker. | `model` query param validated at line 2392 → #1. | PARTIAL. | Same as #13. |
| 16 | `demos/extraction/demo_compare.py:2407` — `refresh_agentic_analyst()` (GET `/api/refresh/agentic-analyst/{ticker}`) | api | Re-runs Agentic Analyst for a ticker. | `model` query param validated at line 2411 → #2. | PARTIAL. | Same as #13. |
| 17 | `demos/extraction/demo_compare.py:2428` — `refresh_discovery()` (GET `/api/refresh/discovery/{ticker}`) | api | Re-runs deterministic KGSpin. | No `model` param (deterministic pipeline, non-LLM). | N/A — not an LLM call. | None. |
| 18 | `demos/extraction/demo_compare.py:2665` — `intelligence()` (GET `/api/intelligence/{ticker}`) | api | Intelligence tab — news + KG extraction. | `model` query param validated at line 2668; passed to `run_intelligence()` (which calls into #3/#4 for clinical track). | PARTIAL. | Same as #13. |
| 19 | `demos/extraction/demo_compare.py:2682` — `refresh_intel()` (GET `/api/refresh/intel/{ticker}`) | api | Re-runs Intelligence pipeline. | `model` query param. | PARTIAL. | Same as #13. |
| 20 | `demos/extraction/demo_compare.py:2706` — `impact()` (GET `/api/impact/{ticker}`) | api | Impact analysis streaming endpoint — wraps `run_impact()` (#11). | No `model` param on endpoint; `GeminiBackend()` hardcoded inside. | NO — same as #11. | Same as #11. |
| 21 | `demos/extraction/demo_compare.py:2718` — `why_this_matters()` (GET `/api/why-this-matters/{ticker}`) | api | WTM endpoint wrapper — calls #7. | No `model` param. | NO — same as #7. | Same as #7. |
| 22 | `demos/extraction/demo_compare.py:2544` — `compare_qa()` (POST `/api/compare-qa/{ticker}`) | api | Cross-pipeline Q&A endpoint — calls #6. | No `model` param. | NO — same as #6. | Same as #6. |
| **CLI / script entry points** | | | | | | |
| 23 | `demos/extraction/run_overnight_batch.py:329` — `main()` `--model` CLI arg | cli | Batch benchmark: iterate tickers, run all five pipelines per ticker, write run-log artifacts. | `--model` flag (default `gemini-2.5-flash`). **NOTE: script is currently broken** — line 152 imports `_run_gemini_full_shot` from `demo_compare`, but that symbol was renamed to `_run_agentic_flash` in INIT-001 Sprint 03 and no longer exists. The `--model` arg is still wired, but the script cannot run end-to-end today. | PARTIAL (intent) / broken (reality) — `--model` overridable in principle; provider hardcoded. | Fix stale import before ADR-002 work. Then: accept `--llm-alias` flag; pass alias id through to the (repaired) extraction entrypoints. |
| 24 | `demos/extraction/run_overnight_experiment.py:788` — `main()` `--model` CLI arg | cli | 5-phase KG quality experiment runner. | `--model` flag (default `gemini-2.5-flash`). Same stale-import concern as #23 — needs verification before running; flagged as suspect. (?) | PARTIAL / suspect. | Same as #23. |
| 25 | `demos/extraction/run_abc_comparison.py:94` — `main()` runs A=KGSpin, B=LLM-Modular, C=Gemini | cli | Three-way diagnostic comparison. **Broken** — imports `_run_modular` from `demo_compare` (removed) and `GeminiModularExtractor` from a module `gemini_modular_extractor` that does not exist in this tree. | No `--model` CLI flag at all. Line 128 reads `mod_kg.get("provenance", {}).get("model", "gemini-2.5-flash-lite")` — hardcoded fallback. | NO. | Repair or delete before ADR-002 work. If repaired: add `--llm-alias` flag; thread through to extraction entrypoints. |
| 26 | `demos/extraction/run_kgen_smell_test.py:75` — `main()` | cli | KGSpin-only deterministic extraction smoke test. | Calls `_run_kgenskills` only (#5). | N/A — no LLM invocation. | None. |
| 27 | `demos/extraction/demo_ticker.py:744` — `generate_resolved_h_modules()` | method (invoked from CLI) | Entity-discovery H-Module generator. Calls `kgenskills.agents.h_module_agent.create_h_module_agent(domain=…, backend_type=…)`. | `backend_type` kwarg, default `"gliner"`. Exposed as CLI `--backend` (line 967) with choices `["gliner", "anthropic", "gemini", "ollama"]`. (?) — whether the non-gliner backends actually invoke LLMs depends on kgspin-core's `h_module_agent` implementation; including here for over-coverage. | PARTIAL — provider overridable via CLI; model is not exposed, presumably taken from a kgspin-core default. | Accept `--llm-alias` flag in addition to `--backend`; thread both provider and model through to `create_h_module_agent` (requires upstream signature change in kgspin-core). |
| **Singleton initializer (non-LLM, included for completeness)** | | | | | | |
| 28 | `demos/extraction/demo_compare.py:535` — `_get_gliner_backend()` | method (singleton) | Module-level cached GLiNER NER backend (deterministic, not an LLM). Used by #5 and indirectly by every KGSpin slot. | `create_backend(backend_type="gliner", labels=[…], negative_labels={…})` at line 542. Labels hardcoded. | NO — no override path. | Out of ADR-002 scope (GLiNER is NER, not an LLM). Noted only because the ADR-002 fleet invariant could be read as "every `create_backend(…)` call site" — GLiNER is a non-LLM backend and the registry is for LLM aliases. |

---

## Section 2 — Multi-stage strategies

The kgspin-demo repo is a **consumer** of kgspin-core's extraction strategies, not their definition. It does not directly orchestrate H-module / L-module / pairwise-rescan stages — those live inside kgspin-core's `KnowledgeGraphExtractor.run_pipeline()`. From this repo's perspective, each strategy is a single `run_pipeline(backend=…)` call with `bundle.execution_strategy` set to one of the core-side dispatch keys.

There are, however, multiple independent **LLM-invoking code paths** exposed by the demo UI that act like parallel "stages" from an operator's perspective. ADR-002 §7 requires each to be independently parameterizable:

| Pipeline / surface | Call sites above | Today: shared backend? | ADR-002 target |
|---|---|---|---|
| **Agentic Flash** (financial + clinical) | #1, #3 | New `create_backend("gemini", model=model)` per call; `model` propagates from the FastAPI endpoint. Provider hardcoded. | Each call resolves an alias independently; consumer config names the alias (e.g., `strategies.agentic_flash.llm: gemini_flash`). |
| **Agentic Analyst** (financial + clinical) | #2, #4 | New `create_backend("gemini", model=model)` per call. Internally, kgspin-core's `run_pipeline` uses one backend instance across every turn of the multi-turn strategy — no per-turn LLM selection is available today at the demo boundary. | Per-stage / per-turn LLM selection is an upstream kgspin-core change (ADR-002 §7 example `h_module_llm`, `l_module_llm`, `pairwise_rescan_llm`). Demo would pass a stage-map once core supports it. |
| **KGSpin deterministic (KGenSkills)** | #5 | Uses GLiNER — not an LLM. | Out of scope. |
| **Cross-pipeline Q&A (`compare_qa`)** | #6 | Single `GeminiBackend()` instance reused across every question × every graph in the request. | Accept `llm_alias` per request; optionally per-question alias. |
| **"Why This Matters"** | #7 | Single `GeminiBackend()` reused for with-KG and without-KG answers. | Alias-per-endpoint. If with/without divergence is ever desired, a pair of aliases. |
| **Auto-flag / Auto-discover-TP** | #8, #9 | Single `GeminiBackend()` per request. | Alias-per-endpoint. |
| **Quality analysis (post-extraction)** | #10 | Single `GeminiBackend()`; not threaded from the ambient `run_comparison(model=…)`. | Thread the ambient alias; optionally separate `quality_analysis_llm` alias. |
| **Impact Q&A** | #11 | Single `GeminiBackend()` reused across all questions × two KG conditions. | Accept `llm_alias` on `/api/impact` endpoint. |
| **Impact quality summary** | #12 | Single `GeminiBackend()`. | Same as #10 — thread ambient alias or dedicate a `quality_analysis_llm`. |
| **H-Module entity-discovery backend (demo_ticker CLI)** | #27 | `backend_type` picks the vendor; model implicit. One backend per run. | Accept `llm_alias`; per-domain override is already present via `domain=`, but model is unexposed. |

---

## Section 3 — Target state gaps

### Fully satisfy ADR-002 today

None. No call site in this repo accepts `llm_alias`, and no call site accepts a direct `(llm_provider, llm_model)` pair. Every call site either hardcodes the provider (`"gemini"`) or instantiates `GeminiBackend()` directly.

### Partially satisfy

The five extraction-strategy wrappers (#1–#4) and the FastAPI endpoints that feed them (#13–#16, #18–#19) form a chain where **model is overridable per-request** via the `?model=…` query parameter but **provider is hardcoded to Gemini**. Model override is constrained to `VALID_GEMINI_MODELS = {"gemini-2.5-flash-lite", "gemini-2.5-flash"}` — a two-element whitelist.

- **One-line fix:** replace `create_backend("gemini", model=model)` with an alias-resolving call; accept `llm_alias` (preferred) / `(llm_provider, llm_model)` on endpoints and thread through.

CLI #23 (`run_overnight_batch.py`) and CLI #24 (`run_overnight_experiment.py`) follow the same intent but are **currently broken** due to stale imports after INIT-001 Sprint 03's rename of `_run_gemini_full_shot` → `_run_agentic_flash`. The `--model` arg remains; repair before ADR-002 work.

CLI #27 (`demo_ticker.py --backend`) accepts a provider override (`gliner|anthropic|gemini|ollama`) but not a model override — model is taken from a kgspin-core default. Provider-overridable, model-hardcoded.

### Violate ADR-002 today

All seven direct `GeminiBackend()` instantiations are hardcoded at both provider and model:

- **#6** `compare_qa` — `GeminiBackend()` at line 2595. **Fix:** accept `llm_alias` in request body.
- **#7** `why_this_matters` → `_ask_wtm` — `GeminiBackend()` at line 2793. **Fix:** add `model` / `llm_alias` query param.
- **#8** `auto_flag_graph` — `GeminiBackend()` at line 3945. **Fix:** accept `llm_alias` in request body.
- **#9** `auto_discover_tp` — `GeminiBackend()` at line 4428. **Fix:** accept `llm_alias` in request body.
- **#10** `run_quality_analysis` — `GeminiBackend()` at line 5489. **Fix:** add `llm_alias` param and thread from `run_comparison`.
- **#11** `run_impact` → `_ask_both` — `GeminiBackend()` at line 8933. **Fix:** add `llm_alias` query param on `/api/impact`; thread through.
- **#12** `_run_impact_quality_analysis` — `GeminiBackend()` at line 9084. **Fix:** add `llm_alias` param; thread from `run_impact`.

CLI #25 (`run_abc_comparison.py`) is broken and also violates (no `--model` flag; hardcoded `"gemini-2.5-flash-lite"` fallback on line 128). Repair-or-delete, then add alias flag.

### Out-of-scope but noted

- **#5** `_run_kgenskills` uses GLiNER (non-LLM NER). Not a violation of ADR-002.
- **#28** `_get_gliner_backend` singleton also GLiNER; out of scope.
- kgspin-core's internal per-stage LLM selection inside `agentic_analyst` / `run_pipeline` is an upstream concern — this repo's call sites only expose whatever granularity core offers at the `run_pipeline(backend=…)` boundary.

---

## Section 4 — Estimated rework

**Overall size: S (small).**

This repo has 12 distinct LLM-invoking call sites across one file (`demos/extraction/demo_compare.py` holds 11 of them; `demos/extraction/demo_ticker.py` holds the 12th). The partially-compliant chain (#1–#4 + #13–#19) already threads a `model` string end-to-end — the refactor there is substitution: take `model` → take `llm_alias` / `llm_provider` + `llm_model`; replace `create_backend("gemini", model=model)` with a resolver call. The seven `GeminiBackend()` hardcodes (#6–#12) are the main surface of change; each is localized to a single function, and the pattern is identical across all seven: add an endpoint/param accept → thread → resolve → `create_backend`.

Upstream dependencies: this work cannot land until (a) kgspin-core provides an alias-aware `BackendFactory.get(alias=..., provider=..., model=...)` (Phase 2 of the ADR's rollout plan) and (b) either kgspin-admin-client or kgspin-interface ships `LLMAliasResolver`. Once both land, the demo's refactor is ~1 sprint of mechanical plumbing plus repairs to the three stale CLI scripts (#23–#25).

Per-stage granularity inside `agentic_analyst` (ADR-002 §7 `h_module_llm` / `l_module_llm` shape) is **not** available at this repo's boundary today and is not an in-repo fix — it depends on kgspin-core exposing a stage-map through `run_pipeline`. That piece is M-sized and owned by kgspin-core.
