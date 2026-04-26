# VP-Prod Review (Phase 3) — phase-2-installation-exec (kgspin-demo-app)

**Reviewer:** VP-Prod (kgspin-demo-app)
**Sprint:** phase-2-installation-exec
**Date:** 2026-04-26
**Reviewing:** `docs/sprints/phase-2-installation-plan/dev-report.md`,
  `docs/reproducibility-by-triple-hash.md`, replay endpoint contract.

## Lens for this Phase 3 review

The Phase 1 (planning) review I authored framed this sprint around two
operationally distinct customer properties:

- **Property V — verifiable identity:** two extractions can be compared
  by triple-hash; matching triples mean identical config.
- **Property P — pinnable reproduction:** customer hands the platform a
  triple + a doc, platform re-runs with that triple pinned.

V is necessary; P is what the customer demos to *their* board. Both
are needed for the GTM property. This Phase 3 review evaluates whether
the executed sprint actually delivers V and the *replay-against-today's-
deployment* slice of P that the dev-report ships.

## R2 commitment audit

**C1 — Customer-facing names** (R2 said: "pipeline version" / "domain
bundle version" / "deployment configuration version" in the doc; JSON
keys stay engineer-named with OpenAPI descriptions carrying the plain
language).

Status in execution: **delivered.** `docs/reproducibility-by-triple-hash.md`
section "The three hashes" has the table mapping wire names to
customer-facing names. Pydantic field declarations on
`ExtractionMetadata` carry the `description=` kwarg with the plain-
language version, so OpenAPI / `/docs` will surface them. **Accepted.**

**C2 — Worked example end-to-end** (R2 said: 4 numbered steps from
POST extract → capture triple → POST replay → diff, runnable against
the demo deployment).

Status in execution: **delivered.** Doc's "Worked example — verify and
replay" section has the full curl/jq/diff workflow. I ran it against
the local match-or-409 endpoint (via the test fixtures' stub) and the
sequence is correct. **Accepted.**

**C3 — Comms decision on slip** (R2 said: if replay slips, GTM
announcement says "verifiable triple-hash provenance shipped today;
one-call replay endpoint in the following sprint").

Status in execution: replay shipped (in match-or-409 form); the
per-historical-hash variant is deferred with documented design. The
comms decision is no longer "V-only ships, P slips" — it's now "V
ships fully, P ships against today's deployment, P-against-historical
ships in 2.1." That is a *better* posture than my R2 contingency
planned for: customers get the immediate "demand a rerun" capability
today, and the historical-pin capability follows. I'll update the GTM
draft to match. **Accepted.**

**C4 — "What stays identical, what may vary"** (R2 said: doc must set
expectations on confidence-score wobble at the 4th decimal; identical
entity / relationship sets, etc.).

Status in execution: **delivered.** Doc's "What stays identical, what
may vary" section has the table. Honest about GPU floating-point
non-determinism even with all three hashes pinned. **Accepted.**

**C5 — Endpoint URL choice** (R2 said: keep
`POST /extract/replay/relationships`; unified `/extract/replay` is a
follow-up).

Status in execution: **delivered.** Endpoint is at
`POST /extract/replay/relationships`. Unified endpoint deferred. **Accepted.**

## Customer-facing property assessment

### Property V — verifiable identity

**Delivered fully.** Every extraction-returning surface (3 API
endpoints + 3 MCP tools + 4 cached-runs UI routes) carries the
`extraction_metadata` block. A customer who receives two extractions
can compare their triples and prove identical platform config. The
schema is stable (field order pinned by smoke test; `schema_version`
field present so future bumps are detectable).

This is the headline GTM win and it ships unconditionally today.

### Property P — pinnable reproduction

**Partially delivered today; full delivery in Phase 2.1.** The
match-or-409 replay endpoint shipped today gives the customer:

- **The "demand a rerun" capability.** Customer pins a triple they
  captured from a prior extract; if the deployment is still on that
  triple, it reruns and reproduces (modulo declared model
  non-determinism). This is the most common customer ask: "show me
  this exact extraction wasn't a fluke."
- **The "deployment-version visibility" capability.** On 409 the
  customer gets the deployment's installed triple in the response, so
  they immediately know what version this deployment is on without a
  separate ops query. That is operationally useful even when the
  replay itself fails.

What the customer cannot do today:

- **Replay against a historical triple after a redeploy.** If
  kgspin-core's git commit changed (pipeline hash flipped) or the
  bundle version was rolled, the customer's old triple yields a 409
  forever. Phase 2.1 ships the per-historical-hash fetch that
  reconstructs the historical deployment in-process for the duration
  of one request.

### Is this acceptable for the GTM announcement?

**Yes.** The match-or-409 endpoint is the *honest* shape of "we
reproduce extractions on demand": it succeeds when reproduction is
genuinely possible (configs still loaded), refuses honestly when it is
not (config has rolled), and tells the customer exactly what version
they would need to roll back to. That is more credible than a
historical-fetch endpoint that silently reconstructs an arbitrary past
deployment from disk — that path has audit-trail failure modes (what
if the historical bundle was deleted? what if the historical
installation was redacted for compliance?) that the simpler
match-or-409 endpoint sidesteps.

The Phase 2.1 follow-up is the *enrichment*, not the *correction*, of
today's shape.

## Doc quality

`docs/reproducibility-by-triple-hash.md` reads like a customer-facing
doc: terse, end-to-end runnable, no unexplained jargon. The
"When `installation_version_hash` is `None`" section is specifically
the kind of operational honesty that customers value — most vendors
omit failure modes from customer copy.

One nit: the "What stays identical, what may vary" table mentions GPU
floating-point variability without specifying that the demo deployment
is CPU-only. Most customer reproduction attempts will be on the demo
deployment (CPU), where the floating-point story is more deterministic.
This is a doc clarity nit, not a sprint-blocker. Flagging for the
copy-editing pass.

## Cross-repo acknowledgment

`docs/cross-repo/2026-04-26-phase-2-installation-notice-received.md`
correctly cross-links the upstream kgspin-core memo, itemizes which
surfaces carry the triple, and acknowledges the operator runbook
deferral. The honesty about "operator runbook deferred to Phase 2.1"
is correct — today's match-or-409 doesn't need it; the runbook only
becomes load-bearing when admin pointer mutations enter the operator
workflow (which is the per-historical-hash replay variant's
prerequisite).

## Risks / follow-ups

| # | Item | Customer impact | When |
|---|---|---|---|
| 1 | Per-historical-hash replay (Phase 2.1) | Customers can't replay against deployments their organization has rolled past. Today: 409 with installed triple; Phase 2.1: 200 with reconstructed historical deployment. | Next sprint |
| 2 | Operator runbook | Operators hand-roll the admin-flip flow; documented in the upstream memo, not in demo-app. | Next sprint, alongside #1 |
| 3 | "Worked example" copy uses GPU floating-point caveat for a CPU-only demo | Minor reader confusion; not a correctness issue. | Copy-editing pass |
| 4 | Unified `/extract/replay` endpoint | Three replay endpoints arrive in series instead of one; surface bloat. | Phase 2.2 or later |

None are GTM-blockers.

## Explicit answer to the original question

> Does Phase 2 deliver the customer-facing reproducibility property?

**Yes.** Property V (verifiable identity) is delivered unconditionally
today. Property P (pinnable reproduction) is delivered against the
deployment's currently-loaded triple today, and against arbitrary
historical triples in Phase 2.1. The GTM headline ("we reproduce
your extractions on demand") is fully supportable today; the longer-
tail historical-replay claim follows in 2.1.

> Is the triple-hash actually pinnable?

**Yes.** Today: pinning happens in two places — admin owns the active-
version pointer for installation; bundles are addressed by hash;
pipeline is addressed by the loaded core's git commit. All three are
immutable once landed. The replay endpoint passes the triple through
to a fresh-per-request extractor, and verifies the result triple
matches the request triple before returning a 200. Modulo declared
model non-determinism (documented in the customer doc), pinned
triples reproduce extractions.

## Verdict

**APPROVED.**

— VP-Prod (kgspin-demo-app)
