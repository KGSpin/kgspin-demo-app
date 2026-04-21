# Sprint W3-D — dev-report

**Branch:** `w3d-demo-unified-dispatch` (off `main` @ `8855334`)
**Scope:** every demo bundle-extraction call site routes through
`run_pipeline(pipeline_config_ref=PipelineConfigRef(name=…, version="v1"),
registry_client=…)`. One dispatch path. YAML is authoritative.

## Refactor summary

### `_get_bundle(bundle_name)` — `demos/extraction/demo_compare.py:462`

Dropped the `pipeline_id=` parameter and the split-bundle overlay
branch. Returns `ExtractionBundle.load(domain_path)` — the domain
bundle unchanged. Pipeline configs now travel separately via
`pipeline_config_ref` on `run_pipeline`, and core resolves them against
admin at dispatch. Deleted `_get_split_bundle` outright (no
`load_split` in W3-A core).

### Each `_run_*` wrapper

| Wrapper | Change |
|---|---|
| `_run_kgenskills(text, …, pipeline_config_ref, registry_client, …)` | New required positional `pipeline_config_ref` + `registry_client`; passes both through to `extractor.run_pipeline`. |
| `_run_agentic_flash` | Builds `PipelineConfigRef(name="agentic-flash", version="v1")` via `_pipeline_ref_from_strategy("agentic_flash")`; uses the shared `_get_registry_client()` singleton. Dropped the `pipeline_id="discovery-deep"` bundle-override hack. |
| `_run_agentic_analyst` | Same pattern, `agentic-analyst` ref. |
| `_run_clinical_gemini_full_shot` | Dispatches via `agentic-flash` ref; deleted `dataclasses.replace(bundle, execution_strategy="agentic_flash")`. |
| `_run_clinical_modular` | Dispatches via `agentic-analyst` ref; deleted `dataclasses.replace(bundle, execution_strategy="agentic_analyst")`. |

### Endpoint whitelist

`compare`, `refresh_discovery`, `slot_cache_check` now share a single
strict validator. The canonical 5 are declared as
`CANONICAL_PIPELINE_STRATEGIES`; `_pipeline_id_from_compare_args` raises
`InvalidPipelineStrategyError` on anything else, which each endpoint
renders as `JSONResponse({"error": …}, status_code=400)`.

### Helpers added

- `_canonical_pipeline_name(strategy)` — maps `fan_out` → `fan-out` etc.,
  raising for non-canonical inputs.
- `_pipeline_ref_from_strategy(strategy)` — returns
  `PipelineConfigRef(name=<hyphen>, version="v1")`.
- `_pipeline_ref_from_pipeline_id(pipeline_id)` — wraps an
  already-hyphenated name; `None` defaults to `fan-out` (baseline
  zero-LLM on Compare tab).

### `pipeline_common.py`

Deleted `_pipeline_resolver` singleton, `_get_pipeline_resolver`, and
`resolve_pipeline_config`. `list_available_pipelines` and `_admin_url`
remain. No import from `kgspin_core.execution.pipeline_resolver`
anywhere in the demo tree.

## Dead code deleted

- `_STRATEGY_TO_PIPELINE_ID_LEGACY` dict (fan_out/discovery_rapid/
  discovery_deep → hyphen form).
- `_resolve_pipeline_config_ref(ref)` (Sprint 12's admin-lookup demo
  resolver — unneeded now that core does resolution).
- `_get_split_bundle(domain_id, pipeline_id)`.
- Legacy `load_split` code path on `_get_bundle`.
- `from dataclasses import replace` + the `bundle.execution_strategy=…`
  monkey-patch pattern (two call sites, clinical path).
- `resolve_pipeline_config` import in `demos/extraction/run_overnight_batch.py`
  (unused).
- `tests/unit/services/test_pipeline_resolve.py` (whole file; the
  `resolve_pipeline_config` surface it tested is gone).

## Tests

- Rewrote `tests/unit/test_pipeline_config_ref.py` — 18 tests pinning
  the canonical-name mapping, the strict whitelist, and the `None`-
  default handling.
- Added `tests/unit/services/test_pipeline_config_ref_dispatch.py` —
  5 tests spying on `KnowledgeGraphExtractor.run_pipeline` to confirm
  every `_run_*` wrapper passes `pipeline_config_ref` + `registry_client`
  with the expected canonical name.
- Deleted `tests/unit/services/test_pipeline_resolve.py` (pinned a
  deleted surface).
- Full suite: **242 passed** (baseline 235 → +7 net).

## Ancillary scripts updated

`run_overnight_batch.py`, `run_abc_comparison.py`, `run_kgen_smell_test.py`
all import `demo_compare` internals. Updated their
`_run_kgenskills` / `_parse_and_chunk` call sites to the new signatures
so they still import clean. Not run end-to-end in this sprint.

## Smoke-test outcome

See `smoke-test.md` for the full transcript. TL;DR:

- All 5 canonical pipelines dispatch correctly — admin `/pointer/…`
  hits are visible in the debug log, and core's `EXTRACTORS[config.extractor]`
  lookup succeeds for each.
- **3 zero-LLM pipelines (fan_out, discovery_rapid, discovery_deep)**
  complete end-to-end without errors.
- **2 agentic pipelines (agentic_flash, agentic_analyst)** dispatch but
  crash inside the extractor with internal bugs:
  - `agentic_analyst.py:67` — `h_module_prompt_template.format(...)`
    doesn't pass `chunk_index` → `KeyError`.
  - `agentic_flash` chunk loop — `KeyError('text')` on a chunk field.

## Upstream blockers to flag

**W3-A (kgspin-core) extractor hierarchy** — two internal bugs (above).
The CTO retry prompt already flagged this shape: "the agentic YAML
prompts aren't yet threaded through the extraction logic … don't iterate
on it in this sprint." Flagging here per that instruction. Demo-side
plumbing is complete; agentic slots will light up as soon as W3-A ships
the prompt-threading fix.

No blockers on W3-B (blueprint YAMLs — all 5 schema-2 configs registered
and resolve via admin) or W3-C (admin — schema-2 validation accepts the
W3-B YAMLs, `/pointer/pipeline_config:<name>:v1` returns 200 for each).
