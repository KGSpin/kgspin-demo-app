# PRD-004 v5 Phase 5B — Manual UI Smoke

**Date:** 2026-05-01
**Branch:** `sprint-prd-004-v5-phase-5b-joinable-cache-20260430`
**Run with:** `uv run python demos/extraction/demo_compare.py`,
then `http://localhost:8080/compare`.

This sprint's headline win is data-layer correctness — there's no new
UX. The smoke checklist verifies the lazy-build path works end-to-end
and confirms the slot's pipeline-specific KG is what GraphRAG retrieves
against (no silent fan_out fallback).

---

## Pre-flight (one-time per machine)

- [ ] All three target tickers have a D2-augmented lander tree:
  ```bash
  kgspin-demo-lander-sec --ticker JNJ --skip-registry
  kgspin-demo-lander-sec --ticker AAPL --skip-registry
  kgspin-demo-lander-clinical --nct NCT00174785 --skip-registry  # JNJ-Stelara
  ```
  After each, confirm:
  ```
  ~/.kgspin/corpus/{domain}/{source}/{ticker}/{date}/{doc_kind}/
  ├── raw.html OR raw.json
  ├── source.txt          # NEW (D2)
  └── manifest.json       # NEW (D2)
  ```
- [ ] Embedding model downloaded once (~90MB, lazy on first build):
  ```bash
  uv run python -c "from kgspin_demo_app.services.doc_corpus_builder import _get_embedder; _get_embedder()"
  ```

## Compare tab — populate kg_cache

- [ ] Switch domain to **Financial**, enter `JNJ`, click **Run**.
- [ ] Confirm at least 2 slots populate KGs (any pipelines — fan_out
  for kgspin-default, agentic_flash for gemini, agentic_analyst for
  modular).
- [ ] No console errors.

## Modal Why-tab Single-shot Q&A — headline correctness fix

For each slot type you populated (fan_out / agentic_flash / agentic_analyst):

1. Click the slot to open its modal.
2. Click the **Why** tab.
3. **Single-shot Q&A** sub-tab is default-active.
4. Pick a templated scenario from the dropdown (or type "Who is the CEO?").
5. Mode: **+1-hop graph** (A2).
6. Click **Run**.

For each pipeline:

- [ ] First click: spinner shows ~15-30s (cold build of `_doc/` + the
      slot-pipeline-specific `_graph/{graph_key}/`). No SSE progress
      UX yet (deferred).
- [ ] Subsequent clicks on the same modal: <1s response (warm cache).
- [ ] Open the browser dev tools network tab → inspect the
      `/api/scenario-a/run` response payload's `debug` field:
  ```json
  "debug": {
    "graph_index_pipeline": "<the slot's pipeline>",
    "graph_index_bundle": "<the slot's bundle>",
    "doc_corpus_status": "hit" | "built" | "legacy",
    "graph_index_status": "hit" | "built" | "legacy"
  }
  ```
  **`graph_index_pipeline` MUST match the slot's pipeline.** If it says
  `fan_out` when you opened an `agentic_flash` slot, the headline fix
  has regressed.

## Verify the on-disk artifacts

After the first modal Run on (e.g.) an `agentic_flash` slot for JNJ:

- [ ] `~/.kgspin/corpus/financial/sec_edgar/JNJ/{date}/10-K/_doc/` exists with:
  - `chunks.json`
  - `chunk_embeddings.npy`
  - `bm25_index.pkl`
  - `manifest.json` (carries `doc_key`, `chunks_count`, `plaintext_sha`)
- [ ] `~/.kgspin/corpus/financial/sec_edgar/JNJ/{date}/10-K/_graph/agentic_flash__financial-default__core-{sha[:7]}/` exists with:
  - `graph_nodes.json` (rows have `parent_doc_offsets` + `provenance`)
  - `graph_edges.json` (rows have `evidence_char_span` + `kind` + `provenance`)
  - `graph_node_embeddings.npy`, `graph_edge_embeddings.npy`
  - `manifest.json` (carries `graph_key`, `pipeline`, `bundle`, `nodes_count`,
    `edges_count`, `join_confidence_breakdown`)
- [ ] Repeat for a `fan_out` slot — confirm a SEPARATE
      `_graph/fan_out__financial-default__core-{sha[:7]}/` directory.

## Modal Why-tab — operator-friendly errors

- [ ] Open a modal for a slot you DIDN'T Compare-Run → click Run →
      response is HTTP 503 with body
      `{"error": "kg_not_in_cache", "detail": "No 'agentic_flash' KG in
      kg_cache for ...; Run the slot on the Compare tab first..."}`.
      The modal UI surfaces this as a status message (existing behavior).

- [ ] Try a totally unknown ticker (`ZZZZ`) → HTTP 503 with
      `{"error": "lander_not_found", "detail": "..."}`. (Pre-5B this
      was `corpus_not_built`; the new error is more diagnostic.)

## Existing functionality unaffected

- [ ] Compare-tab Run still works for all 5 pipelines (no regression
      from the wiring changes).
- [ ] `/compare` page loads, tab strip is unchanged from the
      fixup-20260430 sprint (Compare / Intelligence / Impact).
- [ ] Modal Lineage tab unchanged (followup #12 not yet shipped).
- [ ] Multi-hop scenario-b sub-tab still works (uses fan_out KG
      implicitly per scope-cut; pipeline+bundle threading deferred).

## Console / log spot-checks

- [ ] Server log: `[LAZY_CACHE] {ticker}: building _doc/ corpus` on cold
      first hit per ticker.
- [ ] Server log: `[LAZY_CACHE] {ticker}/{pipeline}: building _graph/
      index ({n} entities, {m} relationships)` on cold first hit per
      (ticker, pipeline).
- [ ] Server log: `graphrag.build.join_confidence_breakdown` structured
      record on each `_graph/` build (telemetry counter per followup #8).

## Backend alive-route guards (unchanged from fixup-20260430)

- [ ] `curl -i 'http://localhost:8080/api/why-this-matters/AAPL?domain=financial'` → non-404.
- [ ] `curl -i -X POST 'http://localhost:8080/api/compare-qa/AAPL' -H 'Content-Type: application/json' -d '{"graphs":[],"domain":"financial"}'` → non-404.
- [ ] `curl -i -X POST 'http://localhost:8080/api/multihop/run' -H 'Content-Type: application/json' -d '{"doc_id":"ZZZZ","scenario_id":"x","slot_pipelines":[null,null,null]}'` → non-404.

---

If every box ticks, the data-layer correctness fix is operator-ready
and demo-testable end-to-end. The dev-report in this directory has the
full deliverable map, deferrals, and ADR-037 cross-repo trail.
