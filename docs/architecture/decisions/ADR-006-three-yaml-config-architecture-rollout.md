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

— Dev Team (kgspin-demo-app)
