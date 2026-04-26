# Sprint Plan — phase-a-bundle-schema-notice (kgspin-demo-app)

**Branch:** `phase-a-bundle-schema-notice` (off `main` @ 951809a)
**Type:** Cross-repo doc-only acknowledgment.
**CTO assignment:** 2026-04-26 — "Phase A Notice Distribution — Cross-Repo Acknowledgment & Per-Sibling Actions"
**Canonical memo:** `kgspin-interface@main:docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice.md`
**Date:** 2026-04-26
**Push, do not merge.** CEO lands.

## TL;DR

Doc-only acknowledgment of the canonical Phase A notice. Single commit. Adds the
companion `…-received.md` at `docs/cross-repo/`. **Status will be `ACK ONLY`** —
the canonical memo's per-sibling row prescribes a FIXTURE UPDATE for "three
bundle.json fixtures under `kgspin-demo/.bundles/domains/`", but those artifacts
do not exist in this repo: `.bundles/` is gitignored runtime cache, no
`bundle.json` fixtures are checked in, and the single source-of-truth domain
file (`bundles/domains/financial-v22d.yaml`) carries no `bundle_schema_version`
field. Plan documents the discrepancy in the acknowledgment file and flags the
divergence for the kgspin-interface team's tracker sweep.

## Per-sibling row (verbatim from canonical memo)

> **kgspin-demo-app** | **NO ACTION** in code. **FIXTURE UPDATE** for the three
> bundle.json fixtures under `kgspin-demo/.bundles/domains/`. The demo-app
> proxies through bundle.json fixtures rather than importing the constant.

CTO sprint memo restates: *"FIXTURE UPDATE for the three bundle.json fixtures
under `kgspin-demo/.bundles/domains/`. Proxy-through-bundle.json pattern means
no code changes."*

## Discrepancy investigation — evidence

Searched the entire repo for the artifacts the per-sibling row prescribes:

| Claim in canonical memo | Repo reality |
|---|---|
| Path `kgspin-demo/.bundles/domains/` | No such path exists. Repo has no `kgspin-demo/` subdir. The closest match is `.bundles/` at repo root. |
| `.bundles/` contains checked-in bundle.json fixtures | `.bundles/` is **gitignored** (`.gitignore:140`: `# Runtime bundle cache (symlinked into the blueprint's compiled/ tree)`). It is the runtime symlink/install location for compiled artifacts produced by kgspin-blueprint's compiler — not a checked-in fixture tree. |
| Three bundle.json fixtures | `find . -name 'bundle.json'` returns zero results. `find . -name '*.json' -path '*domains*'` returns zero results. |
| Demo "proxies through bundle.json fixtures" | Confirmed at runtime — `demos/extraction/pipeline_common.py:49` and `src/kgspin_demo_app/api/server.py:126,196,344` resolve `KGEN_BUNDLES_DIR=.bundles` and walk `.bundles/domains/*/`. But that resolution is against runtime artifacts produced upstream, not git-tracked fixtures. |
| `bundle_schema_version` literal `"2.0"` somewhere editable | The single source-of-truth YAML at `bundles/domains/financial-v22d.yaml` has `domain_schema_version: 5.6.0` and `version: v22d` — neither is the bundle schema version the canonical memo bumps. No `bundle_schema_version` key exists in any tracked file in this repo. Grep for `"2.0"` / `'2.0'` / `bundle_schema_version` / `schema_version.*2` returned only an unrelated RSS feed test fixture (`tests/unit/test_yahoo_rss_client.py:20: <rss version="2.0">`). |

**Conclusion:** there is nothing in this repo's git-tracked tree to bump from
`"2.0"` → `"3.0"`. The compiled `bundle.json` artifacts the memo references
are produced by **kgspin-blueprint** (the writer side, called out separately in
the memo's own per-sibling row for that repo) and dropped into this repo's
gitignored `.bundles/` runtime tree on the developer/operator machine. The
writer-side bump is explicitly named as a next-sprint task in the canonical
memo, so once kgspin-blueprint stamps `"3.0"` on newly-compiled bundles, this
repo's runtime cache picks the new value up automatically — **zero git-tracked
churn here**.

## Status determination

CTO memo: *"Status: `ACTION COMPLETE` if you executed the fixture/doc updates
this sprint, or `ACK ONLY` if your row says NO ACTION."*

Strict reading: row says FIXTURE UPDATE → ACTION COMPLETE.
Honest reading: there are no fixtures to update → nothing executed → `ACK ONLY`
is the only non-misleading marker.

**Plan defaults to `ACK ONLY` with an explicit "Repo-state divergence" callout
in the acknowledgment file**, so the kgspin-interface team's tracker sweep sees
*why* and can amend the canonical row. Marking ACTION COMPLETE without any
diff would silently launder a stale prescription into "complete" status; the
right-shaped output for this repo is an honest `ACK ONLY` plus a one-paragraph
divergence note.

## Scope

In:
- New file `docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice-received.md` containing:
  - Cross-link to the canonical memo.
  - Verbatim copy of this repo's per-sibling action row.
  - **Repo-state divergence** section documenting the evidence above (compact form — three or four sentences plus the table from this plan).
  - Status: `ACK ONLY`.
  - Tracker hint: `receipt-recorded-here` per CTO directive.
  - Signature: Dev team — kgspin-demo-app — 2026-04-26.
- Branch `phase-a-bundle-schema-notice` pushed to `origin`, NOT merged.
- Sprint plan + dev report in `docs/sprints/phase-a-bundle-schema-notice/`.

Out (per CTO):
- No `bundle.py` edits (none would apply — this repo has no `bundle.py`).
- No writer-side schema-version bumps.
- No new sensitivity tests.
- No Phase B reader migration.
- No INSTALLATION work.
- No fictional fixture creation. If checked-in `bundle.json` fixtures don't exist, this sprint will not invent them just to bump a literal.

## Acknowledgment file — content outline

`docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice-received.md`

```markdown
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

The prescribed FIXTURE UPDATE has no applicable target in this repo's git
tree:

- `.bundles/` is gitignored runtime cache (`.gitignore:140` — "Runtime
  bundle cache (symlinked into the blueprint's compiled/ tree)").
- No `bundle.json` files are checked in anywhere in this repo (`find . -name
  bundle.json` returns zero).
- The single git-tracked domain bundle is `bundles/domains/financial-v22d.yaml`,
  a YAML source-of-truth with no `bundle_schema_version` field.
- The compiled `bundle.json` artifacts the memo refers to are produced by
  **kgspin-blueprint** (the writer side, also called out in the memo's
  per-sibling table) and land in this repo's runtime `.bundles/` tree on
  the operator machine. When kgspin-blueprint's writer-side bump ships
  next sprint, this repo's runtime cache picks up `"3.0"` automatically.

Net effect: no git-tracked artifacts in this repo carry the schema-version
literal, so there is nothing to bump here. Acknowledgment recorded;
canonical row may want amending in the next tracker sweep.

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
`kgspin-interface:docs/cross-repo/phase-a-migration-tracker.md` from this
repo. The tracker sweep should record this entry as
`receipt-recorded-here` and consider amending the per-sibling row to
reflect the gitignored-runtime-cache reality.

— Dev team, kgspin-demo-app — 2026-04-26
```

## Commit plan

Single commit on branch `phase-a-bundle-schema-notice`:

```
docs(cross-repo): record Phase A bundle-schema notice receipt (kgspin-demo-app)

Acknowledgment of canonical Phase A bundle-schema-additive notice
(kgspin-interface@main:docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice.md).

Status: ACK ONLY. Per-sibling row prescribes FIXTURE UPDATE for "three
bundle.json fixtures under kgspin-demo/.bundles/domains/", but .bundles/
is gitignored runtime cache and this repo carries no checked-in
bundle.json fixtures. The compiled bundle.json artifacts come from the
kgspin-blueprint writer-side bump (next-sprint task) and land in
.bundles/ on the operator machine — no git-tracked diff applies here.

Acknowledgment file documents the divergence for the canonical
tracker sweep.

No code changes. No fixture changes. No writer-side bump. No bundle.py
edits.

Co-Authored-By: Claude
```

Files touched:
- `docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice-received.md` (NEW)
- `docs/sprints/phase-a-bundle-schema-notice/sprint-plan.md` (NEW — this file)
- `docs/sprints/phase-a-bundle-schema-notice/dev-report.md` (NEW — written at end of sprint)

## Test plan

None required. Doc-only.

Verification before push:
- `git status` clean except the new files.
- `git diff --stat main` shows three adds, all under `docs/`.
- Acknowledgment file renders as valid markdown (no broken cross-link).
- No edits to `bundles/`, `src/`, `tests/`, `demos/`, or `benchmarks/`.

## Risks + mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| CEO/CTO reads `ACK ONLY` and disagrees — wants ACTION COMPLETE marker even though no fixtures exist | Med | Acknowledgment file's "Repo-state divergence" section makes the reasoning explicit. CEO can rewrite the status line in-place at land if a different marker is preferred — single-line edit. |
| Canonical memo intended a different artifact (e.g., the source YAML at `bundles/domains/financial-v22d.yaml`) and we missed it | Low | Searched exhaustively: no `bundle_schema_version` literal anywhere in the tracked tree. The YAML's `domain_schema_version: 5.6.0` is a different field (per-domain schema, not bundle schema). If the CEO redirects, mechanical fix-up before push. |
| Runtime `.bundles/` consumers break when kgspin-blueprint flips writer to `"3.0"` (R-3.1 ERROR mode) | Low | Phase A is additive; the canonical memo's "Audit-your-bundles-now" section addresses this for operators. This repo carries no operator-bundle artifacts, so no audit responsibility lands here. |
| Tracker sweep in kgspin-interface doesn't pick up the divergence note | Low | The acknowledgment file's "Tracker hint: `receipt-recorded-here`" section + path are exactly what the canonical memo's instructions say to leave for the sweep. |

## Open items (best-effort assumptions, callable-out at CEO review)

1. **Status marker.** Plan defaults to `ACK ONLY`. Alternatives: (a) `ACTION COMPLETE` with the divergence note (compliant with strict reading of memo, misleading semantically), (b) custom marker like `ACK ONLY — divergence flagged`. CEO can redirect at review.
2. **Should we additionally stamp `bundle_schema_version: "3.0"` on the source YAML at `bundles/domains/financial-v22d.yaml`?** Plan says no — the canonical memo is explicit that the *value* lives in `kgspin-interface`'s `version.py` constant, and writer-side stamping (which is what would land schema versions on emitted compiled bundles) is a kgspin-blueprint next-sprint task. Adding the field to the source YAML would create drift with the writer's authoritative stamp. Default: leave the YAML untouched. CEO can redirect.
3. **Should we proactively edit `kgspin-interface`'s migration tracker from a sibling commit?** CTO memo: *"DON'T edit that file from your repo."* Plan respects that and only leaves the `receipt-recorded-here` hint in the local acknowledgment for the sweep.

## Completion criteria

- One new file on disk under branch `phase-a-bundle-schema-notice`:
  `docs/cross-repo/2026-04-26-phase-a-bundle-schema-additive-notice-received.md`.
- Sprint plan + dev report under `docs/sprints/phase-a-bundle-schema-notice/`.
- Single commit, conventional message, `Co-Authored-By: Claude` trailer.
- Branch pushed to `origin/phase-a-bundle-schema-notice`. NOT merged.
- Internal VP-Eng / VP-Prod review pass on the plan; BLOCKER/MAJOR items addressed.

## VP review

Internal VP-Eng + VP-Prod reviews recorded in
`docs/sprints/phase-a-bundle-schema-notice/vp-review.md` (written next).

— Dev Team (kgspin-demo-app)
