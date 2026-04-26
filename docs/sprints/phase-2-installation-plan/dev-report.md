# Dev Report — phase-2-installation-exec (kgspin-demo-app)

**Branch:** `phase-2-installation-exec` (off `main`@`541d601`)
**Date:** 2026-04-26
**Sprint plan:** `docs/sprints/phase-2-installation-plan/sprint-plan.md`
**CTO assignment:** "Phase 2 INSTALLATION — kgspin-demo-app EXECUTE (last in sequence)"
**Predecessor sprints landed:** kgspin-interface@`8af1afd`, kgspin-admin@`31e1736`,
kgspin-core@`788afe2`.

## Summary

Demo-app's Phase 2 deliverable — customer-facing surfacing of the
triple-hash on every extraction-returning API surface — is shipped on
this branch. All four CTO-mandated tasks are complete; the replay
endpoint shipped in its **match-or-409** form (the per-historical-hash
fetch variant is documented as a Phase 2.1 follow-up).

## What landed

### Task 1 — Triple-hash surfacing on every extraction-returning surface

| Surface | File | What it now returns |
|---|---|---|
| `POST /extract/relationships` | `src/kgspin_demo_app/api/server.py` | `extraction_metadata` block with `schema_version`, `pipeline_version_hash`, `bundle_version_hash`, `installation_version_hash`, lifted from `result.provenance` (kgspin-core stamps it). Flat `bundle_version` field kept one release as a deprecation shim. |
| `POST /extract/entities` | `src/kgspin_demo_app/api/server.py` | `extraction_metadata: null` (GLiNER entity-only path doesn't run the orchestrator). Field present on the wire so the response shape is schema-stable. |
| `POST /extract/establish` | `src/kgspin_demo_app/api/server.py` | `extraction_metadata` block with `bundle_version_hash` computed from the linker's bundle; pipeline / installation surface as `null` (linker doesn't carry an extractor's frozen triple). |
| MCP tools (`extract_relationships`, `extract_entities`, `establish_relationship`) | `src/kgspin_demo_app/mcp_server.py` | Same `extraction_metadata` block shape (dict, not Pydantic model) on every tool output. |
| Cached-runs UI (Gemini, Modular, KGen, Intel) | `demos/extraction/routes/runs.py` | Detail JSON includes `extraction_metadata` lifted from `kg.provenance`; legacy runs render `<pre-Phase-2>` for fields not recorded at extraction time. |

The new `ExtractionMetadata` Pydantic model lives in
`src/kgspin_demo_app/api/server.py`. Field order is pinned in the
declaration and verified by a smoke test (per VP-Eng C4 from the plan
review). Empty strings (kgspin-core's migration-window default) are
normalized to `None` by the `_build_extraction_metadata` helper so the
customer-facing surface has one consistent "unset" representation.

### Task 2 — Replay endpoint

`POST /extract/replay/relationships` is shipped as a **match-or-409**
endpoint:

- Accepts `{text, source_document, bundle_name, pipeline_version_hash,
  bundle_version_hash, installation_version_hash}`.
- Runs extraction, then verifies the resulting `result.provenance`
  triple matches the request's pinned triple.
- On match → 200 with the same shape as `/extract/relationships` plus
  the echoed triple in `extraction_metadata`.
- On any mismatch → 409 with `requested` and `installed` triples in
  the detail body so the customer can see what version this deployment
  is on.

Per-historical-hash replay (fetch arbitrary bundle / installation by
hash from admin, build a fresh extractor in-process, run, return) is
**deferred to Phase 2.1**. Rationale documented below in §"Replay
deferral".

### Task 3 — Customer-facing copy

`docs/reproducibility-by-triple-hash.md` (~one page, top-level under
`docs/` per the sprint plan's choice). Sections:

- The three hashes (with customer-facing names per VP-Prod C1).
- Where the hashes appear (JSON API, MCP, cached-runs UI).
- Property V (verifiable identity) and Property P (pinnable
  reproduction).
- Worked example end-to-end (per VP-Prod C2): extract → capture triple
  → replay → diff.
- "What stays identical, what may vary" (per VP-Prod C4): entity set,
  relationship set, bundle version reproduce; confidence scores within
  ~3 decimal places; processing-time / wall-clock do not.
- When `installation_version_hash` is `None` semantics.
- Cross-references to ADR-004, ADR-006, and the cross-repo notice.

Cross-link added from `README.md`.

### Task 4 — Cross-repo acknowledgment

`docs/cross-repo/2026-04-26-phase-2-installation-notice-received.md`
on disk:

- Cross-link to kgspin-core's
  `docs/cross-repo/2026-04-26-phase-2-installation-core-completion-notice.md`.
- Itemizes which surfaces now carry the triple-hash.
- Explicit no-reintroduction confirmation for the 7 migrated fields
  in the bundle reads.
- Explicit no-SECRETS-leak confirmation in the customer-facing surface.
- Explicit verbatim-passthrough confirmation (no filtering / rewriting
  of hashes).
- Operator runbook (Success Metric #2 from upstream) is **deferred to
  Phase 2.1** alongside the per-historical-hash replay endpoint.
  Rationale: today's match-or-409 doesn't need it; the runbook only
  becomes load-bearing when admin pointer mutations / pinned reruns
  enter the operator workflow.

### Task 5 — Tests

`tests/api/test_triple_hash_surfacing.py` — 15 tests, all green:

- `ExtractionMetadata` field order pinned (VP-Eng C4).
- `schema_version` defaults to `INSTALLATION_CONFIG_SCHEMA_V1`.
- `_build_extraction_metadata` normalizes empty-string defaults to
  `None`.
- Verbatim hash passthrough.
- `RelationshipResponse` carries the new field; flat `bundle_version`
  preserved as deprecation shim.
- `EntityResponse` returns `extraction_metadata: null` on the
  GLiNER-only path.
- MCP `_extraction_metadata_dict` mirrors the API model's field order
  and missing-attr behavior.
- Cached-runs UI fallback to `<pre-Phase-2>` for legacy runs.
- Cached-runs UI verbatim passthrough for live runs.
- Cached-runs UI normalizes drift empty-strings to `<pre-Phase-2>`.
- Replay endpoint returns 200 on triple match.
- Replay endpoint returns 409 with installed triple on each of
  pipeline / bundle / installation mismatch (3 tests).

The replay endpoint tests use `monkeypatch` to stub
`KnowledgeGraphExtractor` and `get_bundle` so they exercise the
endpoint logic without spinning up the full ML pipeline (consistent
with the surrounding test fixtures' philosophy).

The plan's R2 "round-trip determinism" assertion (per VP-Eng C2) is
deliberately **not** added to demo-app's suite — the byte-identical
property under pinned triples is owned by kgspin-core's test suite
(the orchestrator's triple-hash machinery is verified there). Adding
a duplicate test in demo-app would be redundant and would require
spinning up a heavyweight extraction in CI for marginal coverage.
This is a deliberate scope decision; flagging it for VP-Eng's
Phase 3 review.

### Task 6 — ADR-006 update

`docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md`
gains a "Status as of 2026-04-26: Phase 2 LANDED" section at the top of
"Phase 2 implications for this repo". The pre-Phase-2 wording is
preserved for history under a "Pre-Phase-2 wording (kept for history)"
subheading.

## Replay deferral — rationale and design for Phase 2.1

The match-or-409 replay endpoint shipped today delivers Property P
**partially**: a customer can demand "rerun this extraction with the
triple I captured" and the deployment will either reproduce
(deterministically modulo declared model non-determinism) or refuse
honestly (409 with the deployment's actual triple). What it does NOT
do today is reconstruct an arbitrary historical deployment in-process.

The full per-historical-hash design needs:

1. **Admin's `GET /resource/installation_config/<hash>` endpoint
   call** from demo-app — already exists in
   `kgspin_core.registry_client.AdminResourceRegistryClient.get_installation_config(version_hash)`.
2. **Admin's `GET /resource/bundle/<hash>` endpoint call** from
   demo-app — does **not** exist yet. Bundles are addressed by hash
   in the registry but the demo-app-side fetcher for "load arbitrary
   bundle by hash" is not wired through `kgspin_core.cli.utils`.
3. **Per-request extractor construction** — the
   `KnowledgeGraphExtractor(bundle, installation_config=...,
   triple_hash=...)` constructor already accepts both kwargs (verified
   in kgspin-core's kg_orchestrator.py:223–230). No upstream change
   needed here.
4. **Replay endpoint expansion** — fetch (1) and (2), construct (3),
   run extraction, return result with the requested triple echoed.

Phase 2.1 sprint scope to add:
- A `GET /resource/bundle/<hash>` admin client method (one PR in
  kgspin-core).
- Demo-app's replay endpoint upgraded from match-or-409 to
  fetch-and-replay.
- The operator runbook deferred above (admin-flip flow, what to do
  when `installation_version_hash = null` proliferates).

The match-or-409 endpoint shipping today is forward-compatible: the
request shape is identical to what the per-hash variant will accept,
so callers won't see a wire-shape change when Phase 2.1 lands —
they'll just see fewer 409s (since they'll succeed against
historical deployments).

## Critical-constraint compliance

Per the CTO assignment's three constraints:

1. **No reintroduction of the 7 migrated fields into demo-app's
   bundle reads.** Confirmed via grep:
   ```
   grep -rn "nli_max_candidates_per_sentence\|chunking.max_chars\|chunking.overlap\|extraction_max_workers\|fan_out_window_tokens\|fan_out_sentence_size_estimate\|heuristic_distance_max_chars" src/kgspin_demo_app/api/server.py src/kgspin_demo_app/mcp_server.py
   ```
   returns no hits. Demo-app reads provenance from `result.provenance`,
   never from the bundle directly.

2. **No SECRETS in customer-facing surface.** `ExtractionMetadata`
   carries only the four fields documented in
   `docs/reproducibility-by-triple-hash.md`: schema version + three
   hashes. API keys, model paths, and auth tokens stay env-var-only
   (verified by reading `EXTRACTION_METADATA` field declarations).

3. **Verbatim hash passthrough.** Demo-app does not filter or rewrite
   the hashes. Empty strings (kgspin-core's migration-window default
   per `lineage.py:700-702`) are normalized to `null` for one
   consistent "unset" representation, but no mutation of present
   hashes. Passthrough is asserted by the
   `test_build_extraction_metadata_passes_through_real_hashes` smoke
   test.

## Test results

```
$ uv run pytest tests/api/test_triple_hash_surfacing.py -q
...............                                                          [100%]
15 passed in 0.32s
```

Wider regression check (excluding the integration / playwright /
manual suites that need running services):

```
$ uv run pytest -q --ignore=tests/integration --ignore=tests/playwright --ignore=tests/manual
3 failed, 304 passed, 4 skipped, 1 deselected, 16 warnings in 52.51s
```

The 3 failures + 1 deselect are **pre-existing on main** — verified
by `git stash && pytest && git stash pop`. They are
`bundle_schema_version` mismatch errors against
`kgspin-blueprint/references/bundles/compiled/domains/financial-v2`
(declares schema 2.0, kgspin-core requires 3.0). Unrelated to this
sprint; flagged as a separate cross-repo ticket for the
kgspin-blueprint team to bump.

## Side-effect cleanup (incidental)

`api/server.py` and `mcp_server.py` had stale imports from the
pre-extraction repo split:
`from ..execution.extractor import ExtractionBundle, KnowledgeGraphExtractor`
(and similar for `embeddings`, `cli.utils`, `agents.pattern_compiler`,
`tools.linker_tool`). These pointed at modules that no longer exist
under `kgspin_demo_app.*` — they now live under `kgspin_core.*`.
Both files would `ImportError` at module import time. Fixed as part
of this sprint since the surfacing work touches these files; the
fix is a mechanical `..execution.extractor` →
`kgspin_core.execution.extractor` rename (and parallel for the
other four imports). Without it, the new tests would not be runnable.

## Definition of Done

Per the CTO assignment:

- [x] Triple-hash surfacing live in API + CLI + UI. (CLI is the MCP
  server — same surface treatment as API.)
- [x] Replay endpoint built (match-or-409 form); per-historical-hash
  variant explicitly deferred-with-design (above).
- [x] Customer-facing copy on disk.
- [x] Cross-repo acknowledgment on disk.
- [x] Tests pass (15 new tests, all green).
- [x] dev-report on disk (this file).
- [ ] Phase-3 VP reviews on disk (next steps after this report).
- [ ] Branch pushed, NOT merged.

## Commits on this branch

(Fill in after the commit + push.)

— Dev Team (kgspin-demo-app)
