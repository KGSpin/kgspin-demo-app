# VP-Prod Review — phase-2-installation-plan (kgspin-demo-app)

**Reviewer:** VP-Prod (kgspin-demo-app)
**Sprint:** phase-2-installation-plan
**Date:** 2026-04-26

CTO asked me to specifically address: **does Phase 2 deliver the customer-
facing reproducibility property? Is the triple-hash actually pinnable?**
That's the lens for this review.

## R1

### The customer-facing claim, restated

A prospect's question is, paraphrased: *"If I get a knowledge-graph
extraction from your platform today, can I prove I'd get the same
extraction tomorrow — and can I demand the platform reproduce it?"*

That decomposes into two operationally distinct properties:

- **Verifiable identity (V).** Two extracts I receive can be compared:
  if their triple-hashes match, I know they came from identical
  PIPELINE + BUNDLE + INSTALLATION configs. (Matching triples ⇒ matching
  configs ⇒ deterministic results modulo model noise.)
- **Pinnable reproduction (P).** I can hand the platform a triple-hash
  and a doc, and the platform re-runs that exact extraction with those
  pinned configs.

V is necessary; P is what customers will actually demo to *their* board.
Both are needed for the GTM property.

### What this plan delivers

- **V: YES, fully.** Every extraction-returning surface (3 API endpoints,
  3 MCP tools, cached-runs UI) carries `extraction_metadata` with the
  three hashes. Customers can compare across runs, across deployments,
  across time. This is the core GTM win and it is delivered unconditionally.
- **P: YES, contingent.** The replay endpoint is scoped IN, contingent on
  kgspin-core exposing a per-request installation override on
  `KnowledgeGraphExtractor`. If core declines, P degrades to "customer
  pins by manually setting admin's active version pointer" — which is
  sound but operationally awkward (mutates global state for an
  installation, requires admin write privilege).

### Strengths from a product lens

- **Honest about contingency.** I appreciate that the plan calls out
  the replay-endpoint dependency on a core API change rather than
  burying it. We can plan customer-facing comms around either outcome.
- **Customer doc is in scope.** A *one-page* doc is exactly right for
  this property — long enough to give a sales-engineer something to
  point to, short enough that customers will actually read it.
- **Legacy data honesty.** `<pre-Phase-2>` placeholder for cached runs.
  The temptation to backfill fake hashes would have been bad; the plan
  resists it.
- **`None` semantics handled.** When admin is unreachable and core
  falls back, customers see `null` — not a fake hash, not a swallowed
  error. Doc explains it. That's the right product behavior.

### Conditions

**C1 (BLOCKER → resolved in R2).** The plan does not specify what the
customer-facing payload calls each field. `pipeline_version_hash` /
`bundle_version_hash` / `installation_version_hash` are engineer names.
Customers reading the doc and the API response need consistent,
plain-language labels. I want the doc to use customer-facing names
("pipeline version", "domain bundle version", "deployment configuration
version") and the JSON keys to keep their engineer names with a
short `description` field via Pydantic so OpenAPI surfaces the
plain-language version.

**C2 (BLOCKER → resolved in R2).** "Pinnable reproduction" must include
**a worked customer-facing example** in the doc. Not just "POST this
endpoint" — an end-to-end walkthrough: extract → capture triple →
re-run with triple → compare. If the reader can't follow it, the
property doesn't exist for the customer even if it works in the code.

**C3 (MAJOR → resolved in R2).** The plan's "Minimum acceptable outcome"
implication (per VP-Eng's C5) — surfacing without replay — is *not* a
GTM-equivalent fallback. Surfacing alone delivers V but not P. From a
product-message standpoint, P is what differentiates us from "we log
versions in our metrics, trust us." If replay slips, we need a comms
plan: do we delay the GTM announcement, or announce V now and P next
sprint? Plan should name this decision explicitly so it doesn't get
made in a hallway.

**C4 (MAJOR → resolved in R2).** The doc must address **what
non-determinism the customer should expect even with all three hashes
pinned**. Models are non-deterministic at low decimals. If a customer
re-runs and gets confidence 0.8732 vs. 0.8731, they will (rightly)
ask whether the property is broken. The doc must set the expectation:
*"With identical triples, entity and relationship sets are identical;
confidence scores may vary at the 4th decimal due to model
sampling."* This belongs in the doc, not just the test suite.

**C5 (MINOR → resolved in R2).** The replay endpoint URL
`POST /extract/replay/relationships` is fine but customers will want a
generic `POST /extract/replay` that handles entities, relationships,
and establish in one shape. Plan should at least *consider* the unified
shape and pick deliberately.

### R1 verdict

**CONDITIONALLY APPROVED.** Reproducibility property is real and
well-scoped. C1–C4 must be resolved before doc draft ships; C5 is a
design call.

---

## R2 (post-conditions)

Plan author addresses:

- **C1.** Customer-facing names locked: "pipeline version" /
  "domain bundle version" / "deployment configuration version". JSON
  keys keep engineer names; OpenAPI descriptions carry the plain
  language. Doc uses plain language; API reference uses both.
- **C2.** Doc gets a "Worked example" section: 4 numbered steps from
  POST extract → capture triple → POST replay → diff. ~20 lines of
  curl/JSON, end-to-end runnable against the demo deployment.
- **C3.** Comms decision named: if replay endpoint slips, GTM
  announcement says "verifiable triple-hash provenance shipped today;
  one-call replay endpoint in the following sprint." V on its own is
  still a category-leader claim; we're not delaying.
- **C4.** Doc gets a "What stays identical, what may vary" subsection.
  Identical: entity set (text + type), relationship set (subject +
  predicate + object), bundle version. May vary: confidence scores at
  the 4th decimal, processing-time-ms. Customer can reproduce the
  *graph*; the *scores* are reproducible to a documented epsilon.
- **C5.** Plan keeps `POST /extract/replay/relationships` as the
  primary replay surface (mirrors the most-used `/extract/relationships`
  endpoint). A unified `POST /extract/replay` is a follow-up sprint;
  shipping three replay endpoints in parallel adds surface area for
  marginal benefit. Decision recorded.

### R2 verdict

**APPROVED.**

### Explicit answer to CTO's question

> Does Phase 2 deliver the customer-facing reproducibility property?

**Yes — partially today, fully if replay endpoint ships.** Property V
(verifiable triple identity across extracts) is delivered unconditionally
and is sufficient for the headline GTM claim. Property P (pinned
reproduction via replay endpoint) is delivered contingent on
kgspin-core's per-request installation override; if that core surface
slips, P arrives in the immediately-following sprint and we sequence
the customer announcement to match.

> Is the triple-hash actually pinnable?

**Yes.** Pinning happens in two places:
1. *In the platform:* admin owns the active version pointer for
   installation; bundles are addressed by hash; pipeline is addressed by
   the loaded core's git commit. All three are immutable once landed.
2. *Per-request (replay):* demo-app's replay endpoint passes the triple
   through to a fresh-per-request extractor instance, with installation
   pinned in-process (no admin pointer mutation, so no multi-tenant
   side effects).

The pinning property is real. Modulo model non-determinism (documented
to the customer), pinned triples reproduce extractions.

— VP-Prod (kgspin-demo-app)
