# PRD-004 v5 Phase 5A Fixup — Scope

**From:** Dev team
**To:** CTO
**Date:** 2026-04-30
**Branch:** `sprint-prd-004-v5-phase-5a-fixup-20260430` (off
`sprint-prd-004-v5-phase-5a-20260428` HEAD `8c3f3bb`)
**Predecessor sprint:** `sprint-prd-004-v5-phase-5a-20260428` (12 commits
landed; not yet merged to main)
**Revision:** **v2** — incorporates VP-Eng + VP-Prod review fixes and
CTO Socratic-clarification resolutions.

---

## What this fixup is

The Phase 5A delivery shipped the right backend (corpus + 5 services
+ 4 endpoints + 11 gold drafts) but **placed Scenarios A and B in the
wrong UI surface**. The corrected scope (CTO 2026-04-30 + VP review):

1. Scenarios live **inside the per-graph modal's Why tab** with two
   sub-tabs:
   - **Sub-tab 1: "Single-shot Q&A" (default).** Templated-scenario
     picker + a "type your own" free-text path; both feed
     `/api/scenario-a/run` (Dense RAG vs GraphRAG). Replaces the
     existing Why-tab Q&A flow.
   - **Sub-tab 2: "Multi-hop scenarios."** Same picker; runs through
     `/api/scenario-b/run` (agentic dense vs dual-channel; tool-agent
     placeholder).
   - The "increasing impact" story: same questions, simple retrieval
     vs decomposed multi-hop reasoning.
2. UX is **domain-agnostic** (same code paths). Each domain has its
   own 5-scenario config. **Modal locks to slot's domain at open
   time** (no mid-session re-fetch).
3. **No ticker picker.** Slot binding gives us the ticker;
   `document.body.dataset.currentDomain` tells us which input field
   to read it from.
4. **Agentic Q&A is removed** (frontend only; backend route stays
   alive, same posture as multihop).
5. The **"Analyze Results" button** + a **placeholder result card**
   under each sub-tab (per VP-Prod M1).

This fixup also delivers **4 clinical scaffolds** (placeholder
entries with `status: "scaffold"`; CTO replaces with real designs
once clinical v0 is correct) so the picker has a 5-fin / 5-clin set
on day one. The existing JNJ-Stelara hedge stays as the 5th
clinical entry.

## What is in scope

| ID | Deliverable | LOC est. |
|---|---|---|
| F1 | Move Scenario A + Scenario B markup into `#modal-why-content`. Replace the existing Why-tab Q&A flow. Sub-tab switch inside Why between "Scenario A — One-shot Q&A" and "Scenario B — Multi-hop". | ~250 HTML/CSS |
| F2 | Delete the `prd004-v5-region` block from the bottom of `/compare`. (Standalone view goes away.) | −50 HTML |
| F3 | Rewrite `scenario-a-runner.js` and `scenario-b-runner.js` as **modal-context-aware** runners. Ticker is auto-resolved via `expandedSlot` + `slotState[expandedSlot]` + `currentDomain` (`doc-id-input` / `trial-select` — same pattern as the existing `triggerModalWhyThisMatters`). No ticker picker. | ~−150 (deletion) + ~120 (rewrite) |
| F4 | **Domain-filter the Scenario B template picker** — show only scenarios whose `domain` matches the slot's domain. Clinical slot → 5 clinical templates only; financial slot → 5 fin templates only. | ~30 JS |
| F5 | **Add 4 clinical SCAFFOLD entries** to `multihop_scenarios_v5.yaml` with `status: "scaffold"` (CTO clarification 2026-04-30: clinical actual implementation pending v0). Picker shows them with `(TBD)` suffix; selecting one disables Run with helper copy "Scenario design pending — clinical v0 in progress." **Frontend-only disable** per CTO Conflict #3 (laptop demo, no public-web threat); no backend reject. Zero gold fixtures authored. | ~30 YAML + ~20 JS |
| F6 | **Always-visible "Analyze Results" button** under each scenario, with disabled state until Run completes. Currently `hidden` — change to `disabled`. Same wire path; visibility-only fix. | ~10 HTML/JS |
| F7 | Drop "arXiv:2509.22009" badge. Center pane label becomes "Dual-channel" + an info-tooltip that reads "Deterministic dual-channel pipeline (text + KG sub-queries with verification + expansion). RAGSearch reference architecture." | ~5 HTML |
| F8 | **Remove Agentic Q&A frontend.** Delete `#impact-sub-agentic` sub-tab, the `impact-subtab` nav button, `#tab-agentic` orphan block, the corresponding `impact.js` + `graph.js` handlers, and the `data-action="ask-agentic-question"` registration. **Keep `/api/compare-qa/*` backend route alive** until Phase 5B (matches the multihop posture from plan §2.3 / VP-Prod major #4). | ~−400 HTML/JS |
| F9 | Update the impact tab's welcome copy now that "agentic task quality" is no longer one of the 3 sub-tabs ("3" → "2"). | ~5 HTML |
| F10 | Update integration tests: `test_scenario_endpoints.py` stays valid (endpoints unchanged). Update `test_phase5a_smoke.py` for any DOM-id changes. Manual UI smoke checklist for the modal Why tab. | ~30 |
| F11 | Update `dev-report.md` and `demo-output.md` to document the corrected placement. | ~80 docs |

**Total:** ~700 LOC net (deletion-heavy: ~400 LOC removed, ~300 LOC
added/relocated). Wall-clock ~4-6h.

## What is explicitly out of scope

- No backend changes **beyond a single additive `status: str = "ready"`
  field** on `ScenarioTemplate` + the templates DTO (per §F4a in the
  plan; needed to expose scaffold flag to the picker). The 4
  endpoints (`/api/scenario-{a,b}/{run,analyze}`), the F1 scorer
  (`scenario_b_eval.py`), and the 5 services (`dense_rag`,
  `graph_rag`, `agentic_dense_rag`, `graphsearch_pipeline`,
  `scenario_resolver`) ship otherwise unchanged.
- No new gold fixtures for the 4 new clinical templates. Gold
  authoring is the labor-intensive HITL step; defer per Phase 5B
  scope. The 4 new clinical scenarios will return "no gold available"
  in `/api/scenario-b/analyze` until CTO HITL adds them.
- No Phase 5B work (tool-agent live wiring, full v4 backend deletion,
  per-graph deep-link emission from the producer side).
- No re-authoring of the existing 5 fin templates or the JNJ-Stelara
  clinical hedge gold.
- No changes to corpus build (`scripts/build_rag_corpus.py`); it stays
  per-ticker, no domain filtering needed.
- No tests against a live LLM. Sprint stays $0 token spend per the
  predecessor sprint's posture.

## Behaviors observable to the operator (acceptance bullets)

After this fixup lands:

- Open `/compare` → enter ticker → run an extraction → click "Expand"
  on any slot → modal opens.
- Click the **Why** tab → see two sub-tabs: "One-shot Q&A" (Scenario A,
  default) and "Multi-hop scenarios" (Scenario B).
- **Scenario A** has a free-text input, the A1/A2/A3 mode toggle, and
  Run + Analyze buttons. **No ticker picker.**
- **Scenario B** has a scenario picker showing **only the 5 templates
  matching this slot's domain** (financial OR clinical), plus a
  resolved-question preview and Run + Analyze buttons. **No ticker
  picker.** "Show advanced" toggles in the tool-agent placeholder pane.
- Both scenarios' **Analyze Results buttons are visible** (disabled
  until Run completes; enabled afterward).
- The **bottom of `/compare`** no longer has a Scenario A or Scenario
  B section.
- The **Graph Impact tab** no longer has an "Agentic Q&A" sub-tab.
- The Scenario B center pane is labeled **"Dual-channel"** with an
  info-tooltip; "arXiv:2509.22009" no longer appears in the UI.
- The picker on a **clinical slot** does not say "Select ticker" — it
  resolves from the trial select.

## Hard caps

- **Wall-clock cap: 6 hours.** This is a focused rework.
- **Token budget: $0** — no real LLM calls during dev. Same posture as
  the predecessor sprint.
- **Commits expected: 6** (per plan §10 v2; commit 0 = pre-EXECUTE
  deprecation audit + 1–6 = code commits, after VP-Eng nit-S2 split).

## Branch + commit strategy

- New branch `sprint-prd-004-v5-phase-5a-fixup-20260430` cut off
  `sprint-prd-004-v5-phase-5a-20260428`.
- 6 commits per the plan v2. Push to origin; do NOT merge to main.
- The predecessor branch (`sprint-prd-004-v5-phase-5a-20260428`) does
  not get pushed further — operator merges this fixup branch onto
  main when CEO/CTO approves.

## Completion criteria

- [ ] All 11 deliverables (F1–F11) on branch.
- [ ] All unit + integration tests green (predecessor's 445-pass
      baseline preserved).
- [ ] Manual UI smoke: open the Why tab in a financial slot, see fin
      scenarios; open in a clinical slot, see clinical scenarios.
      Run Scenario A; click Analyze; run Scenario B; click Analyze.
- [ ] Dev-report at `docs/sprints/sprint-prd-004-v5-phase-5a-fixup-20260430/dev-report.md`.
- [ ] Branch pushed to `origin/sprint-prd-004-v5-phase-5a-fixup-20260430`.

— Dev team, 2026-04-30
