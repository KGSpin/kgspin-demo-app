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

---

## 9. Fan_out resolver-swap regression test (CTO post-spike requirement)

**Owner**: dev. **Lands in**: commit 1 (D1 — `kgspin_interface.text.normalize`
utility + chunk-id resolver deprecation).
**Status**: EXECUTE-blocker for commit 1.

CTO sign-off on spike picked **(a)** — deprecate the chunk-id-bound
`_resolve_evidence_span` and replace it with the global-search resolver
sprint-wide. Safety net: pin today's behavior **before** swapping so the
new path is provably a superset of the old one (or any diff is documented).

**Action**: in commit 1, before deleting `build_rag_corpus._resolve_evidence_span`:

1. Capture today's resolver output on the JNJ 10-K fan_out fixture:
   ```python
   # tests/regression/test_resolver_fan_out_baseline.py
   def test_fan_out_resolution_baseline_pinned():
       """Captures the chunk-id-bound resolver's output on JNJ 10-K
       (fan_out KG) so commit 1's resolver swap can prove subsumption."""
       baseline = json.loads(BASELINE_PATH.read_text())  # pinned in repo
       chunks = load_chunks("tests/fixtures/baseline-jnj-10k/")
       kg = load_fan_out_kg("tests/fixtures/baseline-jnj-10k/")
       resolved_today = {
           e.id: _resolve_evidence_span_LEGACY(plaintext, chunks, e.evidence)
           for e in kg.entities + kg.relationships
       }
       assert resolved_today == baseline
   ```
2. Run the new global resolver on the same input:
   ```python
   def test_fan_out_resolution_new_global_subsumes_baseline():
       """Asserts the new global resolver returns offsets that match the
       legacy chunk-id-bound resolver byte-for-byte on fan_out (or
       documents the per-evidence diff if any)."""
       baseline = json.loads(BASELINE_PATH.read_text())
       resolved_new = {
           e.id: resolve_evidence_offsets(plaintext, chunks, e.evidence)
           for e in kg.entities + kg.relationships
       }
       diff = {k: (baseline[k], resolved_new[k]) for k in baseline if baseline[k] != resolved_new[k][0]}
       if diff:
           # Allow diff but require it to be documented in the test docstring
           # AND the global resolver's char_span must STILL fall inside the
           # legacy span (i.e. the new resolver is at least as accurate).
           for ev_id, (legacy_span, (new_span, _conf)) in diff.items():
               assert new_span is None or _span_contained_in(new_span, legacy_span), (
                   f"{ev_id}: new resolver span {new_span} falls outside "
                   f"legacy span {legacy_span} — that's a regression, not a refinement."
               )
       assert len(diff) <= 0.05 * len(baseline), f"Diff rate {len(diff)/len(baseline):.1%} > 5%"
   ```
3. Pin the baseline JSON at `tests/fixtures/baseline-jnj-10k/resolver_baseline.json`
   and commit it. Future resolver changes must update this fixture
   intentionally.

If the diff is non-trivial (>5% of evidence rows differ between
resolvers), pause commit 1 — surface the diff to CTO before proceeding.

---

## 10. D6 explicit empirical threshold + mitigation path (CTO post-spike requirement)

**Owner**: dev. **Lands in**: plan v3 (immediately) + commit 6 (D6).
**Status**: EXECUTE-blocker for commit 6.

CTO callout: spike says "LLM pipelines likely pass; empirical measurement
deferred to D6" — that's fine for the spike gate, but D6 needs an
**explicit empirical threshold** AND a pre-defined mitigation path so
commit 6 doesn't discover failure with no plan.

**Threshold (locked)**: ≥95% **sentence-level** resolution on
`agentic_flash` + `agentic_analyst` over the JNJ 10-K test fixture.
(Note: spike's gate criterion was ≥90% across `{sentence, chunk}` —
this is a stricter sub-criterion specifically for sentence-level
resolution on LLM pipelines, since chunk-level fallback degrades
retrieval precision.)

**Mitigation paths** (pre-defined; D6 picks based on which pipelines fail):

- **(i) Per-pipeline resolver registry**: `RESOLVER_REGISTRY[pipeline]`
  maps each pipeline to a custom resolver. LLM pipelines that fail the
  ≥95% gate get a richer resolver:
  - Tighter prompt fidelity (re-prompt the LLM with "use exact wording
    from the source"), OR
  - Pre-search source.txt for sentences containing each emitted entity's
    text and use the longest substring overlap as the sentence anchor.
  Cost: +1-2 days dev work in D6; no operator-visible impact.

- **(ii) Lower threshold + document known-imperfect rate**: ship D6 with
  the empirical rate (e.g. "agentic_flash resolves to sentence-level at
  87%; chunk-level at 13%") in `architecture.md`. Defer perfect resolution
  to 5E (provenance-aware reranking) where chunk-level evidence gets
  downweighted. Acceptable iff failed pipeline still ≥85% combined
  `{sentence, chunk}` rate.

- **(iii) Downgrade pipeline out of Q1=(b) day-1 scope**: index built but
  flagged `retrieval_ready=False`. The slot's modal Why-tab Run shows a
  "GraphRAG retrieval reduced to chunk-level only for {pipeline}" status.
  Per CTO scope-cut (no new UX in modal Why tab), this becomes a console
  log + admin warning instead of a UI label. Pipeline still works, just
  documented as imperfect.

**D6's commit message** must explicitly state which mitigation was
chosen (if needed) and the empirical numbers. CTO sign-off required
before commit 6 lands if mitigation (i) or (iii) is invoked.

---

## 11. Cross-repo backlog: kgspin-core `Evidence.character_span` universality (CTO post-spike requirement)

**Owner**: kgspin-core team (CTO-dispatched directly).
**Status**: ✅ **RESOLVED — ADR-037 (option B chosen: remove the field).**

CTO dispatched `sprint-evidence-character-span-audit-20260501` (off
kgspin-core's `main`) in parallel with this filing. Sprint landed
**ADR-037 (Accepted)** with option B: `Evidence.character_span` is
removed from the schema. Reasoning per ADR-037: chunker offsets have
paragraph-strip drift so populating the field accurately was
unattainable cheaply, and the demo team's global-search resolver
(`kgspin_interface.text.normalize.resolve_evidence_offsets`, landed
in commit 1) already subsumes the use case. Demo team's spike diagnosis
was right; the global resolver is now the canonical span-resolution
path across the kgspin cluster.

**Demo-team action — verified during commit 2 (D2)**: zero reads of
`Evidence.character_span` in `kgspin_demo_app/`, `scripts/`, or
`services/`. Test fixtures inject the value as dict data for testing
downstream consumers; no test asserts real extraction produces
character_span. Net: no demo-app code change required for ADR-037.

**Post-ADR-037 cleanup** (not 5B-blocking): when kgspin-core's removal
PR merges, demo-app test fixtures
(`tests/unit/test_graph_rag.py`, `tests/unit/test_build_rag_corpus.py`)
can drop the `"character_span"` keys from their hand-authored Evidence
dicts. Trivial cleanup; tracked but not gating.

**Cite at sprint close**: dev-report.md will reference ADR-037 as the
canonical record of the removal decision and the cross-repo trail
(spike diagnosis → CTO dispatch → ADR → demo's resolver becomes the
cluster's span-resolution path).

---

## 12. Lineage display wires through `resolve_evidence_offsets` (CTO post-D1 requirement)

**Owner**: dev. **Lands in**: commit 5 (D5 — `_graph/{graph_key}/`
builder) **as a sub-commit / co-commit** (CTO dealer's-choice between
D5 and D6; we picked D5 for artifact-dependency cohesion).
**Status**: EXECUTE-blocker for commit 5.

The CEO wants the lineage display to render character-precise
highlighting (highlight the exact phrase within the sentence, not the
whole sentence). Today the demo surfaces evidence at 3 call sites as
`r.evidence.sentence_text`:

- `src/kgspin_demo_app/api/server.py:382`
- `src/kgspin_demo_app/api/server.py:514`
- `src/kgspin_demo_app/mcp_server.py:323`

All three follow the identical pattern:
`"evidence": r.evidence.sentence_text if r.evidence else None`.

**Action**: in commit 5's D5 work (where the `_graph/{graph_key}/`
artifact lands and `source.txt` is on disk via D2), update each
call site to:

1. Resolve `(start_char, end_char)` against `source.txt` via
   `kgspin_interface.text.normalize.resolve_evidence_offsets`.
2. Emit alongside `sentence_text`:
   ```json
   {
     "subject": "...",
     "predicate": "...",
     "object": "...",
     "evidence": "Tim Cook serves as ...",
     "evidence_span": [12345, 12401],
     "evidence_join_confidence": "sentence"
   }
   ```
3. UI template change: render character-precise highlighting using the
   `(start, end)` offsets (highlight the exact span inside `sentence_text`,
   not the whole sentence).

**Sequencing**: depends on D2's `source.txt` artifact being available
at request-time. D5 is when the `_graph/{graph_key}/` lookup wires up
end-to-end, so the lineage call sites can read the persisted
`source.txt` from the slot's `(domain, source, ticker, date)` resolved
path.

**Scope cap**: ~1-2 hours including UI template + tests. CTO
escalation trigger if it grows past 3h. Open known check: whether
the lineage UI already has an offset-based highlighter (some templates
do; some highlight the whole sentence as a span). If it doesn't, the
template work is closer to ~1.5h than 0.5h. Surface during D5; ship a
sentence-span fallback for D5 + defer character-precise highlighting
to a follow-up sprint if total exceeds 3h.

**Why now (per CTO)**: the resolver is fresh in our heads, the surface
is tiny (3 call sites + one template), and the alternative is
re-context-switching later. Demo lineage and RAG converge on the same
primitive — that's the cleanup.

---

## Summary: per-commit gate

| Commit | Followups landing | Status |
|--------|-------------------|--------|
| 0 (spike) | none | — |
| 1 (D1) | **#9 (CTO post-spike: regression-test safety net)** | EXECUTE-blocker |
| 2 (D2) | none | — |
| 3 (D3) | none | — |
| 4 (D4) | #2 | EXECUTE-blocker |
| 5 (D5) | #1, #7, **#12 (CTO post-D1: lineage uses resolver)** | EXECUTE-blocker |
| 6 (D6) | #8, **#10 (CTO post-spike: empirical threshold + mitigation)** | in-flight (#8); EXECUTE-blocker (#10) |
| 7 (D7) | none | — |
| 8 (D8) | #4, #5 | EXECUTE-blocker (#4); in-flight (#5) |
| 9 (D9 + docs) | #3, #6 | EXECUTE-blocker (#3); in-flight (#6) |
| 10 (D9 cont.) | none | — |
| (post-sprint) | **#11 (CTO post-spike: kgspin-core backlog ticket)** | post-sprint, not blocking |

EXECUTE-blockers (9 of 12 followups) are non-negotiable per their named
commit. In-flight items can land any time during the sprint. Post-sprint
items are filed but don't gate 5B.

---

## CTO post-spike additions (2026-05-01)

Three items added after CTO sign-off on commit 0's spike:

- **#9** — fan_out resolver-swap regression test (commit 1 EXECUTE-blocker). ✅ landed.
- **#10** — D6 explicit ≥95% sentence-level threshold + 3 pre-defined
  mitigation paths (commit 6 EXECUTE-blocker).
- **#11** — kgspin-core decision on `Evidence.character_span` —
  ✅ **RESOLVED** by ADR-037 (option B: remove the field). Verified
  during commit 2 that demo-app code doesn't read the field; cleanup
  of test-fixture dict keys post-ADR-037 merge.

CTO confirmed: push convention stays "push only on sprint-close." No
flip needed.

---

## CTO post-D1 additions (2026-05-01)

One item added after CTO sign-off on commit 1 (D1):

- **#12** — Lineage display wires through `resolve_evidence_offsets`
  at 3 call sites. EXECUTE-blocker for commit 5 (D5 sub-commit per
  CTO dealer's-choice between D5 and D6; we picked D5 for
  artifact-dependency cohesion). Scope cap: 1-2h with 3h escalation
  trigger.
