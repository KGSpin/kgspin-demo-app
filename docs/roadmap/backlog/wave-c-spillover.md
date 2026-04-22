# Wave C spillover — items deferred to Wave D

**From:** Dev team (kgspin-demo-app)
**Date:** 2026-04-22
**Branch where logged:** `wave-c-polish`

These are items surfaced during the Wave C polish sweep that would
require touching runtime code (or coordinated cross-repo work) and are
therefore out of Wave C scope per the CTO preamble's light-touch rule.

## 1. `kgenskills` runtime identifier sweep

The legacy name appears ~70 times across the running app as a
**wire-format / state-key / path identifier**, not just a stale label.
Changing any of these requires a coordinated edit across Python + JS +
HTML + cached-data migration. Recommended as a dedicated structural
sprint.

### 1a. Pipeline identifier in SSE/JSON wire format
- `demos/extraction/demo_compare.py` — ~35 sites with
  `"pipeline": "kgenskills"` / `"step": "kgenskills"` in SSE event
  payloads. The browser matches on this exact string.
- `demos/extraction/static/compare.html` — ~40 sites with
  `d.pipeline === 'kgenskills'`, DOM IDs `kgenskills-graph`,
  `kgenskills-stats`, `kgenskills-toolbar`, `kgenskills-progress-bar`,
  CSS selectors `.winner-badge.kgenskills`, and dropdown option values
  `<option value="kgenskills">KGSpin</option>`.
- `demos/extraction/routes/feedback.py:45,137` —
  `body.get("pipeline", "kgenskills")` defaults.

### 1b. Cache directory path
- `~/.kgenskills/logs/<pipeline>/<DOC_ID>/...` is the on-disk cache
  root used by every `*RunLog` class in
  `demos/extraction/cache/run_log.py`.
- Renaming breaks all existing cached runs (no migration path).
- Module docstring references in
  `demos/extraction/run_overnight_batch.py:12` and
  `demos/extraction/run_kgen_smell_test.py:6` track the same path.

### 1c. Python logger namespace
- `demos/extraction/demo_compare.py:52-54` — `logging.getLogger("kgenskills")`
  + `.setLevel(DEBUG)` captures the kgspin-core internals' debug
  stream. The logger name `"kgenskills"` must match what core emits;
  rename is a cross-repo change.

### 1d. Legacy `kgenskills.*` imports
- `demos/extraction/pipeline_common.py:659,664` — `from
  kgenskills.execution.chunk_selector import ...` inside a guarded
  try/except. Effectively dead code (the package is gone) but the
  import attempt is still made.
- `demos/extraction/demo_ticker.py` — `from kgenskills.data_sources.*`
  and `from kgenskills.services.entity_resolution`. `demo_ticker.py`
  appears to be a legacy module; candidate for deletion if confirmed
  unused.
- `demos/extraction/run_abc_comparison.py:48-49`, `demo_compare.py:1063`,
  `6382` — same pattern, all inside try/except / fallback blocks.
- Docstring reference in `demos/extraction/demo_compare.py:6385`
  (warning message explaining the core-split).

### 1e. Function name `_run_kgenskills`
- Defined in `demos/extraction/extraction/kgen.py:12`; referenced in
  `extraction/__init__.py`, `demo_compare.py`, `run_overnight_batch.py`,
  `run_abc_comparison.py`, `run_kgen_smell_test.py`, and
  `tests/unit/services/test_pipeline_config_ref_dispatch.py`.
- Rename safe but wide — ~10 call sites + 1 test assertion.

## 2. `ticker` / `nct` parameter-name carryover

Wave A renamed the **wire-format** from `ticker`/`nct` → `doc_id` but
left internal Python parameter names untouched (shim strategy
documented in Wave A §"Route handler internal rename"). Wave B removed
shims in the extracted route modules (`routes/runs.py`, `routes/corpus.py`,
`routes/feedback.py`) but the shims remaining in `demo_compare.py`
still correspond to routes that haven't been extracted yet.

Wave C could not un-shim without touching runtime-visible signatures:

- `demos/extraction/extraction/kgen.py:13` —
  `_run_kgenskills(text, company_name, ticker, bundle, ...)`. Renaming
  `ticker` → `doc_id` touches every call site (5 modules).
- `demos/extraction/demo_compare.py` — ~27 handler bodies still have
  `ticker = doc_id` Wave-A shim one-liners.
- Helper functions reached into by the big orchestrators (e.g. builds
  `f"{ticker}_10K"` strings that become part of cache keys) continue
  to thread the legacy name.

Belongs in the Wave C follow-on that extracts the remaining pipeline
orchestrators (`run_comparison`, `run_single_refresh`,
`_run_clinical_comparison`, `run_intelligence`, `run_impact`) out of
`demo_compare.py`, per Wave B §3b.

## 3. Test-fixture domain literals

The following tests use ticker literals (`"JNJ"`, `"PFE"`, `"AAPL"`,
`"MSFT"`, `"TST"`) as fixture identifiers:

- `tests/integration/test_lander_to_registry.py`
- `tests/unit/test_sec_lander.py`
- `tests/unit/test_yahoo_rss_lander.py`
- `tests/unit/test_marketaux_lander.py`
- `tests/unit/test_registry_http.py`
- `tests/unit/test_demo_compare_registry_reads.py`
- `tests/unit/test_demo_compare_llm_endpoints.py`
- `tests/unit/services/test_pipeline_config_ref_dispatch.py`

These are tests of domain-specific lander/route behaviour where the
parameter name is literally `ticker`. Neutralising the fixture literal
alone is meaningless — the underlying function signature would need to
change first (see §2). Logged here so Wave D handles them as part of
the parameter-rename.

## 4. Sprint-ID / internal-reference comment density

`demos/extraction/demo_compare.py` contains ~80 `# Sprint N: ...` and
`# INIT-001 Sprint N: ...` inline comments that trace historical
rationale. Wave C cleaned only those that combined a stale sprint
label **with** a pre-rename term (`kgenskills` package, etc.).

A full sweep (convert to ADR-ref comments, prune chore labels, retain
only non-obvious "why") is worthwhile but out of light-touch scope.
Roughly 2–3 hours of focused editorial work.

## 5. `bundles/legacy/` empty directory

Wave A retired all runtime references. The directory itself (empty)
is still on disk. Safe to `git rm -rf demos/extraction/bundles/legacy/`
in a one-line cleanup commit, but nothing reads from it so it is
harmless.

## 6. `docs/sprints/_templates/dev-report.md` missing

The CTO preamble asks Wave C to refresh this template if it references
old terms. The template does not exist in this repo. Creating one
from the current Wave B dev-report's structure would give future
sprints a consistent starting point, but is out of polish scope —
tracking here so a subsequent doc-infra sweep can decide whether to
materialise it.

## 7. `docs/personas/` directory

No personas directory exists in this repo (unlike sibling repos that
ship VP/CTO persona files). The preamble's item 6 is a no-op here.

---

— Dev team
