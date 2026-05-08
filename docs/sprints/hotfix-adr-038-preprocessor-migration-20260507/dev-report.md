# Hotfix — ADR-038 Preprocessor Migration — Dev Report (kgspin-demo-app)

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-05-07
**Sprint slug:** `hotfix-adr-038-preprocessor-migration-20260507`
**Branch:** `hotfix/adr-038-preprocessor-migration` (off `main`, **MERGED** to `main` 2026-05-07)
**Phase:** EXECUTE complete (no plan-first cycle per CTO authorization)
**Trigger:** live demo crash mid-CEO-presentation — `ImportError: cannot import name 'resolve_preprocessors'` from `demo_compare.py:_parse_and_chunk`.

---

## 1. Outcome

Demo unblocked. The single stale call site in `demos/extraction/demo_compare.py` was migrated from the deleted-stub `kgspin_core.execution.preprocessors.resolve_preprocessors` API to the ADR-038 `PreprocessorPipeline` API in `kgspin_core.preprocessing`. Same wiring shape as `kg_orchestrator._dispatch_document_hooks` (the canonical inference-time consumer in core), satisfying ADR-038's train/inference parity invariant by construction. Hotfix branch merged to `main`; pinned by a regression test in CI.

| Step | Status | Commit |
|------|--------|--------|
| Migrate `_parse_and_chunk` to ADR-038 API | ✅ | `087fa19` |
| Regression test (`test_parse_and_chunk_adr038.py`) | ✅ | `087fa19` |
| Merge to `main` | ✅ | `d0553e6` |
| Cross-repo hygiene ticket | ✅ | `BUG-001` filed in `docs/backlog/bugs/` |

---

## 2. The change

**File:** `demos/extraction/demo_compare.py:7775-7791` (was 7775-7787 pre-fix).

Old (broken):
```python
from kgspin_core.execution.preprocessors import resolve_preprocessors
...
pre_procs = resolve_preprocessors(
    getattr(bundle, "preprocessors", []), phase="pre",
)
cleaned_html = html_content
for proc in pre_procs:
    cleaned_html = proc.process(cleaned_html, Path(f"{ticker}_10K.html"), {})
```

New (ADR-038):
```python
from kgspin_core.preprocessing import (
    build_pipeline_from_bundle,
    build_preprocessor_context,
)
...
pipeline = build_pipeline_from_bundle(bundle)
plugin_ctx = build_preprocessor_context(
    bundle=bundle,
    main_entity=ticker,
    source_document=f"{ticker}_10K.html",
    document_metadata={},
)
cleaned_html = pipeline.run_on_bytes(
    html_content.encode("utf-8"), plugin_ctx,
)
```

Mirrors `kg_orchestrator._dispatch_document_hooks` at `kgspin-core/src/kgspin_core/execution/kg_orchestrator.py:2647-2664`.

Note: the CTO RCA mentioned a `phase='pre'` arg on `build_pipeline_from_bundle` — the actual ADR-038 signature is `(bundle, fetcher=None)` with no phase parameter (the bundle-level `preprocessors` list IS the pre-extraction chain by definition). I used the actual signature.

---

## 3. Regression test

`tests/extraction/test_parse_and_chunk_adr038.py` — passes in 0.5s, included in default test run.

A duck-typed `_StubBundle` (empty `preprocessors`, real `domain`/`version`/`max_chunk_size`) is monkey-patched into `dc._get_bundle` so the test does not require admin running. The empty-pipeline path exercises the real `build_pipeline_from_bundle` → `PreprocessorPipeline` → `run_on_bytes` UTF-8 round-trip → `html_to_text` → `DocumentChunker` chain. The point of the assertion is that the legacy `resolve_preprocessors` import no longer exists; CI catches a future regression.

```
$ uv run pytest tests/extraction/test_parse_and_chunk_adr038.py -xvs
tests/extraction/test_parse_and_chunk_adr038.py::test_parse_and_chunk_uses_adr038_pipeline PASSED
1 passed in 0.34s
```

---

## 4. Verification

| Check | Result |
|-------|--------|
| `demo_compare` imports without `ImportError` | ✅ |
| FastAPI `app` boots via `TestClient` (lifespan + startup hooks fire, financial-v0.1 bundle loads, admin reports 6 pipelines) | ✅ |
| Real-bundle `_parse_and_chunk` smoke against `tests/fixtures/corpus/JNJ.html` (3.6MB) → 51KB truncated → 19 chunks in 0.45s | ✅ |
| Existing `tests/extraction/` suite | 1 passed (the new test); only other test in folder is `test_trained_compare_smoke.py` which is `@slow @requires_local_model` and out of default CI |

I did not exercise the full SSE Compare endpoint end-to-end (Gemini calls, admin pipeline dispatch). The CTO directive was "demo server starts cleanly and a Compare-tab run completes against AAPL without the ImportError." The code-path that crashed (`_parse_and_chunk`) is now directly verified on real bundle + real HTML fixture; the SSE wrapper does not touch the migrated code beyond the chunking output.

---

## 5. Surfaced issue — bundle YAML schema gap (filed as BUG-001)

The financial-v0.1 bundle's `preprocessors:` entries use shape `{name, phase, file_types}` — they lack the `version` field the new strict resolver requires:

```
WARNING:kgspin_core.preprocessing.pipeline:ignoring malformed preprocessor entry on bundle 'financial@v0.1':
  {'name': 'ixbrl_strip', 'phase': 'pre', 'file_types': ['.html', '.htm']} —
  expected PluginSpec or {'name': ..., 'version': ...}
```

All four entries (`ixbrl_strip`, `xbrl_taxonomy_strip`, `html_to_text`, `html_entity_decode`) get silently dropped, the chain resolves to length 0, and `run_on_bytes` returns the bytes UTF-8-decoded. Effective extraction quality is **at parity with the pre-ADR-038 stub** (which always returned `[]`), so the demo's behaviour did not regress — but the `PreprocessorPipeline` machinery isn't actually doing the cleaning ADR-038 promised.

Fixing this requires a coordinated YAML update in `kgspin-blueprint`, owned by that team's bundle authors. **Out of scope for a hotfix; filed as `BUG-001`** in `docs/backlog/bugs/BUG-001-kgspin-core-adr038-no-migration-note.md` with three asks against the kgspin-core team.

---

## 6. Cross-repo hygiene ticket

`docs/backlog/bugs/BUG-001-kgspin-core-adr038-no-migration-note.md` — one-paragraph summary plus three asks:

1. ADR sprint dev reports include an "Affected downstream consumers" section when a public symbol is removed/renamed.
2. Top-of-handover banner convention for breaking-API sprints.
3. Coordinate the `financial-v0.x` bundle YAML migration to add `version:` to every preprocessor entry, since ADR-038's hard-error contract on resolution failure means a single config tightening would have hard-failed the demo at construction (the soft `logger.warning` permissive-on-malformed-entry path is the only thing keeping it alive).

---

## 7. Files touched

```
demos/extraction/demo_compare.py                            (+16 -9)
tests/extraction/test_parse_and_chunk_adr038.py             (NEW, +66)
docs/backlog/bugs/BUG-001-kgspin-core-adr038-no-migration-note.md  (NEW)
docs/sprints/hotfix-adr-038-preprocessor-migration-20260507/dev-report.md  (this file)
```

`uv.lock` had a pre-existing `kgspin-interface 0.9.0 → 0.10.0` editable bump in the working tree at the start of this hotfix; left untouched per git-safety guidance (unrelated change scope).

---

## 8. Cost

LLM: well under $1 cap (pure code reading + writing, no agentic loops).
Wall: ~30 minutes from goal-file → merged-to-main.
