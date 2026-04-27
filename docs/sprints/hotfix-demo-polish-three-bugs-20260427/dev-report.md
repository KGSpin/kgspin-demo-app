# Hotfix: Demo-polish — three bugs combined (2026-04-27)

**Branch:** `hotfix-demo-polish-three-bugs-20260427` off `main` (clean)
**Commits:** 3 (one per bug)
**Status:** all bugs fixed, runtime-validated against the live demo on `:8080`.

## Per-bug summary

### Bug C — Q&A "Unknown pipeline: fan_out" (commit 6ef11a6)

**Root cause.** `_SLOT_PIPELINE_TO_CACHE_KEY` and `_SLOT_PIPELINE_LABELS`
in `demos/extraction/demo_compare.py` still held the pre-Wave-3 strategy
strings (`kgspin-default`, `kgspin-emergent`, `kgspin-structural`,
`fullshot`, `multistage`). The frontend (`PIPELINE_META` in
`static/js/slots.js`) sends the canonical 5-pipeline underscore names
(`fan_out`, `discovery_rapid`, `discovery_deep`, `agentic_flash`,
`agentic_analyst`), so every `POST /api/compare-qa/{ticker}` call
short-circuited at the dictionary lookup and returned
`{"error": "Unknown pipeline: <name>"}`.

**Fix.** Replace both dicts with the 5 canonical keys. Each KGSpin
strategy maps to `kgs_kg`; `agentic_flash → gem_kg`;
`agentic_analyst → mod_kg`. Labels now read "Signal Fan-out", "Rapid
Discovery", etc., matching the frontend slot panels.

**Smoke test.** After priming `_kg_cache` for UNH via two
`/api/slot-cache-check/...` calls, `POST /api/compare-qa/UNH` with
`graphs=[{pipeline:"fan_out"}, {pipeline:"agentic_analyst"}]` returns
`200` with 6 question results and answer labels `["Signal Fan-out",
"Agentic Analyst"]` — the previously-failing path.

### Bug B — Comparison Matrix Tokens row shows 0 (commit 1b95c0d)

**Root cause.** The LLM run-detail endpoints (`/api/modular-runs`,
`/api/gemini-runs`) and `/api/slot-cache-check` all sourced their
`stats.tokens` value exclusively from `run_data["total_tokens"]`. Some
recent cached runs (e.g. `~/.kgenskills/logs/modular/JNJ/...@2026-04-21T23:52`
and `~/.kgenskills/logs/gemini/JNJ/...@2026-04-22`) saved `total_tokens=0`
at the top level even when the underlying LLM call did consume tokens —
the f5671bc upstream fix that surfaces `tokens_used` on
`Provenance.to_dict()` lands the count at `kg.provenance.tokens_used`,
but the readers never looked there. End result: every Agentic Analyst
slot rendered "0" in the Comparison Matrix's Tokens row, killing the
LLM-vs-KGSpin cost comparison the demo hinges on.

**Fix.** Add `_resolve_total_tokens(run_data)` in
`demos/extraction/routes/runs.py` that tries top-level `total_tokens`
first, then falls back to `kg.provenance.tokens_used` (Phase 2 canonical
surface) and finally to `kg.provenance.total_tokens` (pre-Phase-2 legacy
field). Wired into `gemini_run_detail` and `modular_run_detail`. Same
fallback inlined into `slot_cache_check` in `demo_compare.py` so the
slot's initial cache-hit render also benefits. KGSpin pipelines have
zero LLM tokens by design and still resolve to 0 (correct).

**Smoke test.**
- `GET /api/modular-runs/UNH/0` (fresh post-Phase-2 run) →
  `stats.tokens = 307989` ✓
- `GET /api/modular-runs/JNJ/2` (legacy run with tokens stored in
  top-level `total_tokens`) → `stats.tokens = 370503` ✓
- `GET /api/modular-runs/JNJ/0` (genuinely tokenless save — neither
  top-level nor `kg.provenance` has any token field) still returns 0,
  which is the correct signal that this specific cached run never
  captured tokens. Re-running the JNJ extraction would write a fresh
  log with the correct value.

### Bug A — Multi-hop scenario picker empty (commit 8e02df9)

**Root cause.** Two issues, stacked.
1. `/api/multihop/scenarios` and `/api/multihop/run` both did
   `from demos.extraction.scenarios import ...` (and same for `judge`).
   `demos/extraction/demo_compare.py` is invoked as a script
   (`uv run python demos/extraction/demo_compare.py`), so `sys.path[0]`
   is the `demos/extraction/` directory and there is no `demos`
   top-level package on the path. Every request hit
   `ModuleNotFoundError: No module named 'demos'` and the endpoint
   returned `500`.
2. Even after fixing the import, `scenario_to_dict()` returned
   `{"id": ...}` while the picker JS at `compare-runner.js:2619, 2624`
   reads `s.scenario_id` for both the lookup map and the `<option>`
   value attribute. Options would have rendered with `value="undefined"`
   and the lookup map would have collapsed to `{undefined: lastScenario}`.

**Fix.** Switch the imports to same-directory form (`from scenarios`,
`from judge`) so the script-mode `sys.path` resolves them. Rename the
serialized field from `id` → `scenario_id` so it matches the dataclass
attribute and the picker JS.

**Smoke test.** `GET /api/multihop/scenarios` returns `200` with 4
scenarios; the first entry has `scenario_id="jnj_acquisitions_litigation"`
(was previously a 500 with `Internal Server Error` body).

## Validation summary

All three fixes runtime-validated by hitting the running demo on
`http://127.0.0.1:8080` after a kill-and-restart of `start-demo.sh`
(the previously-running instance was on stale code, hence the original
500 reproduction). The matrix Tokens row was not exercised through the
full UI click path because that requires a fresh comparison run (~25 min
of LLM time for a real 10-K); the API endpoints that feed the matrix
were verified directly and shown to return the correct token values
for both fresh and legacy cached runs.

## Out of scope (per CTO brief)

- No bundle / orchestrator / kgspin-core changes were made.
- The genuinely-tokenless cached run for JNJ (modular index 0) is
  preserved as 0 — re-extraction is the only way to repair it. This is
  a data artifact, not a bug in the read path.
- No Phase 2.1 INSTALLATION work, no Row 8 / Row 12 RCA work, no VP
  reviews requested.
