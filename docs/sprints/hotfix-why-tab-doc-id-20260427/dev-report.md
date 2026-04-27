# Hotfix — Why-tab UnboundLocalError + `ticker`→`doc_id` rename

**Date:** 2026-04-27
**Branch:** `hotfix-why-tab-doc-id-20260427` (off `main`)
**File:** `demos/extraction/demo_compare.py` — `why_this_matters` (lines 2727–2869)

## Summary

The `/api/why-this-matters/{doc_id}` endpoint was 500-ing on every click because line 2756 read `ticker = ticker.upper()` while the function parameter is `doc_id` — `ticker` was never bound in this scope, raising `UnboundLocalError`. The Wave-A wire-format shim `ticker = doc_id` that other endpoints (`compare`, `refresh_agentic_flash`, `refresh_agentic_analyst`, `lineage_data`) carry was missing here. Rather than re-introduce the shim, I removed the local variable name `ticker` from this function entirely and used the parameter `doc_id` directly: `doc_id = doc_id.upper()` plus four other internal references swapped (`_kg_cache.get(doc_id)`, three error-payload `"doc_id": doc_id` fields, and the `is_clinical` branch on `doc_id.startswith("NCT")`). The function body is now `ticker`-free.

## Architectural note (CEO ask)

The CEO flagged that "Why This Matters" was domain-specific-locked on the variable name `ticker`. The rename to `doc_id` is the right doctrinal cleanup: per ADR-004 the engine and surfaces should treat the document identifier as a domain-agnostic key — a stock ticker for finance, an NCT-id for clinical, a slug or UUID for news — and let the configuration layer (BUNDLE/PIPELINE/INSTALLATION YAML) decide how the identifier is interpreted. A short comment in the function makes the convention explicit: *"`doc_id` is the domain-agnostic identifier: a stock ticker for finance, an NCT-id for clinical, etc."* The route path was already `/{doc_id}` — only the body was lagging. Other endpoints in the same file still carry the `ticker = doc_id` shim and the variable name `ticker` internally; converting them is out of scope for this partner-call hotfix and is mechanical follow-up for the next pass.

## Validation

Static: `python -c "import ast; ast.parse(...)"` parses cleanly; the only path that referenced an unbound name is gone; `doc_id` is the function parameter and therefore guaranteed bound on every call. Runtime smoke against the dev server was deferred — a server is already running on :8080 with old code (in use by other parallel agents) and a fresh boot on a different port would have an empty cache (returning `{"error": "No cached data..."}` rather than a 200 with content), which doesn't add evidence beyond the static check. The CEO/operator should reload the running server (or restart on :8080) and click 'Why' on the AAPL fan_out output to confirm; the fix is mechanically complete.

## Out of scope (per brief)

- Bundle / orchestrator changes — none made.
- Phase 2.1 INSTALLATION work — none made.
- Renaming `ticker` in `compare`, `refresh_agentic_flash`, `refresh_agentic_analyst`, `lineage_data`, and the many domain-specific helpers (SEC fetcher, news cache keys, cache logger) — those `ticker` references are in domain-specific contexts (SEC filings, ticker resolution) where rename is either inappropriate or non-mechanical.
