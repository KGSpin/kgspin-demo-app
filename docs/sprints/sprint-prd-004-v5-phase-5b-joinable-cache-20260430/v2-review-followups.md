# v2 Review Follow-ups (VP-Eng + VP-Prod)

Both VP reviews **APPROVED v2 WITH NITS**. No blockers. Items below are
tracked per-commit during EXECUTE (not folded into plan v3).

Legend:
- **EXECUTE-blocker**: must land in or before its named commit.
- **in-flight**: tracked per-commit; can land any time during the sprint.
- **post-sprint**: tracked but explicitly not in 5B; rolls to a follow-up.

---

## 1. Headline correctness fix has no direct test (VP-Eng top concern)

**Owner**: dev. **Lands in**: commit 5 (D5 — `_graph/{graph_key}/` builder).
**Status**: EXECUTE-blocker for commit 5.

The entire reason 5B exists — "GraphRAG reads from the slot's actual KG,
not a fan_out fallback" — is asserted only in §6 success-criteria prose.

**Action**: Add `tests/integration/test_graphrag_uses_slot_kg.py`:

```python
def test_graphrag_loads_slot_pipeline_kg(app_client_with_warm_kg_cache):
    """Open an agentic_flash slot's modal, click Run, assert the loaded
    graph_key manifest's pipeline == 'agentic_flash' (not 'fan_out')."""
    client = app_client_with_warm_kg_cache  # fixture pre-populates kg_cache
    resp = client.post(
        "/api/scenario-a/run",
        json={"question": "Who is the CEO?", "ticker": "JNJ", "mode": "A2",
              "slot_pipeline": "agentic_flash", "slot_bundle": "financial-default"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    # Headline assertion — the loaded graph_key MUST resolve to agentic_flash.
    assert payload["debug"]["graph_index_pipeline"] == "agentic_flash", (
        f"GraphRAG loaded {payload['debug']['graph_index_pipeline']!r} KG, "
        f"not the slot's agentic_flash. Silent fallback regression."
    )
```

Add a `debug.graph_index_pipeline` field to the `/api/scenario-a/run`
response (gated behind a `?debug=1` or always-emitted internal field
that tests can read). This is the only operator-invisible assertion
that proves the correctness fix.

---

## 2. `doc_key` should include `normalization_version` directly

**Owner**: dev. **Lands in**: commit 4 (D4 — `_doc/` builder).
**Status**: EXECUTE-blocker for commit 4.

Plan §2.2 had `doc_key` bind `normalization_version` transitively via
`source_sha`-of-plaintext. VP-Eng caught: a normalizer change without
a `source.txt` rewrite would alias.

**Action**: Update §2.2's `doc_key` formula in plan + implementation:

```
doc_key = sha256(
  domain || "\0" ||
  source || "\0" ||
  ticker || "\0" ||
  source_sha || "\0" ||
  normalization_version    # NEW — explicit, not transitive
)
```

Manifest also persists `normalization_version` separately so migration
can detect skew without recomputing.

---

## 3. Migration state missing: manifest-OK but files-corrupt post-rsync

**Owner**: dev. **Lands in**: commit 9 (D9 — migration script).
**Status**: EXECUTE-blocker for commit 9.

Plan §5.1's D9 migration-states table covers partial-write recovery
(crashed mid-build) but not the rsync-corruption class: manifest claims
good, files don't load, no in-flight build to detect.

**Action**: Add row to the migration-states table:

| State | Detected by | Action |
|-------|-------------|--------|
| Manifest OK but artifact files truncated/corrupt | manifest SHA matches but `numpy.load`/`json.loads` raises, OR file size doesn't match recorded `bytes` field | Delete dir contents (keep manifest backup); rebuild + WARN; emit dry-run "REBUILD: $path (post-rsync corruption)" |

Manifest persistence MUST record per-file `bytes + content_sha` so the
migration can detect this without loading the artifact.

---

## 4. `flock(2)` portability + cross-worker SSE fan-out

**Owner**: dev. **Lands in**: commit 8 (D8 — lazy build on modal Run).
**Status**: EXECUTE-blocker for commit 8.

Plan R6 picked `flock(2)` as authoritative. VP-Eng caught: `flock` is
advisory + macOS/Linux-only; Windows devs are out. Also: SSE fan-out
across multiple uvicorn workers needs a cross-process bus (Redis pubsub)
or in-process scope only.

**Action**: Two decisions to make + document during commit 8:

A. **Portability scope** — declare the demo's supported platforms
   explicitly in `architecture.md`: macOS + Linux (the demo's actual
   audience). Windows is not supported for the runtime cache;
   `tests/fixtures/rag-corpus/` pinned fixtures cover Windows
   contributors. Document.

B. **SSE fan-out scope** — choose:
   - **(B1) In-process-only fan-out**: lock holder's progress events
     reach in-process waiters via asyncio queue; cross-worker waiters
     poll the lock file (no live progress, just "still building" status).
     **Recommended for 5B** — demo is single-worker uvicorn.
   - **(B2) Redis pubsub**: progress events broadcast to all workers.
     Adds a Redis dependency. Defer to 5C if/when multi-worker.

Plan v2 implicitly assumed (B2); flip to (B1) and document the
single-worker constraint in `architecture.md` deployment section.

---

## 5. Cap D8 stage labels at 3; collapse on warm

**Owner**: dev. **Lands in**: commit 8 (D8 — lazy build on modal Run).
**Status**: in-flight.

VP-Prod nit: "Embedding 312/487" is credibility on cold first-run, but
exposing BM25 as a separate stage to non-technical operators reads as
overhead.

**Action**:

- Cold first-run: 3 SSE stage labels max — **Chunking**, **Embedding N/M**,
  **Indexing** (BM25 folded under "Indexing").
- Warm runs (<1s): no progress UI; status text changes to "Done" immediately.
- D8's SSE event schema: `{stage: "chunking" | "embedding" | "indexing",
  current?: int, total?: int, done: bool}`.

---

## 6. Pre-demo setup script: warm `_kg_cache` for demo (ticker × pipeline) matrix

**Owner**: dev + demo lead. **Lands in**: commit 9 (alongside migration script).
**Status**: in-flight (setup-script line item).

VP-Prod's demo-day failure mode: §2.4 step 3 bails when KG isn't in
`_kg_cache`. R5 pre-warms the embedding model but not KGs. Operator opens
a cold slot mid-demo → dead-end prompt mid-pitch.

**Action**: Add a `scripts/warm_demo_caches.py` that:

1. Reads a `demo_matrix.yaml` (committed in `docs/sprints/.../demo-matrix.yaml`):
   ```yaml
   tickers: [JNJ, AAPL, JNJ-Stelara]
   pipelines: [fan_out, agentic_flash, agentic_analyst]
   bundles: [financial-default, clinical-default]
   ```
2. For each (ticker, pipeline, bundle) tuple:
   - Calls the same code path as Compare-tab Run (synchronously).
   - Persists `_graph/{graph_key}/` to disk.
   - Reports timing per cell.
3. Emits a green-light report: "ready for demo: X / Y cells warm."

Run by demo lead before any customer-facing meeting.

---

## 7. D5 round-trip test: assert `kind` preserved through write→read

**Owner**: dev. **Lands in**: commit 5 (D5 — `_graph/{graph_key}/` builder).
**Status**: EXECUTE-blocker for commit 5.

Bridges flow as ordinary edges in 5B but storage preserves `kind` field
for future 5C use. Without an explicit test, a future refactor could
silently drop the field.

**Action**: Add to `tests/unit/test_graph_index_builder.py`:

```python
def test_graph_index_preserves_kind_field_round_trip(tmp_path):
    """The `kind` discriminator (intra | bridge | registry) survives
    write→read so 5C's bridge-aware retrieval can still find them."""
    kg = {
        "entities": [...],
        "relationships": [
            {"id": "r1", "subject": ..., "predicate": "competes_with",
             "object": ..., "kind": "bridge"},
            {"id": "r2", "subject": ..., "predicate": "has_executive",
             "object": ..., "kind": "intra"},
        ],
    }
    write_graph_index(tmp_path / "_graph" / "test", kg)
    loaded = load_graph_index(tmp_path / "_graph" / "test")
    kinds = {e["id"]: e["kind"] for e in loaded["edges"]}
    assert kinds == {"r1": "bridge", "r2": "intra"}
```

---

## 8. Counter for `join_confidence == "none"` retrieval participation

**Owner**: dev. **Lands in**: commit 6 (D6 — LLM-pipeline join backfill).
**Status**: in-flight (telemetry-only; no UI).

VP-Eng nit: 5E's potential reranking work needs to know how often `none`-
confidence evidence is retrieved. Without a counter, no signal.

**Action**: In `graph_rag.retrieve()`, after assembling the result set,
log a structured event:

```python
logger.info(
    "graphrag.retrieval.join_confidence_breakdown",
    extra={
        "doc_key": doc_key,
        "graph_key": graph_key,
        "n_sentence": ...,
        "n_chunk": ...,
        "n_none": ...,
    },
)
```

5E can aggregate from logs; no UI / metric backend dependency in 5B.

---

## Summary: per-commit gate

| Commit | Followups landing | Status |
|--------|-------------------|--------|
| 0 (spike) | none | — |
| 1 (D1) | none | — |
| 2 (D2) | none | — |
| 3 (D3) | none | — |
| 4 (D4) | #2 | EXECUTE-blocker |
| 5 (D5) | #1, #7 | EXECUTE-blocker |
| 6 (D6) | #8 | in-flight |
| 7 (D7) | none | — |
| 8 (D8) | #4, #5 | EXECUTE-blocker (#4); in-flight (#5) |
| 9 (D9 + docs) | #3, #6 | EXECUTE-blocker (#3); in-flight (#6) |
| 10 (D9 cont.) | none | — |

EXECUTE-blockers (5 of 8 followups) are non-negotiable per their named
commit. In-flight items can land any time during the sprint.
