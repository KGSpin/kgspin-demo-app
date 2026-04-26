# Sprint Plan — architecture-3yaml-rollout (kgspin-demo-app)

**Branch:** `architecture-3yaml-rollout` (off `main` @ 12f5e20)
**Type:** Cross-repo doc-only rollout. This repo's slice only.
**CTO assignment:** 2026-04-26 — "Cross-Repo ADR Rollout — 3-YAML Architecture (multi-repo, EXECUTE)"
**Date:** 2026-04-26
**Push, do not merge.** CEO lands.

## TL;DR

Doc-only. Single commit. Adds a one-page companion ADR cross-linking to the canonical 3-YAML architecture ADR in kgspin-interface, plus an Architecture references entry in CLAUDE.md. Names this repo's specific responsibility under the framework: **consumer of all three YAMLs at extract time; records the triple-hash in extraction provenance metadata.**

## Cross-repo dependency

| Artifact | Repo | Status this sprint | Notes |
|---|---|---|---|
| Canonical ADR `ADR-NNN-three-yaml-config-architecture.md` | kgspin-interface | Lands first (separate repo's slice) | kgspin-interface ADRs run 001–003 today; canonical will land as **ADR-004**. Companion in this repo cross-links by that filename. |
| Companion ADR (this sprint) | kgspin-demo-app | This sprint | Filename: `ADR-006-three-yaml-config-architecture-rollout.md` (next free in this repo; 001–005 already used). |
| CLAUDE.md cross-reference (this sprint) | kgspin-demo-app | This sprint | **CLAUDE.md does not currently exist in this repo** (every sister repo has one — see Risks). Plan creates a minimal CLAUDE.md whose body is exactly the "Architecture references" section the CTO specified. |

## Scope

In:
- New file `docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md` (~1–2 page companion note).
- New file `CLAUDE.md` containing the "Architecture references" section per CTO spec.
- Branch `architecture-3yaml-rollout` pushed to `origin`, NOT merged.

Out (per CTO):
- No code changes.
- No YAML schema changes.
- No bundle.py edits.
- No INSTALLATION implementation (Phase 2).
- No sensitivity tests.
- No new MutationType variants.
- Task 1 (canonical ADR in kgspin-interface) — different repo.
- Tasks for the other 6 repos — different teams.

## Companion ADR — content outline

`docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md`

```
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
```

## CLAUDE.md — content

Since no CLAUDE.md exists in this repo today, the file is created with exactly
the section the CTO specified:

```
# Architecture references

- ADR-004 (three-yaml-config-architecture, kgspin-interface): canonical
  reference for BUNDLE/PIPELINE/INSTALLATION/SECRETS/CONSTANT category
  framework. INSTALLATION is Phase 2 (not yet implemented).
- Companion: `docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md`
  (this repo's specific responsibility).
```

Nothing else. If a richer CLAUDE.md is wanted (sister repos have a multi-section
file), that's a follow-up sprint — out of scope per CTO's "no additional content"
direction.

## Commit plan

Single commit on branch `architecture-3yaml-rollout`:

```
docs(architecture): roll out 3-YAML config ADR companion (kgspin-demo-app)

Adds the kgspin-demo-app companion note for the cross-repo 3-YAML config
architecture (canonical: kgspin-interface ADR-004). Names this repo's
responsibility — consumer of all three YAMLs at extract time, records the
triple-hash in extraction provenance metadata — and the Phase 2 implications
when INSTALLATION ships.

Also adds CLAUDE.md with the standard "Architecture references" section
cross-linking to canonical + companion.

No code changes. No YAML schema changes. No metadata-schema edits.

Co-Authored-By: Claude
```

Files touched:
- `docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md` (NEW)
- `CLAUDE.md` (NEW)

## Test plan

None required. Doc-only.

Verification before push:
- `git status` clean except the two new files.
- `git diff --stat main` shows two adds, both under `docs/architecture/decisions/` and root.
- ADR file renders as valid markdown (no broken cross-link syntax).

## Risks + mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Canonical ADR lands at a different number than ADR-004 in kgspin-interface | Low | kgspin-interface's next free is unambiguously 004 (existing ADRs are 001–003). If the canonical lands at a different NNN, single-line edit on `ADR-006-...md` and `CLAUDE.md` before push. |
| CLAUDE.md absence is intentional in this repo (e.g., would conflict with another bootstrap effort) | Low | If true, drop CLAUDE.md from the commit and inline the cross-reference in `README.md` instead. CEO can redirect at review. |
| Companion ADR landing before canonical confuses readers | Low | CTO's "coordinate so canonical lands first" directive — push order under CEO control. Branch is push-not-merge. |
| Drift on this repo's responsibility wording vs. other companions | Low | Wording lifted verbatim from CTO assignment table row for kgspin-demo-app. |

## Completion criteria

- Two new files on disk under branch `architecture-3yaml-rollout`.
- Single commit, conventional message, `Co-Authored-By: Claude` trailer.
- Branch pushed to `origin/architecture-3yaml-rollout`. NOT merged.
- Dev report at `docs/sprints/architecture-3yaml-rollout/dev-report.md` summarizing the above.
- Signal: **DEV_REPORT_READY** to the CTO once branch is on remote.

## Open items (best-effort assumptions, callable-out at CEO review)

1. **CLAUDE.md creation.** This repo currently has no CLAUDE.md. Default plan creates a minimal one (Architecture references section only). Alternative: skip Task 3 in this repo and inline the cross-link into `README.md`. Plan defaults to creation; CEO can redirect.
2. **Canonical ADR number.** Plan hard-codes `ADR-004` for the kgspin-interface canonical. If kgspin-interface lands at a different NNN, mechanical fix-up before push.
