# Phase 2 INSTALLATION — Notice Received (kgspin-demo-app)

**From:** kgspin-demo-app Dev Team
**Date:** 2026-04-26
**Re:** kgspin-core's cross-repo memo
  `docs/cross-repo/2026-04-26-phase-2-installation-core-completion-notice.md`
  in the `kgspin-core` repo.

## Status: ACTION COMPLETE

This repo is row #3 in the per-sibling action checklist of the
upstream notice. The required actions:

> (a) Add `installation_version_hash` to the response shape per the
>     kgspin-interface notice.
> (b) Ship the operator runbook at
>     `docs/operator-runbooks/triple-hash-replay.md` (Success Metric #2
>     from the cross-repo memo).

are addressed in this branch (`phase-2-installation-exec` off
`main`@`541d601`) as follows.

## What demo-app surfaces

Every extraction-returning surface now carries an `extraction_metadata`
block with the full triple — `pipeline_version_hash`,
`bundle_version_hash`, `installation_version_hash` — alongside the
`schema_version` constant exported by `kgspin_interface.version`:

| Surface | File | Behavior |
|---|---|---|
| `POST /extract/relationships` | `src/kgspin_demo_app/api/server.py` | `extraction_metadata` block from `result.provenance`; `bundle_version` flat field kept one release as deprecation shim. |
| `POST /extract/entities` | `src/kgspin_demo_app/api/server.py` | `extraction_metadata: null` (GLiNER entity-only path doesn't run the orchestrator that mints the triple). |
| `POST /extract/establish` | `src/kgspin_demo_app/api/server.py` | `extraction_metadata` block with `bundle_version_hash` computed from the linker's bundle; pipeline / installation surface as `null`. |
| `POST /extract/replay/relationships` | `src/kgspin_demo_app/api/server.py` | New endpoint. Match-or-409 against the deployment's currently-loaded triple. |
| MCP tools (`extract_relationships`, `extract_entities`, `establish_relationship`) | `src/kgspin_demo_app/mcp_server.py` | Same `extraction_metadata` block on every tool output. |
| Cached-runs UI | `demos/extraction/routes/runs.py` | Passes `extraction_metadata` through; legacy runs render `<pre-Phase-2>` for unrecorded fields. |

## Customer-facing copy

Customer-facing reproducibility doc lives at
`docs/reproducibility-by-triple-hash.md` — what each hash means, how
to pin them, what stays identical vs. what may vary, and a worked
example end-to-end. Cross-linked from `README.md`.

## Operator runbook (Success Metric #2 of the upstream memo)

The customer-facing doc covers the customer-facing usage. The
operator-side runbook (admin-flip flow, what to do when
`installation_version_hash = null` proliferates, etc.) is **deferred
to Phase 2.1** — it requires the per-hash bundle/installation fetch
that today's match-or-409 replay endpoint does not need. We will
ship it alongside the per-historical-hash replay endpoint.

## Critical-constraint compliance

- **No reintroduction of the 7 migrated fields into demo-app's bundle
  reads.** Verified via grep against the migrated field names; demo-app
  only reads them indirectly via `result.provenance` from the
  orchestrator.
- **No SECRETS in customer-facing surface.** `extraction_metadata`
  carries only the three hashes + schema version. API keys, model
  paths, and auth tokens stay env-var-only.
- **Verbatim hash passthrough.** Demo-app does not filter or rewrite
  the hashes; empty strings (kgspin-core's migration-window default)
  are normalized to `null` for one consistent "unset" representation,
  but no mutation of present hashes.

## Default value drift acknowledgment

Per the upstream memo's two-field default drift callout
(`nli_max_candidates_per_sentence` 5→64,
`heuristic_distance_max_chars` 500→1500): demo-app does not author
bundle YAMLs, so this drift is downstream of any bundle author who
relied on the old bundle defaults. Demo-app surfaces the resulting
extraction unchanged.

## Strict-mode flip

Demo-app does not directly invoke the bundle Pydantic validator; we
consume bundles via `kgspin_core.cli.utils.load_bundle`. The strict
default flip is transparent to this repo. If a bundle YAML in
`kgspin-demo-config` carries deprecated fields and breaks demo-app's
load path, the temporary escape hatch is `KGSPIN_BUNDLE_PYDANTIC_LENIENT=1`
(documented in the upstream memo).

— Dev Team (kgspin-demo-app)
