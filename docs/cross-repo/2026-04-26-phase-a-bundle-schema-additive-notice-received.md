# Phase A — Bundle Schema-Additive Notice — Receipt (kgspin-demo-app)

**Date received:** 2026-04-26
**Repo:** kgspin-demo-app
**Status:** `ACK ONLY` (see Repo-state divergence below)
**Tracker hint:** receipt-recorded-here

## Canonical memo

`kgspin-interface@main:docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice.md`

> Headline: 3.0 is the new floor for validated tunings. Phase A bumps the
> bundle schema version from `"2.0"` to `"3.0"`. The Python identifier
> `SCHEMA_V2` stays for import stability; only the value flips.

## Per-sibling action row (verbatim)

> kgspin-demo-app — NO ACTION in code. FIXTURE UPDATE for the three
> bundle.json fixtures under `kgspin-demo/.bundles/domains/`. The
> demo-app proxies through bundle.json fixtures rather than importing
> the constant.

## Repo-state divergence

The prescribed FIXTURE UPDATE has no applicable target in this repo's
git tree. Evidence:

| Claim in canonical memo | Repo reality |
|---|---|
| Path `kgspin-demo/.bundles/domains/` | No `kgspin-demo/` subdir exists; closest match is `.bundles/` at repo root. |
| `.bundles/` contains checked-in bundle.json fixtures | `.bundles/` is **gitignored** (`.gitignore:140` — "Runtime bundle cache (symlinked into the blueprint's compiled/ tree)"). It is the runtime symlink/install location for compiled artifacts produced by kgspin-blueprint, not a checked-in fixture tree. |
| Three `bundle.json` fixtures | `find . -name bundle.json` returns zero results in the tracked tree. |
| `bundle_schema_version` literal `"2.0"` somewhere editable | No `bundle_schema_version` key exists in any tracked file. The single git-tracked domain bundle (`bundles/domains/financial-v22d.yaml`) has `domain_schema_version: 5.6.0` and `version: v22d` — neither is the bundle schema version the canonical memo bumps. |

The compiled `bundle.json` artifacts the memo refers to are produced by
**kgspin-blueprint** (the writer side, also called out in the memo's
per-sibling table) and land in this repo's runtime `.bundles/` tree on
the operator machine. When kgspin-blueprint's writer-side bump ships
next sprint, this repo's runtime cache picks up `"3.0"` automatically —
zero git-tracked churn here.

## Status

`ACK ONLY` — no fixture or doc updates executed in this repo this sprint
because the prescribed targets do not exist in the git-tracked tree.

The functional Phase A change (R-3.0 WARN mode → R-3.1 ERROR mode) is
inherited transparently: when an operator-installed `.bundles/` tree
contains bundles compiled by a `"3.0"`-stamping blueprint, this repo's
runtime path validates against the new bounds with no code or fixture
diff here.

## Tracker

Per CTO direction: do not edit
`kgspin-interface:docs/cross-repo/phase-a-migration-tracker.md` from
this repo. The tracker sweep should record this entry as
`receipt-recorded-here` and consider amending the per-sibling row to
reflect the gitignored-runtime-cache reality of this repo.

— Dev team, kgspin-demo-app — 2026-04-26
