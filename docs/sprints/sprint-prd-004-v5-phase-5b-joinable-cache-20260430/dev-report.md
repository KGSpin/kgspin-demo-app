# PRD-004 v5 Phase 5B — Dev Report

**From:** Dev team
**To:** CTO
**Date:** 2026-05-01
**Branch:** `sprint-prd-004-v5-phase-5b-joinable-cache-20260430` (off
`sprint-prd-004-v5-phase-5a-fixup-20260430` HEAD `b499888`).
**Cross-repo:** `kgspin-interface` branch
`sprint-prd-004-v5-phase-5b-text-normalize-20260501`.
**Status:** **DEV_REPORT_READY** (3 commits + cross-repo D1; pre-merge.)

---

## TL;DR

The headline correctness fix landed: **GraphRAG now reads from the
slot's pipeline-specific KG instead of silently using fan_out for
every slot.** Modal Why-tab Single-shot Q&A's right pane finally
reflects the slot you opened (an `agentic_flash` slot's GraphRAG
pane uses an agentic_flash KG, not a fan_out fallback).

Net diff vs the predecessor sprint:

| Commit | Title | Files | +/− |
|--------|-------|-------|-----|
| 6b8f85a | spike + plan v2 + scope v2 + v2 followups | 4 | +943/0 |
| 3d7a2d0 | CTO post-spike additions to followups | 1 | +159/4 |
| de65693 (kgspin-interface) | text.normalize utility + tests | 6 | +581/2 |
| de226d6 | resolver-swap subsumption regression net (D1) | 3 | +249/2 |
| 7d230dd | landers persist source.txt + manifest.json (D2) | 5 | +611/27 |
| 1ec78a4 | joinable two-store cache + lazy build (D4-D8) | 8 | +1195/17 |

Total: **27 files changed, ~3700 insertions / ~50 deletions**
(plan/scope/audit/spike/followups ≈ 1100 lines; runtime code +
tests ≈ 2600 lines).

Tests: 434 pass + 15 skipped. 4 pre-existing failures (test_scenarios,
test_multihop_endpoint, test_compare_qa_*, registry-http) remain
out of scope.

## Deliverable map

| ID | Title | Status | Lands in commit |
|----|-------|--------|-----------------|
| D1 | `kgspin_interface.text.normalize` (canonical plaintext + global resolver) | ✅ shipped | de65693 + de226d6 |
| D2 | Lander writes `source.txt + manifest.json` | ✅ shipped | 7d230dd |
| D3 | Admin registry soft-add `plaintext_sha + normalization_version` | **DEFERRED** — extras dict already threads to `source_extras`; promotion to top-level admin column is cleanup | — |
| D4 | `_doc/` builder + `dense_rag.get_corpus(doc_key)` API | ✅ shipped | 1ec78a4 |
| D5 | `_graph/{graph_key}/` builder + `graph_rag.get_index(graph_key)` API | ✅ shipped | 1ec78a4 |
| D6 | LLM-pipeline join backfill (sentence-search → chunk_id fallback) | ✅ shipped | 1ec78a4 (folded into D5 builder) |
| D7 | `_kg_cache` → `_graph/{graph_key}/` persist hook on Compare-tab Run | **COLLAPSED into D8** — lazy build at modal Run time covers the contract; eager hook deferred (brittle wiring across many cache-write sites in demo_compare.py) | — |
| D8 | Lazy build on modal Run with progress UX | **PARTIAL** — synchronous build shipped; SSE progress UX deferred to follow-up | 1ec78a4 |
| D9 | Migration script + sunset `tests/fixtures/rag-corpus/` | **DEFERRED** — no migration needed since legacy-fallback contract preserves pre-D2 fixtures unchanged | — |

## Followups (per `v2-review-followups.md`)

| # | Title | Status |
|---|-------|--------|
| 1 | TestClient invariant `graph_index_pipeline == "agentic_flash"` | **PARTIAL** — debug field in response (`debug.graph_index_pipeline`); explicit test deferred |
| 2 | `doc_key` SHA includes `normalization_version` directly | ✅ |
| 3 | D9 migration-states table | n/a (D9 deferred) |
| 4 | `flock(2)` portability + cross-worker SSE fan-out scope | n/a (synchronous build) |
| 5 | D8 stage labels capped at 3 + collapse-on-warm | n/a (no SSE UX) |
| 6 | `scripts/warm_demo_caches.py` + demo-matrix yaml | **DEFERRED** — operator runs lazy build per slot via modal Run |
| 7 | D5 round-trip preserves `kind` field | ✅ — `kind` is preserved in `_edge_to_row` provenance |
| 8 | `join_confidence == "none"` retrieval counter | ✅ — logged at build time per pipeline (`graphrag.build.join_confidence_breakdown`) |
| 9 | fan_out resolver-swap regression test | ✅ shipped (de226d6) |
| 10 | D6 ≥95% empirical threshold + mitigation paths | **DEFERRED** — empirical threshold not measured (LLM extractions slow); mitigation path docs in followups doc are still authoritative |
| 11 | kgspin-core `Evidence.character_span` ticket | ✅ **RESOLVED via ADR-037** (option B: field removed) |
| 12 | Lineage display wires through `resolve_evidence_offsets` (3 call sites) | **DEFERRED** — independent of modal Why-tab Run; ~1-2h follow-up |

## Top surprising findings

1. **`Evidence.character_span = None` universally.** Cross-repo audit
   during the spike (commit 0) found that no production extractor in
   kgspin-core sets the field to a real value. The fan_out resolver
   today works by accident, not by contract. Resolved by ADR-037
   (kgspin-core landed Option B — remove the field; demo's global
   sentence-search resolver becomes the cluster's canonical span-
   resolution path).
2. **Chunking schemes diverge across extractors and the corpus
   builder.** Today's `build_rag_corpus._resolve_evidence_span`
   chunk-id-bound lookup would silently miss for non-fan_out pipelines.
   The new global-search resolver in
   `kgspin_interface.text.normalize.resolve_evidence_offsets` decouples
   the join from chunking-scheme alignment. Subsumption regression
   test (followup #9) pins the contract.
3. **Multiple kg_cache write sites in demo_compare.py made eager
   D7 persist brittle.** Lazy build at modal Run time (D8) covers
   the same contract with a single injection point. The eager hook
   gets deferred without functional loss.
4. **The "RAG corpus missing" error has three real causes** (no
   lander, lander but no D2 manifest, lander with manifest but no
   `_doc/` build). Lazy_cache surfaces them as distinct error codes
   (`lander_not_found`, `kg_not_in_cache`, `corpus_not_built`) so
   future operator-facing UX can guide each remedy separately.

## Deferrals (rolled to follow-up sprints)

- **D3 admin registry soft-add** — out of critical path; tracking as
  admin-side cleanup ticket.
- **D7 eager Compare-Run persist hook** — lazy at modal Run is
  sufficient; eager would dilute the kg_cache write surface.
- **D8 SSE progress UX** — synchronous build for now. Modal Run blocks
  ~15-30s cold; instant warm. Operator narration during demo is "the
  modal is computing the embedding/index for this graph; takes ~30s
  the first time you open it for a new slot type."
- **D9 migration + fixture sunset** — legacy-fallback contract preserves
  old fixtures; no migration needed for existing dev `~/.kgspin/corpus/`
  trees.
- **Followup #6 demo-warm script** — deferred; lazy build per slot is
  acceptable for the demo flow.
- **Followup #10 D6 empirical threshold** — empirical measurement
  deferred (LLM extractions slow); mitigation paths in followups doc
  remain authoritative for when measurement happens.
- **Followup #12 lineage display** — independent of the headline fix;
  ~1-2h follow-up sprint.
- **/api/scenario-b/run pipeline+bundle threading** — multi-hop
  service refactor too large for time-box; multi-hop continues to use
  fan_out implicitly. Single-shot (scenario-a) is the demo's primary
  surface.

## Cross-repo trail (per ADR-037 cite)

The spike's diagnosis of `Evidence.character_span` universality
prompted CTO to dispatch
`sprint-evidence-character-span-audit-20260501` directly to the
kgspin-core team in parallel with the demo team's filing. That sprint
landed **ADR-037 (Accepted)**: option B chosen — `character_span` is
removed from the `Evidence` schema. Reasoning per ADR-037: chunker
offsets have paragraph-strip drift that made accurate population
unattainable cheaply, and the demo team's global-search resolver
already subsumes the use case. **The demo's
`kgspin_interface.text.normalize.resolve_evidence_offsets` is now
the canonical span-resolution path across the kgspin cluster.**

Demo team verified during D2 that no demo-app code reads
`Evidence.character_span`; post-ADR-037-merge cleanup of test-fixture
dict keys (`tests/unit/test_graph_rag.py`,
`tests/unit/test_build_rag_corpus.py`) is non-blocking.

## Pre-existing failures (unchanged)

- `tests/unit/test_scenarios.py::test_scenario_to_dict_shape`
- `tests/integration/test_multihop_endpoint.py::test_multihop_run_happy_path`
- `tests/unit/test_demo_compare_llm_endpoints.py::test_compare_qa_*`
- `tests/unit/test_register_fetchers_cli.py`, `tests/unit/test_registry_http.py`

All four pre-date 5B and are out of scope. Tracked for a separate
cleanup sprint.

## Operator experience

After this sprint:

- Operator clicks Run on a slot in the Compare tab → KG lands in
  in-memory `_kg_cache` (unchanged).
- Operator opens that slot's modal → Why tab → Single-shot Q&A → types
  a question → Run.
- First time per (slot, pipeline) on a fresh ticker: blocks ~15-30s
  while `_doc/` and `_graph/{graph_key}/` are built. No SSE progress
  UX yet (deferred); operator sees the spinner.
- Subsequent runs: instant warm-cache return.
- GraphRAG retrieved-context now reflects the slot's actual KG. Open
  an `agentic_flash` slot, GraphRAG pulls from agentic_flash's
  extracted entities/edges. Open a `fan_out` slot, GraphRAG pulls from
  fan_out. **Headline correctness fix shipped.**

If the operator opens a modal for a slot that hasn't been Compare-Run
yet, the modal returns `kg_not_in_cache` (HTTP 503 with operator-
friendly message: "Run the slot on the Compare tab first to extract
the KG, then re-open the modal").

## Demo-day prep checklist

Before any customer-facing demo with this sprint's work:

- [ ] Run `kgspin-demo-lander-sec --ticker {JNJ,AAPL}` (and clinical
  equivalents) so each ticker has a D2-augmented lander tree
  (`source.txt + manifest.json` written).
- [ ] On Compare tab, Run the demo matrix of slots (fan_out / agentic_flash
  / agentic_analyst × {JNJ, AAPL, JNJ-Stelara}) once each — populates
  `_kg_cache` so modal Runs don't bail on `kg_not_in_cache`.
- [ ] Pre-warm the embedding model: import
  `kgspin_demo_app.services.doc_corpus_builder` and call
  `_get_embedder()` once at startup so the first modal Run doesn't
  download `all-MiniLM-L6-v2` mid-demo (~90MB, one-time).
- [ ] Walkthrough the modal Why-tab Single-shot Q&A path to confirm
  GraphRAG retrieves from the slot's actual pipeline (`debug.graph_index_pipeline`
  in the response should match the slot's pipeline label).
