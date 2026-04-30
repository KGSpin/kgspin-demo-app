# PRD-004 v5 Phase 5A Fixup — Manual UI Smoke

**Date:** 2026-04-30
**Branch:** `sprint-prd-004-v5-phase-5a-fixup-20260430`
**Run with:** `uv run python demos/extraction/demo_compare.py`,
then `http://localhost:8080/compare`.

The fixup sprint moves PRD-004 v5 Scenario A/B from a `/compare` standalone
region into each per-graph modal's Why tab. This checklist verifies the
new placement, the scaffold contract, and the deprecated-Agentic-Q&A
removals — without exercising LLM round-trips (which require
`KGSPIN_LIVE_LLM=1` + valid Gemini Flash creds).

---

## Pre-flight

- [ ] Server starts on `:8080` without `ImportError` or
      `ReferenceError` during init.
- [ ] `/compare` loads; tab strip shows: **Compare**, **Intelligence**,
      **Impact**. (No "Agentic" tab.)

## Compare tab — Agentic Q&A Comparison block is gone

- [ ] Below the slot grid, the only action buttons are **Run** and
      **Analyze** (no "Run Q&A" button).
- [ ] No `#slot-qa-section` ("Agentic Q&A Comparison") block visible.
- [ ] Loading 2 graphs enables Analyze (Run is always enabled).

## Impact tab — Agentic Q&A sub-tab is gone

- [ ] Impact sub-tab strip shows: **Lineage**, **Reproducibility**,
      **Auditability** (no "Agentic Q&A").
- [ ] Welcome copy no longer mentions "agentic task quality."

## Page-level Why-this-matters block is gone

- [ ] No top-of-page "Why This Matters" Q&A flow on `/compare`. (The
      per-graph successor lives in the modal Why tab.)

## Per-graph modal — Why tab placement check (financial)

1. On Compare tab, populate at least one slot with a financial graph
   (e.g. ticker `AAPL` + `KGSpin Default`).
2. Click the slot to open the modal.
3. Click the **Why** tab inside the modal.

- [ ] **Graph-identity header** at the top shows the slot's pipeline
      label, bundle, and ticker (`Apple Inc. — KGSpin Default —
      financial-default`).
- [ ] Below the header, **two sub-tabs** are visible: **Single-shot
      Q&A** (default-active) and **Multi-hop scenarios**.
- [ ] **Single-shot Q&A** sub-tab shows:
  - Scenario picker with 5 financial scenario IDs.
  - "Or type your own…" free-text input.
  - **Run** + **Analyze Results** buttons (Analyze disabled until Run
    completes).
  - Placeholder result card: "Run a scenario to see Dense RAG vs
    GraphRAG side-by-side."
- [ ] **Multi-hop scenarios** sub-tab shows:
  - Picker with 5 financial templated entries (no clinical entries on
    a financial slot).
  - Each entry's preview shows the resolved question (e.g. `Apple Inc.`
    substituted for `{company}`).
  - **Run** + **Analyze Results** buttons.

## Per-graph modal — Why tab placement check (clinical)

1. Switch domain to **Clinical** (top-right toggle).
2. Populate a clinical slot (e.g. trial `NCT00174785` / `JNJ-Stelara`).
3. Open the modal → Why tab.

- [ ] Graph-identity header reads the clinical slot's labels (no
      "select ticker" copy).
- [ ] **Multi-hop scenarios** sub-tab picker shows 5 clinical entries:
  1 ready (`stelara_adverse_events_cohort_v5`) + 4 scaffolds suffixed
  with `(TBD)`.
- [ ] Selecting a `(TBD)` scaffold disables the **Run** button and
      shows helper text: "Scenario design pending — clinical v0 in
      progress."
- [ ] Selecting the ready Stelara scenario enables Run; the resolved
      question contains "Stelara", "Centocor", and "NCT00174785".

## Modal-domain lock (mid-session domain switch)

1. With a clinical slot's modal Why tab open, flip the domain toggle
   back to **Financial**.

- [ ] The open modal's picker **does not refresh** — clinical scenarios
      stay listed, locked to the slot's domain at open time.
- [ ] Closing and re-opening the modal on a *new* (financial) slot
      shows the financial picker.

## Sub-tab toggle

- [ ] Clicking **Multi-hop scenarios** swaps content, sets active state
      on the sub-tab button, deactivates Single-shot Q&A.
- [ ] Clicking **Single-shot Q&A** restores the original sub-tab.
- [ ] No console warnings or errors during toggling.

## Console check

- [ ] No `ReferenceError: startImpact is not defined`.
- [ ] No `ReferenceError: triggerModalWhyThisMatters is not defined`.
- [ ] No `ReferenceError: runSlotQA is not defined`.
- [ ] No `ReferenceError: renderCachedQARun is not defined`.

## Backend alive-route guard (CLI)

- [ ] `curl -i 'http://localhost:8080/api/why-this-matters/AAPL?domain=financial'`
      returns a non-404 (200 with structured-error or 4xx — anything but
      404). VP-Prod #4 multihop posture.
- [ ] `curl -i -X POST 'http://localhost:8080/api/compare-qa/AAPL'
      -H 'Content-Type: application/json'
      -d '{"graphs":[],"domain":"financial"}'` returns non-404.
- [ ] `curl -i -X POST 'http://localhost:8080/api/multihop/run'
      -H 'Content-Type: application/json'
      -d '{"doc_id":"ZZZZ","scenario_id":"x","slot_pipelines":[null,null,null]}'`
      returns non-404.

## Visual regression spot-checks (F13)

- [ ] **Scenario-B paper badge** (in Multi-hop sub-tab): neutral grey-blue
      `#5B9FE6` on `#1a1a3e` background, `cursor: help`. (Was green/gold
      accent in Phase 5A.)
- [ ] **Modal Why tab** vertical layout fits in the modal viewport
      without inducing a second scrollbar.
- [ ] No "arXiv:2509.22009" wording anywhere in the UI.

---

If every box ticks, the fixup sprint is operator-ready. The dev-report
in this directory has the full deliverable map, test matrix, and
deferral list.
