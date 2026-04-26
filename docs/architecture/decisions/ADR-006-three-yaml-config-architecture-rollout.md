# ADR-006: Three-YAML Config Architecture — Rollout in kgspin-demo-app

**Status:** Accepted
**Date:** 2026-04-26
**Deciders:** CTO (canonical), Dev Team (rollout)
**Canonical:** kgspin-interface ADR-004 (`three-yaml-config-architecture`)
**Related:** ADR-003 (fetcher ABC + admin registry), ADR-005 (llm_model registry kind);
  upstream PRD-031, PRD-005, kgspin-blueprint ADR-006, kgspin-blueprint ADR-028.

## Cross-link

This is the kgspin-demo-app companion to the canonical 3-YAML architecture ADR
landed in kgspin-interface as ADR-004. Read the canonical first; this note only
states what kgspin-demo-app is on the hook for.

## This repo's responsibility

kgspin-demo-app is the **end consumer** of all three YAMLs at extract time:
PIPELINE (kgspin-core/interface schema version + git commit), BUNDLE (per-domain
provenance_id + bundle version), and INSTALLATION (Phase 2; today held by
RUNTIME/ENV — env vars + CLI flags).

At extract start, the demo records the **triple-hash** in
`extraction_metadata`:
- `pipeline_version_hash`
- `bundle_version_hash`
- `installation_version_hash`  ← Phase 2; placeholder until kgspin-admin ships
  the `installation_config` resource.

Reproducibility-by-config-hash is the customer trust property. Demo's
provenance writers must carry the triple forward on every emitted fact.

## Phase 2 implications for this repo

**Status as of 2026-04-26: Phase 2 LANDED.** kgspin-interface shipped
`InstallationConfig` (`8af1afd`), kgspin-admin shipped the
`installation_config` resource type (`31e1736`), kgspin-core shipped the
triple-hash provenance machinery and 7-field read-site migration
(`788afe2`). This repo's surfacing slice landed on the
`phase-2-installation-exec` branch — see
`docs/sprints/phase-2-installation-plan/` for the plan + dev-report and
`docs/cross-repo/2026-04-26-phase-2-installation-notice-received.md` for
the cross-repo acknowledgment.

What this repo now does:
- `api/server.py` — every extraction-returning response carries an
  `extraction_metadata` block (Pydantic model `ExtractionMetadata`) with
  `schema_version`, `pipeline_version_hash`, `bundle_version_hash`,
  `installation_version_hash`. Field order is pinned for stable JSON
  serialization. `RelationshipResponse` keeps the flat `bundle_version`
  field for one release window as a deprecation shim.
- `mcp_server.py` — same triple-hash block on every MCP tool output.
- `demos/extraction/routes/runs.py` — cached-runs UI passes
  `extraction_metadata` through; legacy runs render `<pre-Phase-2>` for
  fields not recorded at extraction time.
- `POST /extract/replay/relationships` — match-or-409 replay endpoint:
  verifies the request's triple matches the deployment's currently-loaded
  triple, runs extraction on match, returns 409 with `installed` triple
  on mismatch.

Customer-facing reproducibility doc lives at
`docs/reproducibility-by-triple-hash.md`.

### Pre-Phase-2 wording (kept for history)

Until kgspin-interface lands `InstallationConfig` and kgspin-admin ships the
`installation_config` endpoints (register / version-bump / retrieve-by-hash):
- New fields added to demo runtime that *would* be INSTALLATION (resource
  caps, per-installation policy knobs, performance criteria) live as env
  vars or CLI flags. Categorize per the three rubrics in the canonical ADR
  before adding.
- `extraction_metadata.installation_version_hash` lands as `None` (or a
  placeholder constant); the schema slot is reserved.
- When Phase 2 fires: mechanical migration — demo reads installation hash
  from admin and stamps it on every extract alongside the existing pipeline
  + bundle hashes.

## Sensitivity-test mandate

The Wave 1B mandate (sensitivity tests for ALL fields) applies regardless of
category. Demo is not on the hook for authoring those tests, but must NOT
regress them when wiring new metadata into provenance.

## Out-of-scope (no-op this sprint)

No code changes. No new env vars. No metadata-schema edits. This is a
documentation rollout only; implementation lands when Phase 2 fires.

(See "Status as of 2026-04-26" above — Phase 2 has landed; this section
describes the original rollout-ADR scope.)

— Dev Team (kgspin-demo-app)
