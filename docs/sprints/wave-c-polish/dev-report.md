# Wave C — Polish — Dev Report

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-22
**Branch:** `wave-c-polish`
**Baseline:** `main` @ `9702d72` (post-Wave-B merge)

---

## TL;DR

Single polish commit. 4 files edited, 1 new backlog file. No runtime
behaviour changed; 204 tests pass (same baseline as Wave A + B).

## Changed

- **`src/kgspin_demo_app/landers/_shared.py`** — `sha256_file`
  docstring: dropped `ADR-036` citation and `Sprint 07` reference;
  kept the substantive note that the interface helper serialises
  through canonical JSON and is therefore not the right tool for raw
  file bytes.
- **`demos/extraction/demo_compare.py`** — three comment neutralisations:
  - Line 52 area (`# INIT-001 Sprint 02: ...`) → plain "log + surface
    any failure" rationale; sprint ID dropped.
  - Pre-news-fetch block (5-line `# Sprint 06 Task 2: ...` + `# Pre-Sprint-06
    the code imported from kgenskills.data_sources.*`) → collapsed to a
    single sentence describing the current `kgspin-plugin-news-financial`
    path; pre-rename historical explanation retired.
  - SEC non-cached path header (`# --- Sprint 33.14c: Non-cached path ...`) →
    `# Non-cached path: full SEC extraction via _run_kgenskills.`
- **`docs/architecture/decisions/ADR-005-llm-model-registry-kind.md`** —
  `kgspin-archetypes` → `kgspin-blueprint` in three locations (seed-files
  path, Alternative 2 heading + body, Ack protocol). CLI command string
  `kgspin-admin sync archetypes <blueprint>` was **not** changed (runtime
  CLI verb — cross-repo concern, logged to spillover).

## New

- **`docs/roadmap/backlog/wave-c-spillover.md`** — Wave D items
  surfaced during the sweep that required runtime-code changes and so
  were out of light-touch scope. Covers:
  1. `kgenskills` runtime identifier sweep (~70 sites across
     Python + JS + HTML + cache paths + logger name).
  2. `ticker` / `nct` parameter-name carryover inside `_run_kgenskills`
     and remaining Wave-A shims in `demo_compare.py` (~27 handlers
     still unextracted).
  3. Test-fixture ticker literals (meaningless to neutralise without
     §2 first).
  4. Dense sprint-ID inline comments in `demo_compare.py` (~80 sites)
     — a full editorial sweep is worth ~2–3h but out of scope.
  5. `bundles/legacy/` empty directory — trivial `git rm` pending.
  6. `docs/sprints/_templates/dev-report.md` — not present; deferred
     to doc-infra sweep.
  7. `docs/personas/` — not present; preamble item 6 is a no-op here.

## Items considered and deliberately skipped

- **Test fixtures** (preamble item 1) — no `"ticker": "AAPL"` / `"MSFT"` /
  `"NVDA"` / `"TSLA"` / `"GOOG"` JSON literals found. The ticker-string
  fixtures that exist are positional arguments to SEC / Yahoo / Marketaux
  lander tests where the parameter is literally `ticker`. Logged to
  spillover §3.
- **ADR-001 (`kg_filters` placement)** — references to the
  `kgenskills.*` namespace are load-bearing historical context
  explaining why the vendor decision was made (the namespace's
  disappearance was the trigger). Rewriting would misrepresent the
  decision. Left as-is.
- **`docs/architecture/llm-call-sites.md`** — every `_run_kgenskills`
  mention is a reference to the still-current function symbol. No
  staleness to fix.
- **`~/.kgenskills/logs/` cache paths** in
  `demos/extraction/cache/run_log.py` — runtime-visible filesystem
  paths. Structural. Logged to spillover §1b.
- **~50 other `# Sprint N:` comments** in `demo_compare.py` that
  don't collide with pre-rename terms — not in the CTO's polish
  definition (only the combined "stale sprint + pre-rename" set).
  Logged to spillover §4.

## Tests

`uv run pytest --ignore=<3 pre-existing-fail-to-collect-files>` →
**204 passed, 8 warnings in 49.49s.**

Same three files fail to collect as in Wave A + B (kgspin-core
circular import, not introduced by this sprint):

- `tests/unit/services/test_pipeline_config_ref_dispatch.py`
- `tests/unit/test_demo_compare_registry_reads.py`
- `tests/unit/test_pipeline_config_ref.py`

— Dev team
