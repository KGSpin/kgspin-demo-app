# Sprint Plan — phase-2-installation-plan (kgspin-demo-app)

**Branch:** `phase-2-installation-plan` (off `main` @ 8684797)
**Type:** PLAN-ONLY. Push, do NOT merge. CEO + VPs approve, then Phase 2 fires execution.
**CTO assignment:** 2026-04-26 — "Phase 2 — INSTALLATION (PLAN-ONLY, cross-repo)"
**Date:** 2026-04-26
**Repo's slice:** Step 4 of 4 (downstream of kgspin-interface → kgspin-admin → kgspin-core).

## TL;DR

This repo's Phase 2 deliverable is **customer-facing surfacing of the triple-hash**:
every extraction response — across `api/server.py`, `mcp_server.py`, and the cached-runs
UI — carries an `extraction_metadata` block with `pipeline_version_hash`,
`bundle_version_hash`, and `installation_version_hash`. Plus a replay endpoint
(scoped IN, contingent on a small kgspin-core API addition) and a one-page
customer-facing doc explaining what the three hashes mean and how to pin them.

We are the *consumer*: kgspin-interface defines the schema, kgspin-admin stores
the InstallationConfig, kgspin-core mints the hashes and threads them through
the extractor. By the time this repo's tasks fire, all three upstream contracts
are landed; demo-app's job is plumbing + presentation.

## Cross-repo posture

Per CTO sequencing (CTO assignment §"Cross-repo sequencing"):

| Step | Repo | Delivers | Consumed by demo-app as |
|---|---|---|---|
| 1 | kgspin-interface | `InstallationConfig` Pydantic model + `INSTALLATION_CONFIG_SCHEMA_V1` | Imported wire shape (schema-version constant for compat checks) |
| 2 | kgspin-admin | Resource type `installation_config` + `GET /resource/installation_config/<hash>` | HTTP fetch source for the active installation (and per-request pinned version, for replay) |
| 3 | kgspin-core | Orchestrator reads installation; result carries `(pipeline_version_hash, bundle_version_hash, installation_version_hash)` | Hashes attached to `result.provenance` (or sibling field) on every `extractor.extract(...)` call |
| **4** | **kgspin-demo-app (this plan)** | Triple-hash on every response surface; replay endpoint; customer doc | — |

Demo-app does **no hash computation**. We surface what core hands us. If core
hands us `installation_version_hash = None` (e.g., admin unreachable, fallback
to defaults), we surface `None` and document the meaning.

## Scope

**In:**
1. **Triple-hash on every customer-returning surface.**
   - `api/server.py`:
     - `EntityResponse` (POST /extract/entities) — add `extraction_metadata` block
     - `RelationshipResponse` (POST /extract/relationships) — add `extraction_metadata` block; keep `bundle_version` flat field for one release as a deprecation shim
     - `establish_relationship` JSON return (POST /extract/establish) — add `extraction_metadata`
   - `mcp_server.py`:
     - `_extract_entities`, `_extract_relationships`, `_establish_relationship` outputs — same `extraction_metadata` block
   - `demos/extraction/routes/runs.py` (cached-runs UI):
     - When loading a run, render `extraction_metadata` if present; render `<pre-Phase-2>` for the `installation_version_hash` slot on legacy cached runs (no data migration).
2. **Replay endpoint** — `POST /extract/replay/relationships` (scoped IN, contingent — see §Risks):
   - Body: `{text, source_document, pipeline_version_hash, bundle_version_hash, installation_version_hash}`.
   - Verifies `pipeline_version_hash` matches the loaded core; if mismatch → 409 with `expected/got`.
   - Fetches the pinned bundle + installation by hash from admin (per-request, in-process; does NOT mutate the global active version pointer).
   - Runs extraction with those pins; returns the same shape as `/extract/relationships` plus an echo of the requested triple.
   - **Contingent on kgspin-core exposing `KnowledgeGraphExtractor(bundle, installation_config=...)`** (currently constructor takes only `bundle`). If core declines per-request override → defer endpoint; ship surfacing only. See §Open items.
3. **Customer-facing copy** — new `docs/reproducibility-by-triple-hash.md` (~1 page):
   - What each hash means (1 paragraph each).
   - How to pin all three (CLI/API examples).
   - What customers see when one is `None` (legacy / pre-Phase-2 / admin unreachable).
   - Cross-link from `README.md`.
4. **Smoke tests** — assert triple-hash field presence on every surface (3 API endpoints + 3 MCP tools + 1 cached-run shape). Boundary cases: all three present; `installation_version_hash = None`; replay 409 on pipeline mismatch.
5. **ADR-006 update** — replace "Phase 2 placeholder" wording with a "Phase 2 landed" note + cross-link to this sprint's plan.

**Out:**
- No work that belongs to upstream repos (no schema definition, no admin storage, no core orchestrator changes).
- No multi-region installation discovery (CTO out-of-scope).
- No customer UX rollout beyond the API + minimal copy (CTO out-of-scope; product sprint follows).
- No bundle YAML edits.
- No mutation of admin's active-version pointer from demo-app (replay is in-process per-request override only).
- No backfill of triple-hash onto historical cached runs (legacy renders as `<pre-Phase-2>`).

## Task breakdown

| # | Task | Effort | Sequencing | Owner-facing files |
|---|---|---|---|---|
| 1 | Define demo-app `ExtractionMetadata` Pydantic model (3 hash fields, schema-version field, all `Optional[str]` to allow `None`); import schema-version constant from kgspin-interface | S | After Step 1 lands | `src/kgspin_demo_app/api/server.py` (new model class) |
| 2 | Wire triple-hash into `EntityResponse`, `RelationshipResponse`, establish endpoint return | M | After Step 3 lands (need core to mint hashes) | `src/kgspin_demo_app/api/server.py` |
| 3 | Wire triple-hash into MCP tools (`_extract_entities`, `_extract_relationships`, `_establish_relationship`) | S | Parallel with #2 | `src/kgspin_demo_app/mcp_server.py` |
| 4 | Cached-runs UI: render `extraction_metadata` block; legacy fallback copy | S | Parallel with #2 | `demos/extraction/routes/runs.py`, `demos/extraction/static/*` |
| 5 | Replay endpoint `POST /extract/replay/relationships` | M | After #2 + core constructor change | `src/kgspin_demo_app/api/server.py` |
| 6 | Smoke tests for triple-hash presence + replay 409 path | M | Parallel with #2–#5 | `tests/api/test_triple_hash_surfacing.py` (new) |
| 7 | Customer-facing doc `docs/reproducibility-by-triple-hash.md` + README cross-link | S | Parallel with implementation | `docs/reproducibility-by-triple-hash.md` (new), `README.md` |
| 8 | ADR-006 update note ("Phase 2 landed") | XS | Last | `docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md` |

Effort key: XS ≤ 1h, S ≤ 0.5d, M ≤ 1.5d, L ≤ 3d. Sprint total: ~5–6 dev-days,
single dev. Replay endpoint (#5) is the longest pole and the only contingent
item — hence the §Risks discussion.

## Sequencing (within this sprint)

Demo-app cannot start coding until kgspin-core (Step 3) lands, because we
consume `result.<hashes>` from the orchestrator. Doc-only tasks (#1 model
sketch, #7 customer copy, #8 ADR update wording) can begin earlier — they
need only the wire shape from kgspin-interface (Step 1) and the customer-
facing language from kgspin-interface ADR-004.

Suggested intra-sprint order:
1. (Pre-core-landing) #7 doc draft, #1 model class skeleton, #8 ADR copy.
2. (After core lands) #2, #3, #4 in parallel — three small wiring PRs.
3. (After #2) #6 smoke tests pinning the surfacing.
4. (After core's per-request override decision) #5 replay endpoint OR documented deferral.
5. Final commit: #8 ADR update.

## Definition of Done

- Every surface that returned an extraction result before Phase 2 now also returns `extraction_metadata` with three keys (any of them may be `None`).
- `extraction_metadata` round-trips through the cached-runs UI (live + legacy).
- If implemented: `POST /extract/replay/relationships` rejects pipeline mismatch with 409 and successfully replays on match. If deferred: a tracked follow-up issue references the core dependency.
- `docs/reproducibility-by-triple-hash.md` reviewed by VP-Prod (covered in this sprint's R1/R2).
- Smoke tests green; pre-Phase-2 tests not regressed.
- ADR-006 status note updated.
- Branch `phase-2-installation-plan` (this PLAN-ONLY commit) pushed; later, an execution branch lands the implementation.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **kgspin-core declines per-request installation override** (replay endpoint blocked) | M | M | Document deferral cleanly; surfacing-only still gives customers the *verification* half of reproducibility (they can confirm two extracts share a triple). Open follow-up sprint to add the core constructor knob. |
| Admin's `GET /resource/installation_config/<hash>` shape diverges from core's read shape, demo-app pulls a different field set | L | M | Demo-app must NOT bypass core to read installation directly. We always go through `result.<hashes>` (core has read it). Replay endpoint asks core for the loaded installation's hash, asks admin only for "fetch by hash" (Step 5 of admin plan). |
| `installation_version_hash = None` proliferates (admin unreachable / fallback path) and confuses customers | M | L | Customer doc has explicit "what `None` means" section; UI shows `<unset>` with a tooltip; smoke tests cover the `None` case. |
| MCP tool callers depend on flat `bundle_version` field; nesting under `extraction_metadata` breaks them | L | L | Keep `bundle_version` flat for one release alongside the new block; deprecation note in changelog; remove in a follow-up sprint. |
| Cached-runs UI renders legacy runs incorrectly when injecting Phase 2 fields | L | L | Detect absence of `extraction_metadata` in cached payload; render `<pre-Phase-2>` placeholder; cover with smoke test on a fixture from a Wave J cached run. |
| Replay endpoint payload hits the same 409 path repeatedly as customers paste pipeline hashes from older deploys | M | L | 409 body includes `installed_pipeline_hash` so customers know what version this deployment is on; doc explains the workflow. |

## Open items (for VP/CEO review)

1. **Replay endpoint scope.** Plan keeps it IN, contingent on a small core
   constructor change (`KnowledgeGraphExtractor(bundle, installation_config=...)`).
   If core's plan declines that knob, demo-app drops #5 and ships surfacing-only.
   This must be locked before execution kicks off.
2. **Backwards-compat shim window.** `bundle_version` flat field — keep for
   one release? Remove immediately? Plan recommends one-release shim to avoid
   breaking external MCP callers; CEO can shorten.
3. **Doc placement.** `docs/reproducibility-by-triple-hash.md` at top level of
   `docs/`, or under `docs/architecture/`? Plan defaults to top level (it's
   customer-facing, not architectural). VP-Prod should validate.
4. **Replay endpoint method/path.** Plan picks `POST /extract/replay/relationships`
   to mirror existing `/extract/relationships` shape. Alternative: extend the
   existing endpoint with optional `replay_pins` body fields. Plan's choice keeps
   the contracts independent and the 409 path explicit.

## Cross-repo wire-shape assumptions (callable-out at CEO review)

This plan assumes:
- kgspin-core exposes the triple on its result object via stable field names
  (`result.pipeline_version_hash`, `result.bundle_version_hash`,
  `result.installation_version_hash`) — or via a sibling `result.metadata`
  object. Either is fine; demo-app adapts.
- kgspin-admin's `GET /resource/installation_config/<hash>` returns at minimum
  the hash, the schema-version, and the resolved YAML body; demo-app does not
  parse YAML field-by-field (core does that), only forwards the body to core.
- kgspin-interface exports `INSTALLATION_CONFIG_SCHEMA_V1` as an importable
  constant; demo-app embeds it in `extraction_metadata.schema_version` so
  customers can detect schema bumps without needing to talk to admin.

If any of those three assumptions breaks at integration time, that's a
cross-repo coordination spike, not a demo-app re-plan.

## Completion criteria for THIS planning sprint

- `sprint-plan.md` (this file) on disk.
- `vp-eng-review.md` on disk with explicit **APPROVED**.
- `vp-prod-review.md` on disk with explicit **APPROVED** and an explicit
  yes/no on the customer-facing reproducibility property.
- Single commit on branch `phase-2-installation-plan`, pushed to `origin`,
  NOT merged.
- Signal: **SPRINT_PLAN_READY**.
