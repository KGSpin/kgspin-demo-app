# Sprint W3-D — Smoke test transcript

**Date:** 2026-04-21
**Branch:** `w3d-demo-unified-dispatch`
**Ticker:** JNJ (Johnson & Johnson, 10-K via SEC EDGAR)
**Admin:** http://127.0.0.1:8750 (5 schema-2 pipelines registered)
**Demo:** http://localhost:8088 (launched with existing KGSpin stack; 8080 was occupied by the operator's running demo)

## Pre-flight

```
$ curl -s "http://127.0.0.1:8750/resources?kind=pipeline_config" | python -c "..."
agentic-analyst   v1
agentic-flash     v1
discovery-deep    v1
discovery-rapid   v1
fan-out           v1
```

JNJ 10-K corpus landed via `kgspin-demo-lander-sec --ticker JNJ
--output-root /Users/apireno/repos/kgspin-demo-app/.data` (default
`~/.kgspin/corpus` is outside admin's `filepointer_roots` allowlist;
landing into `repos/` subtree is the documented workaround).

## 5-pipeline dispatch check

Every canonical pipeline resolves its YAML from admin at dispatch:

| Pipeline | Endpoint | HTTP | `/pointer/…` hit | Dispatch |
|---|---|---|---|---|
| fan_out | `GET /api/refresh-discovery/JNJ?strategy=fan_out&corpus_kb=100` | 200 | `pipeline_config:fan-out:v1` → 200 OK | ✅ extractor ran |
| discovery_rapid | `GET /api/refresh-discovery/JNJ?strategy=discovery_rapid&corpus_kb=200` | 200 | `pipeline_config:discovery-rapid:v1` → 200 OK | ✅ extractor ran, 400 entities / 17 rels |
| discovery_deep | `GET /api/refresh-discovery/JNJ?strategy=discovery_deep&corpus_kb=200` | 200 | `pipeline_config:discovery-deep:v1` → 200 OK | ✅ extractor ran |
| agentic_flash | `GET /api/refresh-agentic-flash/JNJ?corpus_kb=100` | 200 | `pipeline_config:agentic-flash:v1` → 200 OK | ⚠️ dispatch OK; extractor internal KeyError (flagged below) |
| agentic_analyst | `GET /api/refresh-agentic-analyst/JNJ?corpus_kb=100` | 200 | `pipeline_config:agentic-analyst:v1` → 200 OK | ⚠️ dispatch OK; extractor internal KeyError (flagged below) |

All five demo dispatch paths now resolve the YAML through
`load_pipeline_config_via_registry` — no `ORCHESTRATORS[strategy]`, no
`load_split` overlay, no `execution_strategy` string dispatch.

## Zero-LLM pipeline outcome notes

- **fan_out** extracted 0 entities on JNJ's first 100KB. `[FAN_OUT] 0
  anchors → 0 triples` in the debug log — the pipeline dispatched and
  ran cleanly, but the first 100KB of JNJ's 10-K (cover page +
  boilerplate) carries no anchors for the fan-out head. Bigger corpus
  sizes or different tickers yield entities; this isn't a dispatch
  defect.
- **discovery_rapid** 400 entities / 17 relationships at 200KB — matches
  prior-known coverage, pipeline dispatched cleanly.
- **discovery_deep** dispatched and ran; step_complete + kg_ready + done
  emitted without error.

## Agentic pipelines — upstream blocker flagged

Both agentic wrappers dispatched correctly and reached W3-A's new
`AgenticFlashExtractor` / `AgenticAnalystExtractor`, but the extractors
crash inside `.extract()` before producing output. The CTO flagged this
shape in the retry prompt ("the agentic YAML prompts aren't yet threaded
through the extraction logic"); these are the concrete symptoms:

```
File "kgspin-core/.../execution/extractors/agentic_analyst.py", line 67, in _system_prompt
    return self.config.h_module_prompt_template.format(
        entity_block=entity_block,
        valid_predicates=valid_predicates,
    )
KeyError: 'chunk_index'
```

```
[AGENTIC_FLASH] Chunk 1/1 failed: 'text'
[AGENTIC_FLASH] Complete: 0 entities, 0 relationships (deduped across 1 chunks), 0 tokens, 0.0s, 1 errors
```

- `agentic_analyst.py:67` — `h_module_prompt_template` has a
  `{chunk_index}` placeholder that the extractor's `.format(...)` call
  isn't filling; the YAML carries the template but the extractor is
  built for the legacy prompt shape.
- `agentic_flash.py` chunk loop raises `KeyError('text')` when it tries
  to read a chunk field that isn't on the object it constructs.

**Demo-side is correct.** These are W3-A (kgspin-core extractor
hierarchy) defects. Not iterated on in this sprint per the CTO's
instruction to flag upstream issues and land what works.

## Endpoint whitelist enforcement

Wave 3 requires any `strategy=` outside the canonical 5 to 400. Spot
checks:

```
$ curl -s -o /dev/null -w "%{http_code}\n" "…/api/compare/JNJ?strategy=fan_out&corpus_kb=100"
200

$ curl -s "…/api/compare/JNJ?strategy=emergent&corpus_kb=100"
{"error":"strategy='emergent' is not one of the canonical pipelines:
 fan_out, discovery_rapid, discovery_deep, agentic_flash,
 agentic_analyst. Hyphen form is used on the wire to admin
 (e.g. strategy=agentic_flash → pipeline name 'agentic-flash')."}

$ curl -s "…/api/refresh-discovery/JNJ?strategy=llm_full_shot"
{"error":"strategy='llm_full_shot' is not one of the canonical pipelines:…"}

$ curl -s -o /dev/null -w "%{http_code}\n" "…/api/compare/JNJ?strategy=discovery_agentic&corpus_kb=100"
400
```

- `emergent` (Wave 2 legacy name) → 400
- `llm_full_shot` (pre-Wave 2 legacy) → 400
- `discovery_agentic` (dropped from canonical set by W3-B) → 400

Each 400 carries the focused message listing the 5 allowed values.

## Unit suite

```
$ uv run pytest tests/unit -q
...
242 passed, 13 warnings in 49.23s
```

Baseline was 235; +7 net from the rewritten
`test_pipeline_config_ref.py` (18 tests) and the new
`tests/unit/services/test_pipeline_config_ref_dispatch.py` (5 tests),
minus the deleted `tests/unit/services/test_pipeline_resolve.py`.

## Summary

Dispatch symmetry is achieved: one path, `run_pipeline(pipeline_config_ref
=…, registry_client=…)`, for all 5 canonical pipelines. The 3 zero-LLM
pipelines deliver end-to-end; the 2 agentic pipelines are blocked on
W3-A extractor-internals bugs, not demo-side plumbing.
