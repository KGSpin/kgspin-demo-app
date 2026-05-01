# PRD-004 v5 Phase 5B — Two-Store Joinable Cache (Data-Layer Correctness)

**From:** Dev team
**To:** CTO
**Date:** 2026-04-30
**Branch (proposed):** `sprint-prd-004-v5-phase-5b-joinable-cache-20260430`
(off `sprint-prd-004-v5-phase-5a-fixup-20260430` HEAD `b499888`)
**Predecessor:** `sprint-prd-004-v5-phase-5a-fixup-20260430` (6 commits;
not yet merged to main).
**Revision:** **v2** — incorporates CTO scope-cut after v1 VP review:
strip provenance/bridge/join_confidence UX, keep data-layer correctness
core. VP-Eng correctness nits from v1 still apply; addressed inline.

---

## TL;DR

Phase 5A's modal Why-tab GraphRAG pane **silently uses a fan_out KG
regardless of the slot's pipeline.** [build_rag_corpus.py:362](../../../scripts/build_rag_corpus.py#L362)
defaults `pipeline="fan_out"` and writes one per-ticker artifact dir;
graph_rag reads from there. Open an `agentic_flash` slot, click Run —
GraphRAG returns answers from a fan_out KG. The fixup-20260430 sprint
fixed the labels; this sprint fixes the data.

5B is a **pure data-layer correctness sprint with zero new UX**. The
demo's modal Why tab keeps its existing dual-pane (Dense RAG | GraphRAG)
+ judge layout. Operator-visible behavior changes only because GraphRAG
now reads from the slot's actual KG.

The redesign:

1. **Two stores, joinable via char offsets**:
   - `_doc/` (per-source-doc, pipeline-independent) — chunks +
     embeddings + BM25 over canonical `source.txt`.
   - `_graph/{graph_key}/` (per-(doc, pipeline, bundle, core_sha)) —
     node/edge embeddings; provenance preserved as build-time metadata.
2. **Layout nested under the lander's existing per-(ticker, date,
   doc_kind) subdir** at `~/.kgspin/corpus/{domain}/{source}/{ticker}/{date}/{doc_kind}/`.
   `tests/fixtures/rag-corpus/` sunset / pinned to small unit fixtures.
3. **Canonical plaintext owned by the lander** — utility in
   `kgspin_interface.text.normalize`, lander persists `source.txt +
   manifest.json`, admin registry tracks `plaintext_sha +
   normalization_version` on `corpus_document`.
4. **`_kg_cache` persists `_graph/{graph_key}/` on Compare-tab Run**
   so modal Run never needs to re-extract.
5. **Lazy build on modal Run with progress UX** (chunking → embedding
   → BM25 SSE stages). Still narratable during demo as "the work the
   system does to make GraphRAG work."
6. **LLM-pipeline join backfill** (sentence-search → chunk_id fallback)
   so LLM-pipeline KGs have offsets for graph_rag's existing
   overlap-join. **Internal field; not surfaced in any UI.**

What's deliberately **NOT** in this sprint (per CTO scope-cut):

- No provenance UI in modal Why tab.
- No `📎 N sources` count badges.
- No `join_confidence` badges.
- No bridge-special UI (no `edge_kind` discriminator at retrieval; no
  separate "Cross-hub bridges (N)" pane; no per-mode policy in graph_rag).
  Bridges flow through as ordinary edges.
- No cross-pipeline dedup (the modal is per-slot, single-pipeline; cross-pipeline
  views are out of scope).
- No demo-script.md for "≥2 bridges per query."

The demo narrative is unchanged from Phase 5A: **Dense RAG vs GraphRAG
answer-quality delta**, judged by an LLM. The proof is in the answers,
not in retrieval-mechanics transparency.

---

## 1. Scope

### In-scope deliverables (D1–D9)

| ID | Title | Sized |
|----|-------|-------|
| D1 | Shared canonical-plaintext utility in `kgspin-interface` | M |
| D2 | Lander writes `source.txt + manifest.json` (SEC + clinical) | M |
| D3 | Admin registry: extend `corpus_document` with `plaintext_sha + normalization_version` (soft-add nullable columns) | M |
| D4 | `_doc/` builder (chunk + embed + BM25) refactored out of `build_rag_corpus.py`; `dense_rag.get_corpus(doc_key)` API | M |
| D5 | `_graph/{graph_key}/` builder (node/edge embed, provenance preserved as metadata); `graph_rag.get_index(graph_key)` API | L |
| D6 | LLM-pipeline join backfill: sentence-search → chunk_id fallback. Internal field only; no UI surface. | M |
| D7 | Persist `_kg_cache` to `_graph/{graph_key}/` on Compare-tab Run | M |
| D8 | Lazy build on modal Run with SSE progress events + UI ("Chunking… Embedding 312/487… BM25…") | L |
| D9 | Migration script + sunset of `tests/fixtures/rag-corpus/` (or trim to pinned unit fixtures) | M |

### Out of scope (deferred)

- **Bridge per-mode policy in graph_rag** — bridges flow through as
  ordinary edges; storage-side `kind: "bridge"` metadata preserved
  but no retrieval/UI behavior. (Future sprint when a unified-graph
  data layer makes bridges meaningful.)
- **Cross-pipeline dedup** at retrieval time — modal is per-slot,
  single-pipeline.
- **Provenance UX** in modal Why tab — provenance lives in graph
  storage as build-time metadata; existing graph-explorer features
  surface it elsewhere if needed.
- **Cross-slot retrieval** (Compare tab unified view).
- **Live re-embedding** on `NORMALIZATION_VERSION` bump — manual D9 migration.
- **Pre-existing test failures** (test_scenarios, test_multihop_endpoint,
  test_compare_qa_*, registry-http) — separate cleanup sprint.

---

## 2. Architecture

### 2.1 Cache layout

```
~/.kgspin/corpus/financial/sec_edgar/JNJ/2025-02-14/10-K/
├── raw.html                             # lander, today
├── source.txt                           # NEW (D2) — canonical plaintext
├── manifest.json                        # NEW (D2) — source_sha + plaintext_sha
│                                        #          + normalization_version + source
├── _doc/                                # NEW (D4) — pipeline-independent dense corpus
│   ├── chunks.json                      #   [{id, text, char_offset_start, char_offset_end}]
│   ├── chunk_embeddings.npy             #   (n_chunks, 384) float32, L2-normalized
│   ├── bm25_index.pkl
│   └── manifest.json                    #   doc_key fingerprint
└── _graph/                              # NEW (D5) — per-(pipeline, bundle, core_sha)
    ├── fan_out__financial-default__core-abc/
    │   ├── graph_nodes.json             #   [{id, canonical_name, entity_type, evidence: [...]}]
    │   ├── graph_edges.json             #   [{id, subj_canonical, predicate, obj_canonical, kind, provenance: [...]}]
    │   ├── graph_node_embeddings.npy
    │   ├── graph_edge_embeddings.npy
    │   └── manifest.json                #   graph_key fingerprint
    ├── agentic_flash__financial-default__core-abc/
    ├── agentic_analyst__financial-default__core-abc/
    ├── discovery_rapid__financial-default__core-abc/
    └── discovery_deep__financial-default__core-abc/
```

Clinical mirrors the layout under `~/.kgspin/corpus/clinical/clinicaltrials_gov/{NCT}/{YYYY-MM-DD}/trial/`.

### 2.2 Cache keys (VP-Eng v1 nits #1, #2 addressed)

- `doc_key = sha256(domain || "\0" || source || "\0" || ticker || "\0" || source_sha || "\0" || normalization_version)`
  - **NEW (per VP-Eng v1 nit #2)**: `source` is in the SHA, not just the path. Prevents same-plaintext / different-fetcher collisions.
- `graph_key = sha256(doc_key || "\0" || pipeline || "\0" || bundle || "\0" || bundle_version || "\0" || kgspin_core_sha)`
  - **NEW (per VP-Eng v1 nit #1)**: `bundle_version` (content-hash of bundle YAML) is in the SHA. Prevents stale-index reuse when a bundle's predicate set or canonicalizer changes without a rename.
- `subdir_name(graph_key) = f"{pipeline}__{bundle}__core-{kgspin_core_sha[:7]}"` —
  human-readable; collision-resistance lives on the manifest's full SHA, not the path.

### 2.3 Stored provenance shape (graph_edges.json row)

Provenance is **build-time metadata** — preserved on disk for graph-explorer
features and future cross-pipeline work. Not surfaced in the modal Why tab.

```json
{
  "id": "rel-0042",
  "subj_canonical": "Apple Inc.",
  "predicate": "has_executive",
  "obj_canonical": "Tim Cook",
  "kind": "intra | bridge | registry",
  "provenance": [
    {
      "evidence_kind": "intra | bridge | registry",
      "source_doc_key": "<sha>",
      "pipeline": "agentic_flash",
      "sentence_text": "Tim Cook serves as Chief Executive Officer of Apple Inc.",
      "char_span": [12345, 12401],
      "join_confidence": "sentence | chunk | none",
      "hub_ref": null
    }
  ]
}
```

### 2.4 Runtime flow (modal Why-tab Run)

1. **Slot context** read from `expandedSlot`, `slotState[i].pipeline`,
   `slot.bundle`, ticker.
2. **Resolve `doc_key`** from `(domain, source, ticker)` → look up the latest
   dated subdir under `~/.kgspin/corpus/{domain}/{source}/{ticker}/`. Read
   `manifest.json`. If `_doc/` missing → SSE `event: build_stage` →
   build chunk + embed + BM25 over `source.txt` (~15-30s cold). Write + cache.
3. **Resolve `graph_key`** from `(doc_key, slot.pipeline, slot.bundle,
   bundle_version, core_sha)`. If `_graph/{graph_key}/` missing on disk:
   - Look up `_kg_cache[ticker]` for the in-memory KG (already there from
     Compare-tab Run; D7's persist hook should have written it on last Run).
   - SSE `event: build_stage` → embed nodes + edges → write to disk (~5-10s).
   - **If KG isn't in `_kg_cache` either**: SSE `event: build_stage` with
     prompt **"This slot hasn't been Run on Compare yet. Run it there first
     (5-10 min for LLM extraction) — or pick a slot that's already loaded."**
     Bail. Don't trigger LLM extraction inside a modal Run.
4. **Retrieve** `(doc_corpus, graph_index)` pair → run scenario A or B as today.
5. **Modal UI** — unchanged from Phase 5A. Two answer panes + judge verdict.
   Existing `<details>Retrieved chunks</details>` debug-collapsible stays.

---

## 3. Pre-EXECUTE gate (Spike — half day)

Verify the assumption Q1=(b) commits us to: **discovery_rapid and
discovery_deep emit `Evidence.character_span` set (not None) into a
plaintext that aligns with `_strip_html_to_text`'s output.**

**Numeric pass criterion (per VP-Eng v1 nit #6):**

- Build a `_doc/` for JNJ 10-K (latest dated subdir).
- For each pipeline (fan_out, agentic_flash, agentic_analyst,
  discovery_rapid, discovery_deep), run extraction over the same
  `source.txt`, then attempt offset resolution for every entity / edge
  evidence using D6's planned algorithm (sentence-search → chunk_id
  fallback).
- **Gate**: ≥90% of evidence rows resolve to `join_confidence ∈ {sentence,
  chunk}` for each pipeline. ≥10% `join_confidence == "none"` from any
  pipeline → that pipeline is downgraded out of Q1=(b) and added to
  the deferral list.

Spike output: 1-page memo at `docs/sprints/sprint-prd-004-v5-phase-5b-joinable-cache-20260430/spike-evidence-offsets.md`
with the per-pipeline numbers. CTO sign-off before EXECUTE if any
pipeline downgrades.

---

## 4. Commit sequence (proposed)

| # | Title | Deliverables | Sized |
|---|-------|--------------|-------|
| 0 | Spike gate: per-pipeline evidence-offset resolution rates | (gate) | ½ day |
| 1 | `kgspin_interface.text.normalize` utility + golden-file tests | D1 | ½ day |
| 2 | Lander writes `source.txt + manifest.json` (SEC + clinical); end-to-end test | D2 | 1 day |
| 3 | Admin registry: soft-add `plaintext_sha + normalization_version` on `corpus_document` | D3 | ½ day |
| 4 | `_doc/` builder + `dense_rag.get_corpus(doc_key)` API; idempotency test | D4 | 1 day |
| 5 | `_graph/{graph_key}/` builder + `graph_rag.get_index(graph_key)` API; round-trip test | D5 | 1.5 days |
| 6 | LLM-pipeline join backfill (sentence-search → chunk_id → internal `join_confidence` field) | D6 | 1 day |
| 7 | `_kg_cache` → `_graph/{graph_key}/` persist hook on Compare-tab Run | D7 | ½ day |
| 8 | Lazy build on modal Run with SSE progress events + UI | D8 | 1.5 days |
| 9 | Migration script (D9 first) + dev-report + demo-output | D9 + docs | 1 day |
| 10 | Sunset `tests/fixtures/rag-corpus/`; pin unit fixtures (D9 second per VP-Eng v1 nit #7) | D9 (cont.) | ½ day |

**Total estimated**: ~9 dev-days across 10 commits + 1 spike.
**Calendar**: ~1.5 weeks with normal interruptions / review cycles.

**Ordering note (per VP-Eng v1 nit #7)**: Migration script (D9 first
half) lands and is verified green BEFORE fixture sunset (D9 second
half) — fixture is the fallback if migration has a post-merge bug.

---

## 5. Tests

### 5.1 New / updated coverage

- **D1**: golden-file tests for `canonical_plaintext_from_html` and
  `canonical_plaintext_from_clinical_json` with byte-stable outputs
  + `NORMALIZATION_VERSION` round-trip.
- **D2**: integration — fetch a known SEC HTML; assert `source.txt +
  manifest.json` present, `plaintext_sha` matches re-computation.
- **D3**: registry-write test — `corpus_document` row carries
  `plaintext_sha + normalization_version`. Backwards-compat read of
  pre-extension rows returns `None` for those fields without erroring.
  **Invariant**: readers MUST tolerate `plaintext_sha is None`.
- **D4**: idempotency — second build is a no-op via manifest fingerprint
  match; `--force` rebuilds.
- **D5**: round-trip an in-memory KG → write → read → verify provenance
  preserved (no information loss).
- **D6**: parametrized over fan_out + agentic_flash evidence; assert
  `join_confidence == "sentence"` for verbatim matches, `"chunk"` for
  paraphrased, `"none"` for missing-chunk-id. **Field is internal —
  asserted on the data, not on any UI surface.**
- **D7**: TestClient — POST to `/api/start_comparison` on a slot;
  assert `_graph/{graph_key}/` exists on disk afterward.
- **D8**: TestClient — POST to `/api/scenario-a/run` with no `_doc/`
  on disk; assert SSE `event: build_stage` arrives with stage labels
  in order (chunking → embedding → bm25), then `event: result`.
- **D9 (migration)**: dry-run on a fixture lander tree; assert
  idempotent + safe (no overwrites without `--force`); explicit
  migration-states table:

  | State | Detected by | Action |
  |-------|-------------|--------|
  | Fresh — no `_doc/` no `_graph/` | absence | Build both |
  | Manifest match, both present | manifest SHA == recompute | No-op |
  | Plaintext SHA mismatch | manifest.plaintext_sha != recompute | Rebuild + log; `--force` skips check |
  | Partial-write recovery (half-written `.npy`) | numpy load raises | Delete dir; rebuild |
  | Orphan `_graph/{graph_key}/` (no installed core matches) | manifest.kgspin_core_sha not in current env | Skip with WARN; flag in dry-run output |
  | Pre-extension lander tree (no `source.txt`) | absence | Generate via D1 utility; write manifest |

### 5.2 Pre-existing failures inherited from 5A fixup

Out of scope. Separate cleanup sprint.

---

## 6. Risks + mitigations

| # | Risk | Likelihood | Mitigation |
|---|------|------------|------------|
| R1 | discovery_rapid/discovery_deep emit chunk-relative or no spans | M | Spike (commit 0) gates EXECUTE with numeric ≥90% pass criterion. |
| R2 | LLM-pipeline sentence-text fuzzy match too lossy | M | D6 stores `join_confidence` internally; future sprint can rerank. Demo doesn't surface; quality only matters for graph_rag's overlap-join correctness. |
| R3 | Lander cache-dir convention diverges between SEC + clinical | L | D2 unit-tests both layouts; integration pass exercises both end-to-end. |
| R4 | Migration (D9) bricks an existing dev's `~/.kgspin/corpus/` | L | Migration is opt-in; default no-op; states-table (§5.1 D9) covers all known cases; backup convention. |
| R5 | First-run model download (`all-MiniLM-L6-v2`, ~90MB) blocks demo | M | **Pre-warm in demo-day setup script** (per VP-Prod v1). D8's progress UI is the runtime fallback; setup-script gate is primary. |
| R6 | Concurrent modal Runs race on the same `_doc/` build | L | Per-doc-key **`flock(2)`-based file lock** as authoritative (per VP-Eng v1 nit #4); asyncio lock is in-process optimization only. Second waiter subscribes to first's progress SSE stream (fan-out, not silent block). |
| R7 | `tests/fixtures/rag-corpus/` removal breaks tests that grep for it | L | D9 audits + migrates affected tests; CI green before fixture sunset. |
| R8 | `kgspin_core_sha` doesn't fully pin extraction behavior (transitive deps shift) | L | manifest's full SHA is canonical; subdir name uses `[:7]` for readability; on transitive-dep change without `kgspin_core_sha` change, `--force` rebuild required. Documented in migration-guide. |
| R9 | D6's `join_confidence == "none"` evidence participates in retrieval | L | Default policy: include but no rerank. Defer reranking to a future sprint. Internal field, no UI exposure. |

---

## 7. Open questions for VP review

### VP-Eng (correctness)

1. Cache-key composition (§2.2) — `doc_key` adds `source`; `graph_key`
   adds `bundle_version`. Confirm both compositions are sufficient or
   flag any missed dimension.
2. D9 migration-states table (§5.1) — comprehensive, or any state class
   missing?
3. R6 concurrency model (`flock(2)` authoritative + asyncio in-process
   + SSE fan-out for waiters) — robust enough for `pytest-parallel` +
   multi-`uvicorn`-worker?

### VP-Prod (sanity check)

1. Sprint ships **zero new UX**. Modal Why tab is unchanged from Phase
   5A's existing dual-pane layout. Operator-visible behavior changes
   only because GraphRAG now reads from the slot's actual KG. Confirm
   this scope cut is the right move.
2. D8's progress UI ("Chunking… Embedding 312/487 chunks… Building
   BM25…") is the only new operator-visible affordance. Acceptable as
   a build-narration moment during demo, or do you want it suppressed
   (silent build with a single "Building corpus…" spinner)?

### CTO

1. The sprint takes ~1.5 weeks (was ~3 in v1). Worth executing as a
   standalone sprint, or fold into a larger initiative?
2. Sunset of `tests/fixtures/rag-corpus/` (D9 second half) — confirm
   acceptable, or pin a stable seed for some unit tests?

---

## 8. Documentation deliverables (commit 9)

- **dev-report.md** — deliverable map, top surprising findings, deferrals.
- **demo-output.md** — manual UI smoke checklist (modal Run cold vs warm cache).
- **architecture.md** — cache-layout reference + key formulas + storage
  schema (canonical doc for future contributors).
- **migration-guide.md** — step-by-step for existing devs to run the
  D9 migration on their `~/.kgspin/corpus/` tree.

---

## 9. Out-of-scope follow-up sprints

- **5C: Unified per-pipeline graph view** — when the data layer matures
  enough to merge per-doc graphs into one queryable surface per pipeline.
  Bridges become invisible; provenance becomes meaningful.
- **5D: `NORMALIZATION_VERSION` bump live-migration** — automatic rebuild
  when the lander updates to a newer canonicalizer.
- **5E: Provenance-aware retrieval ranking** — boost edges with N>1
  sources; downweight `join_confidence == "none"`. (Internal field
  becomes useful when there's a reranking pipeline to consume it.)
- **5F: Cross-slot Compare-tab retrieval** — exposes retrieval across
  multiple slots simultaneously.
- **Pre-existing test cleanup** — separate triage sprint.

---

**Awaiting VP-Eng (correctness) + VP-Prod (sanity check) + CTO review
before EXECUTE. v2 is responsive to v1's CTO scope-cut and v1's VP-Eng
correctness nits.**
