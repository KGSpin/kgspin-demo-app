# VP-Eng Review — phase-2-installation-plan (kgspin-demo-app)

**Reviewer:** VP-Eng (kgspin-demo-app)
**Sprint:** phase-2-installation-plan
**Date:** 2026-04-26

## R1

### Strengths

- **Repo-role discipline.** Plan correctly frames demo-app as Step 4 / pure
  consumer. No attempt to redefine the schema, no attempt to compute hashes
  locally, no attempt to read admin's storage directly. That's the right
  posture for this repo and stays consistent with the architecture-3yaml-rollout
  ADR-006 framing.
- **Surface inventory is complete.** Three API endpoints, three MCP tools, the
  cached-runs UI. I cross-checked against `src/kgspin_demo_app/api/server.py`
  and `src/kgspin_demo_app/mcp_server.py`; no extraction-returning surface is
  missed. Pattern compiler endpoints (`/compile`) correctly excluded — they
  return *bundle* metadata, not extraction output.
- **Backwards-compat discipline.** Keeping `bundle_version` flat alongside the
  new `extraction_metadata` block is the right call for one release. MCP
  callers in particular tend to be brittle.
- **Replay endpoint reasoning.** The plan does NOT mutate admin's active-version
  pointer to achieve replay — that would have been a multi-tenant correctness
  bug. Per-request in-process override is the right shape; the dependency on a
  core constructor knob is honestly named.
- **Legacy-data handling.** `<pre-Phase-2>` placeholder for cached runs is the
  right choice — no migration, no fake hashes, just an honest signal.

### Conditions

**C1 (BLOCKER → resolved in R2).** The plan says "Demo-app must NOT bypass core
to read installation directly." Good principle but the plan's #5 (replay
endpoint) implies fetching by hash from admin. Need an explicit clarification:
does demo-app fetch the installation YAML body from admin and *forward* it
into core's constructor (which then validates + parses), or does demo-app
hand core only the hash and core fetches from admin? The former minimizes
demo-app's dependency on admin's full wire shape; the latter is cleaner.
Plan needs to pick one.

**C2 (MAJOR → resolved in R2).** Smoke-test scope is described as field
*presence*. That's necessary but not sufficient. We need at least one
*round-trip* assertion: extract a doc, capture the triple, re-run with the
captured triple, assert byte-identical extraction output (modulo non-
determinism). Without this, "replay works" is a claim, not a verified
property.

**C3 (MINOR → resolved in R2).** Effort estimate "5–6 dev-days, single dev"
omits review/integration overhead. Plan should add a 1-day buffer for
cross-repo integration friction (admin's wire shape arriving slightly
different from assumption, etc.).

**C4 (MINOR → resolved in R2).** No mention of how `extraction_metadata`
field ordering is stable across responses. JSON dict ordering matters for
some downstream tooling (CLI grep, log aggregators). Plan should pin field
order in the Pydantic model.

**C5 (NIT).** The contingent replay endpoint is the highest-risk item. If
replay slips, the *surfacing-only* deliverable is still load-bearing for
the GTM property (customers can verify reproducibility offline by comparing
two extract responses). Plan should call this out explicitly so a
"surfacing-only" outcome doesn't read as failure.

### R1 verdict

**CONDITIONALLY APPROVED.** Address C1–C4 in R2; C5 is a copy nit.

---

## R2 (post-conditions)

Plan author addresses:

- **C1.** Demo-app sends the *hash only* to core; core fetches the YAML body
  from admin. This keeps demo-app's admin dependency to one endpoint
  (`GET /resource/installation_config/<hash>` is consumed only by core),
  and demo-app calls admin only to *verify the hash exists* before passing
  it through (defensive: better 404 from demo-app than ambiguous error from
  core). Will add this as a §"Cross-repo wire-shape assumptions" bullet.
- **C2.** Adds a determinism-budget round-trip test to task #6. Captures
  triple from a first run, re-runs with explicit pins, asserts:
  - Same entity set (subject `text` + `entity_type` tuples).
  - Same relationship set (subject + predicate + object tuples).
  - Confidence scores within a documented epsilon (model non-determinism).
  Tests are NOT byte-identical (LLM scores fluctuate at the 4th decimal in
  practice on this codebase) but are property-identical, which is what the
  customer cares about.
- **C3.** Effort revised: ~5–6 dev-days code + 1-day cross-repo integration
  buffer = 6–7 dev-days total. Plan's task table updated.
- **C4.** Pydantic model `ExtractionMetadata` with field order:
  `schema_version, pipeline_version_hash, bundle_version_hash,
  installation_version_hash`. Pydantic v2 preserves declaration order in
  serialization; pinned by smoke test.
- **C5.** Plan updated to add an explicit "Minimum acceptable outcome"
  section: surfacing-only ships the *verification* half of the customer
  property; replay ships the *reproduction* half. Both are valuable; only
  surfacing is strictly required.

(Author will make these edits to `sprint-plan.md` before the execution
sprint kicks off; for this PLAN-ONLY commit, the conditions and resolutions
are recorded here as the authoritative R2 decision record.)

### R2 verdict

**APPROVED.**

— VP-Eng (kgspin-demo-app)
