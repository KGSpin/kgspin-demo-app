# VP-Eng Review (Phase 3) — phase-2-installation-exec (kgspin-demo-app)

**Reviewer:** VP-Eng (kgspin-demo-app)
**Sprint:** phase-2-installation-exec
**Date:** 2026-04-26
**Reviewing:** `docs/sprints/phase-2-installation-plan/dev-report.md`
  + branch contents on `phase-2-installation-exec`

## Lens for this Phase 3 review

The Phase 1 (planning) review I authored gave a CONDITIONALLY APPROVED →
APPROVED verdict contingent on R2 resolutions of C1–C5. This Phase 3
review evaluates whether the *executed* sprint actually addresses each
of those R2 commitments and whether the implementation choices that
diverge from the plan are well-reasoned.

## R2 commitment audit

**C1 — Replay-endpoint admin/core boundary** (R2 said: demo-app sends
hash-only to core; core fetches the YAML body from admin).

Status in execution: **N/A this sprint, deferred to 2.1.** The
match-or-409 endpoint shipping today does not fetch by hash — it
matches against the deployment's currently-loaded triple. The
hash-only-to-core boundary the R2 spec referred to applies to the
per-historical-hash variant deferred to Phase 2.1. Dev-report's
"Replay deferral — rationale and design for Phase 2.1" section
correctly identifies which kgspin-core surface (the constructor
kwargs already exist) and which admin surface (a new
`GET /resource/bundle/<hash>` admin client method) Phase 2.1 needs.
Dev did not silently expand demo-app's admin dependency. Good.

**C2 — Round-trip determinism assertion** (R2 said: capture triple
from a first run, re-run with explicit pins, assert property-identical
entity / relationship sets).

Status in execution: **deliberately not added; flagged in dev-report
for VP-Eng review.** Dev's reasoning: the byte-identical-under-pinned-
triples property is owned by kgspin-core's test suite, where the
orchestrator's triple-hash machinery is verified end-to-end. Adding a
duplicate test in demo-app would require spinning up a heavyweight
extraction in CI for marginal coverage.

**My evaluation:** I accept the deferral with one caveat. The test I
wanted at R2 was *demo-app-side property*: confirm that the demo-app
endpoint *passes the pin through* faithfully, not that the platform
under it is deterministic. The match-or-409 design satisfies this
indirectly — if demo-app ever filtered or mutated the triple en route
to / from core, the 409 path would fire spuriously and the smoke tests
(`test_replay_endpoint_returns_200_on_triple_match`) would fail. That
is a pin-passthrough test in disguise. Combined with the explicit
verbatim-passthrough smoke test
(`test_build_extraction_metadata_passes_through_real_hashes`), the
property I asked for in R2 is covered. **Accepted.**

For Phase 2.1, when the per-historical-hash replay endpoint lands and
demo-app starts constructing extractors with pinned bundles fetched
from admin, the round-trip assertion becomes load-bearing for
demo-app's own correctness — at that point I will require it.

**C3 — Effort estimate buffer** (R2 said: 6–7 dev-days total with a
1-day cross-repo integration buffer).

Status in execution: dev landed the slice in one working session.
The sprint plan's effort estimate was pessimistic — typical for a
new architectural pattern. Not a concern; underrun is healthy.

**C4 — Pydantic field-order pin** (R2 said: declaration order
`schema_version, pipeline_version_hash, bundle_version_hash,
installation_version_hash`, smoke-tested).

Status in execution: **delivered.** `ExtractionMetadata.model_fields`
matches the declared order; smoke test
`test_extraction_metadata_field_order_pinned` asserts the JSON dump
key order. **Accepted.**

**C5 — "Surfacing-only is not a failure" copy** (R2 said: dev-report
should explicitly note that surfacing-only ships the verification half
of the GTM property even if replay slips).

Status in execution: replay shipped, so this no longer applies in the
"slip" sense. Dev-report's "Replay deferral — rationale and design for
Phase 2.1" section honestly delineates what is and isn't pinnable
today: Property V is fully delivered, Property P is delivered against
the deployment's currently-loaded triple, and the per-historical-hash
variant is the Phase 2.1 follow-up. That framing is the spirit of C5.
**Accepted.**

## Implementation observations

### Side-effect cleanup of stale imports

`api/server.py` and `mcp_server.py` had stale `..execution.extractor`-
style imports that would have `ImportError`-d on first load. Dev
fixed them (mechanical rename to `kgspin_core.execution.extractor`)
as part of this sprint because the surfacing work touches these files
and the new tests would otherwise be unrunnable.

This is a defensible scope decision — the alternative would be to
leave the files in a state where the new triple-hash code cannot be
tested. I would have preferred a separate "fix stale imports" prep
PR (cleaner blame), but that is a process preference, not a
correctness concern. The fix is mechanical, contained to two import
blocks, and surfaced explicitly in the dev-report. **Accepted; flag
as a process note for next sprint.**

### Cached-runs UI fallback semantics

The `<pre-Phase-2>` placeholder for legacy runs is the right call.
Dev also normalized empty-string drift cases (`""` in provenance)
to the same placeholder, which means the UI shows one consistent
"unrecorded" signal regardless of whether the run was captured pre-
Phase-2 or captured during the kgspin-core migration window with
empty-string defaults. This is a small but correct call —
prevents customer confusion across the cohort that overlaps the
two states.

### MCP server's `establish_relationship` metadata fallback

Dev computes `bundle_version_hash` from `bundle.model_dump()` for the
establish endpoint, since the linker doesn't carry an extractor's
frozen triple. This is correct (the bundle is in scope; the
pipeline / installation hashes are not), and it's wrapped in a
try/except that falls back to `None` on any exception. The
try/except is broad (catches `Exception`), but the alternative is
plumbing a typed exception class through `kgspin_core.provenance`,
which is overkill for a defensive None-fallback on a metadata
field. **Accepted.**

## Risks / follow-ups

| # | Item | Severity | Owner | When |
|---|---|---|---|---|
| 1 | Per-historical-hash replay endpoint (Phase 2.1) | M | demo-app + kgspin-core (admin client method) | Next sprint |
| 2 | Operator runbook (`docs/operator-runbooks/triple-hash-replay.md`) | L | demo-app | Next sprint, alongside #1 |
| 3 | Pre-existing 4 test failures in `test_pipeline_config_ref_dispatch.py` (bundle schema 2.0 vs 3.0) | M | kgspin-blueprint | Cross-repo ticket; not blocking this sprint |
| 4 | Stale-import cleanup process: should "fix infra to make new test runnable" be its own commit? | L | demo-app | Process note |

None of these are blockers for this sprint.

## Verdict

**APPROVED.**

All R2 commitments from Phase 1 are addressed (or deferred with
honest reasoning). The match-or-409 replay design is forward-
compatible with the Phase 2.1 per-historical-hash variant. Critical
constraints (no field reintroduction, no SECRETS leak, verbatim
passthrough) are verified. Test coverage is appropriate for the
demo-app slice (15 new tests; the upstream kgspin-core suite owns the
end-to-end determinism property). Pre-existing failures in
`test_pipeline_config_ref_dispatch.py` are documented as out of scope.

— VP-Eng (kgspin-demo-app)
