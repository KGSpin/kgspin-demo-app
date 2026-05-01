# PRD-004 v5 Phase 5B — Scope (v2)

**Date:** 2026-04-30
**Status:** Pre-VP-review (v2). Reconciled with v1 CTO scope-cut +
VP-Eng correctness nits.

## In-scope

- **Two-store joinable cache**: `_doc/` (per-source-doc, pipeline-independent)
  + `_graph/{graph_key}/` (per-(doc, pipeline, bundle, core_sha)).
- **Layout** nested under lander's existing per-(ticker, date, doc_kind) subdir.
- **Canonical plaintext**: shared `kgspin_interface.text.normalize` utility
  + lander persists `source.txt + manifest.json` at fetch time. Admin
  registry tracks `plaintext_sha + normalization_version` on
  `corpus_document` (soft-add nullable columns; readers tolerate
  pre-extension `None`).
- **Join contract**: char-offset overlap between dense chunks and graph
  evidence. LLM-pipeline backfill via sentence-search → chunk_id fallback;
  `join_confidence` stored as **internal field**, not surfaced.
- **`_kg_cache` persistence** to `_graph/{graph_key}/` after Compare-tab Run.
- **Lazy build on modal Run** with SSE progress events + UI ("Chunking…
  Embedding x/N… BM25…").
- **Coverage for all 5 demo pipelines**: fan_out, agentic_flash,
  agentic_analyst, discovery_rapid, discovery_deep. **Gated on spike
  numeric criterion** (≥90% evidence resolution per pipeline).
- **Migration script** + sunset of `tests/fixtures/rag-corpus/` (or
  trim to pinned unit fixtures). Migration ships and goes green BEFORE
  fixture sunset.
- **Cache-key composition**: `doc_key` includes `source` (e.g. `sec_edgar`);
  `graph_key` includes `bundle_version` (content-hash of bundle YAML).

## Deliberately out of scope (zero new UX in modal Why tab)

- **Provenance UX** in modal Why tab — no `📎 N sources` count badges,
  no per-evidence accordion expand-on-click, no per-pipeline labels in
  retrieved-context. Provenance lives in graph storage as build-time
  metadata for graph-explorer features and future cross-pipeline work;
  modal Why tab continues to show only Dense answer / GraphRAG answer /
  judge verdict, exactly as Phase 5A landed.
- **`join_confidence` UI surface** — internal field only.
- **Bridge per-mode policy** in graph_rag — bridges flow through as
  ordinary edges. Storage-side `kind: "bridge"` metadata preserved
  (PRD-056 wrote it; we don't strip it; we don't act on it). No
  separate "Cross-hub bridges (N)" pane, no `edge_kind` filter, no
  modal footer.
- **Cross-pipeline dedup** at retrieval — modal is per-slot, single-pipeline.
- **Cross-slot Compare-tab retrieval** view.
- **Live re-embedding** on `NORMALIZATION_VERSION` bump (manual D9 migration).
- **Pre-existing test failures** (test_scenarios, test_multihop_endpoint,
  test_compare_qa_*, registry-http) — separate cleanup sprint.

## Locked Socratic answers (v2)

- **Q1 (pipeline scope)**: all 5 demo pipelines from day 1, gated on
  pre-EXECUTE spike confirming ≥90% evidence resolution per pipeline.
- **Q2 (LLM join strategy)**: sentence-search → chunk_id fallback;
  `join_confidence` stored internally, **not** surfaced in UI.
- **Q3 (canonicalizer owner)**: a+b hybrid — shared utility in
  `kgspin_interface.text.normalize`, lander persists `source.txt +
  manifest.json`. Admin registers `plaintext_sha + normalization_version`
  on `corpus_document` (soft-add nullable; derived caches NOT registered).
- **Q4 (build trigger)**: lazy on modal Run with SSE progress events +
  UI. Demo-narratable as "the work the system does to make GraphRAG work."
- **Q5 (cache root)**: nest under lander's per-(ticker, date, doc_kind)
  subdir at `~/.kgspin/corpus/{domain}/{source}/{ticker}/{date}/{doc_kind}/`.
- **Q6 (bridge interaction)**: storage preserved (`kind: "bridge"`),
  retrieval treats as ordinary edges, **no UX**. Per CTO clarification:
  the demo treats "the graph" as one continuous surface; bridge
  fragmentation is an artifact of how it's built today, not how it
  should be queried.

## Success criteria

- **Open an `agentic_flash` slot's modal → click Run → GraphRAG retrieves
  from the slot's actual `agentic_flash` KG** (not a fan_out fallback).
  This is the headline correctness fix.
- First-time Run on a fresh ticker shows progress UX ("Chunking…
  Embedding 312/487 chunks…") within 1s of click; completes in <30s
  cold (assuming KG is in `_kg_cache` from a prior Compare-tab Run);
  subsequent runs <1s warm.
- Modal Why tab UI is **identical to Phase 5A's existing layout** — two
  answer panes + judge verdict. No new affordances beyond D8's progress UI.
- Phase 5A fixup-20260430 alive-route guards still pass.
- All 5 pipelines exercised by integration tests with the new two-store
  cache; pre-existing failures remain pre-existing (not regressed).
- Migration script (D9) runs idempotent on a populated dev `~/.kgspin/corpus/`
  tree without data loss; dry-run output is comprehensive (per migration-states
  table).

## CTO scope-cut summary (vs v1)

Stripped from v1 → v2:

- D9 (dedup-by-canonical-triple at retrieval) — out.
- D10 (modal Why-tab provenance UI, accordion, badges) — out.
- D12 (bridge per-mode policy in graph_rag) — out.
- VP-Prod's `demo-script.md` (≥2 bridges per query) — out.
- "Side-by-side Dense RAG can't reach" pitch language — out (factually
  wrong framing per CTO; both methods can semantically cross graphs).
- 14 deliverables → 9. ~3 weeks → ~1.5 weeks.

What stays from v1:

- All cache-layer correctness work (D1–D8).
- All VP-Eng v1 correctness nits applied (cache-key compositions,
  D9 migration-states table, R6 concurrency model, spike numeric
  criterion, D9 ordering).
- Pipeline scope (Q1=b), join strategy (Q2=c, internal-only).
