# Dev Report — sprint-fix-runtime-bugs-20260427 (kgspin-demo-app)

**Branch:** `fix-runtime-bugs-20260427` (off `main` @ 27cfd2f)
**Date:** 2026-04-26
**Scope:** runtime hotfix — no Phase 3 VP reviews. Push, do not merge.

## What landed

Single commit on `fix-runtime-bugs-20260427`:

| File | Change | Bug |
|---|---|---|
| `demos/extraction/demo_compare.py` | `run_single_refresh`: build `_refresh_doc_metadata` dict in the cache-hit refresh path before the call sites that pass it as `document_metadata=`. | Bug 1 |
| `demos/extraction/demo_compare.py` | `_get_feedback_store`: import from `kgspin_tuner.feedback.store` (was `kgenskills.feedback.store`). | Bug 2 |
| `demos/extraction/routes/feedback.py` | Three local imports flipped from `kgenskills.feedback.models` to `kgspin_tuner.feedback.models` (`/api/feedback/false_positive`, `/false_negative`, `/true_positive`). | Bug 2 |
| `pyproject.toml` | Add `kgspin-tuner` to `[project].dependencies` and to `[tool.uv.sources]` as `path = "../kgspin-tuner", editable = true`. | Bug 2 |
| `uv.lock` | Regenerated to pick up the new editable source. | Bug 2 |

### Bug 1 — `NameError: _refresh_doc_metadata`

`run_single_refresh` (`demos/extraction/demo_compare.py:6707`) had call sites passing `document_metadata=_refresh_doc_metadata` without ever defining the variable, so the first refresh raised `NameError` immediately after the cache-hit `_cfg_hash` line. Fix: build the dict from the already-cached `info` payload (`company_name` + `doc_id` are the only keys consumed downstream by the H-module resolver override that this metadata exists to drive). `cik / accession_number / filing_date / fiscal_year_end` are intentionally blank on the cache-hit path because the refresh does not re-fetch `sec_doc`. The shape matches what `run_full` constructs upstream — same keys, same defaults.

### Bug 2 — `kgenskills` → `kgspin_tuner` import migration

The HITL feedback store carved out of `kgenskills` into `kgspin-tuner` (canonical home post-PRD-042). Three call sites in `demos/extraction/routes/feedback.py` plus the lazy import in `_get_feedback_store` still pointed at the old `kgenskills.feedback.*` module path, which crashed on the first POST to any `/api/feedback/*` route. Verified the target modules exist at `/Users/apireno/repos/kgspin-tuner/src/kgspin_tuner/feedback/{store,models}.py`. Added `kgspin-tuner` as an editable dep so `uv sync` resolves it.

## Bug 3 (clinical 0/0) — STILL OPEN, NOT DIAGNOSED

Did not get to Bug 3 in this sprint. The clinical demo path returning `0/0` is **untouched and unverified** in this commit. CEO should treat it as outstanding for the next sprint. No working-hypothesis recorded — opening the diagnosis is itself the next sprint's first task.

## End-to-end smoke — DEFERRED

I did not run any end-to-end smoke for this hotfix. **Static-only confirmation** for both bugs:

- Bug 1: read the surrounding call sites in `run_single_refresh`, confirmed the dict shape matches the `run_full` constructor, confirmed no other unbound references.
- Bug 2: confirmed `kgspin_tuner.feedback.{store,models}` exist on disk; confirmed `pyproject.toml` + `uv.lock` are consistent (editable source registered).

Paths NOT exercised: actual extraction-refresh request against a cached document; actual POST to `/api/feedback/false_positive | false_negative | true_positive`; `uv sync` from a clean venv. Recommend the next sprint open with a smoke pass on those three endpoints before any further feature work.

## Out-of-scope confirmations

- No Phase 3 VP reviews (per CTO direction — runtime hotfix only).
- No bundle / pipeline / installation YAML changes.
- No metadata-schema edits.
- No cross-repo edits in `kgspin-tuner` or `kgspin-interface` — this hotfix consumes the existing `kgspin-tuner` API surface as-is.

## Push, not merge

Branch is pushed to `origin/fix-runtime-bugs-20260427`. CEO lands.
