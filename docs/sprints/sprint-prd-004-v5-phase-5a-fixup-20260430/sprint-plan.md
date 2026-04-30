# PRD-004 v5 Phase 5A Fixup — Sprint Plan

**From:** Dev team
**To:** CTO
**Date:** 2026-04-30
**Branch:** `sprint-prd-004-v5-phase-5a-fixup-20260430` (off
`sprint-prd-004-v5-phase-5a-20260428` HEAD `8c3f3bb`)
**Predecessor:** `sprint-prd-004-v5-phase-5a-20260428` (12 commits;
not yet merged to main)
**Revision:** **v2** — incorporates VP-Eng + VP-Prod review fixes
(see §11) + CTO Socratic-clarification resolutions (see §7).

---

## TL;DR

Phase 5A delivered the right backend but mis-placed the UI. The v2
plan corrects placement, lands the **"increasing impact" story** in
the Why tab (single-shot retrieval → multi-hop decomposition on the
same question set), and absorbs both VP-review pass and CTO
Socratic-clarification feedback.

1. **Move Scenarios into the per-graph modal's Why tab** with two
   sub-tabs:
   - **Sub-tab 1: "Single-shot Q&A" (default).** Picker over the 5
     fin / 5 clin templated scenarios + a "type your own" free-text
     box. Both paths feed `/api/scenario-a/run` (Dense RAG vs
     GraphRAG). Replaces the existing Why-tab Q&A flow.
   - **Sub-tab 2: "Multi-hop scenarios."** Same picker; runs the
     question through `/api/scenario-b/run` (agentic dense +
     dual-channel; tool-agent placeholder).
2. **Domain-agnostic UX, per-domain scenario configs.** 5 fin + 1
   real clinical (JNJ-Stelara hedge) + 4 clinical scaffolds with
   `status: "scaffold"`. Modal **locks to slot's domain at open
   time** (no mid-session re-fetch); picker filters accordingly.
3. **No ticker picker.** Slot binding gives us the ticker.
4. **Remove Agentic Q&A** (frontend only; backend route stays alive
   per multihop posture).
5. **Analyze Results buttons + placeholder result card** under each
   sub-tab. Disabled until Run completes.

Author the 4 clinical scaffolds (placeholder copy, no gold). Drop
the "arXiv:2509.22009" badge wording. Delete the
bottom-of-`/compare` standalone region. Backend services +
endpoints + F1 scorer ship **unchanged** (with one small additive
exception: `ScenarioTemplate` gains a `status: str = "ready"` field
exposed via the templates DTO).

**6 commits** (was 5; commit 2 split per VP-Eng nit). ~700 LOC net
(deletion-heavy: ~400 removed, ~300 relocated/added). 4–6h
wall-clock, **$0 token spend**.

---

## 1. What changes vs the predecessor sprint

The predecessor sprint built:
- Corpus + 5 services + 4 endpoints + 11 gold drafts (CORRECT, KEEP).
- Bottom-of-`/compare` Scenario A + Scenario B sections (WRONG
  PLACEMENT, REMOVE).
- A global ticker picker with hardcoded 8-entry list (WRONG, REMOVE).
- 5 fin + 1 clinical hedge templates (UNDER-DELIVERED, ADD 4 CLINICAL).
- Plan deferred 4 clinical templates to 5B; user has now reclassified
  them as 5A scope.

Backend, services, F1 scorer, gold fixtures, corpus builder: zero
changes. This is a frontend rework + a YAML expansion.

## 2. Reconnaissance findings

### 2.1 Modal Why tab as the new home

`#modal-why-content` lives in `compare.html` lines ~2673-2700. Today
it carries a single textarea + Ask button + with-graph / without-graph
answer panes (the Sprint 05 HITL-round-2 "Why This Matters" Q&A flow,
backed by `/api/why-this-matters/{ticker}`).

The fixup **replaces** this body with:

```
+ Header: 💡 Why This Matters · pipeline label · ticker label · status
+ Sub-tabs: [One-shot Q&A] [Multi-hop scenarios]
+ Sub-tab content A — Scenario A (free-text, A1/A2/A3 toggle, Run, Analyze)
+ Sub-tab content B — Scenario B (template picker, Run, Analyze, three panes)
```

The existing `triggerModalWhyThisMatters` function in `slots.js:949`
and the `/api/why-this-matters/{ticker}` backend route are **kept
alive** in case a hotfix needs them (same posture as the multihop
backend kept alive in 5A commit 9a). Frontend buttons go away.

### 2.2 How the modal exposes the active ticker

`slots.js:949` shows the existing pattern:

```js
let ticker;
if (currentDomain === 'clinical') {
    ticker = document.getElementById('trial-select').value;
} else {
    ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
}
```

`expandedSlot` (module-level int in `slots.js`) + `slotState[expandedSlot]`
give us the slot's pipeline + bundle + meta. The two new runners read
these directly — exact same pattern.

### 2.3 Domain inference

`currentDomain` is a global in `domain-switch.js`; flips between
`'financial'` and `'clinical'` when the operator switches domain at
the top of the page. The Scenario B template picker filters on this
plus the slot's pipeline metadata.

### 2.4 Where Agentic Q&A lives

Two surfaces:
- **`#tab-agentic`** at `compare.html:2295` — orphan top-level tab.
  No nav button reaches it; safe to delete.
- **`#impact-sub-agentic`** at `compare.html:2453` — active sub-tab
  inside the Graph Impact tab. The "Run Impact Analysis" button is
  here. `impact.js:392` is the handler; `graph.js:1378` has another
  agentic block.

Both delete. Keep `/api/compare-qa/{doc_id}` (in `demo_compare.py:2284`)
alive until 5B.

### 2.5 Cross-repo discipline

Predecessor sprint adhered strictly. This fixup likewise: no edits
outside `kgspin-demo-app`.

## 3. Deliverables

### F1. Modal Why tab — sub-tab scaffold + Single-shot sub-tab markup

**Files:** `static/compare.html`

Replace `#modal-why-content` body with a sub-tab container + the
**Single-shot sub-tab** content. CSS reuses the existing
`.scenario-a / .pane / .scenario-a-verdict` classes (already in
`compare.html` from commits 9b/10) with modal-scoping selectors
where needed.

**Single-shot sub-tab structure (CTO Socratic resolution 2026-04-30):**

```
[Why tab header: 💡 Why This Matters · ticker · pipeline · slot · status]
[Sub-tabs: ⚪ Single-shot Q&A (default)  ⚪ Multi-hop scenarios]

[Single-shot sub-tab content]
┌─ Templated scenarios ──────────────────────────────┐
│ [Picker: ▼ pick one (filtered by slot's domain)]   │
└─────────────────────────────────────────────────────┘
            — or —
┌─ Type your own ────────────────────────────────────┐
│ [free-text textarea]                                │
└─────────────────────────────────────────────────────┘
[A1 / A2 / A3 mode toggle]
[Run]  [Analyze Results (disabled until Run)]
[Dense RAG pane | GraphRAG pane]
[Placeholder card: "Run a scenario or type a question, then click Analyze for an LLM-judge verdict."]
[Verdict aside (hidden until Analyze completes)]
```

Behavior:
- Picking a scenario → its **resolved-question text auto-populates
  the free-text textarea**, then user can edit before Run. This
  unifies both paths into a single fetch to `/api/scenario-a/run`.
- Free-text-only path: user types a question, clicks Run; same
  endpoint.
- Picker is filtered by slot's domain (set at modal-open time per
  F14 below).

New CSS additions (~50 LOC):
- `.why-subtabs` — horizontal tab bar.
- `.why-subtab` / `.why-subtab.active` — pill-style buttons.
- `.why-subtab-content` / `.why-subtab-content.active` — show/hide.
- `.modal-why-wrap`, `.modal-why-header`, `.modal-why-rubric`,
  `.modal-why-pipeline`, `.modal-why-ticker`, `.modal-why-graph-id`
  (graph identity per VP-Prod watch S1), `.modal-why-status` —
  modal-scoped Why-tab chrome.
- `.modal-scenario-placeholder-card` — the "Run a scenario, then
  Analyze" placeholder card (per VP-Prod M1).

**DOM-id rename surface (per VP-Eng M1).** Every `scenario-a-*` id
in the predecessor sprint's `scenario-a-runner.js` becomes
`modal-scenario-a-*`. Full enumeration:

| Old (predecessor) | New (fixup) |
|---|---|
| `scenario-a-question-input` | `modal-scenario-a-question-input` |
| `scenario-a-ticker-picker` | (removed; slot context replaces) |
| `scenario-a-mode` (radio name) | `modal-scenario-a-mode` |
| `scenario-a-mode-badge` | `modal-scenario-a-mode-badge` |
| `scenario-a-run-btn` | `modal-scenario-a-run-btn` |
| `scenario-a-analyze-btn` | `modal-scenario-a-analyze-btn` |
| `scenario-a-status` | `modal-scenario-a-status` |
| `scenario-a-dense-answer` | `modal-scenario-a-dense-answer` |
| `scenario-a-dense-context` | `modal-scenario-a-dense-context` |
| `scenario-a-graphrag-answer` | `modal-scenario-a-graphrag-answer` |
| `scenario-a-graphrag-context` | `modal-scenario-a-graphrag-context` |
| `scenario-a-verdict` | `modal-scenario-a-verdict` |
| `scenario-a-verdict-winner` | `modal-scenario-a-verdict-winner` |
| `scenario-a-verdict-rationale-a` | `modal-scenario-a-verdict-rationale-a` |
| `scenario-a-verdict-rationale-b` | `modal-scenario-a-verdict-rationale-b` |
| `scenario-a-verdict-summary` | `modal-scenario-a-verdict-summary` |

**New** in fixup (no predecessor counterpart):
- `modal-scenario-a-template-picker` — the per-domain template
  dropdown (single-shot tab).

### F2. Multi-hop sub-tab markup inside the modal Why tab

Same idea as F1, but for the second sub-tab. Includes the same
template picker (domain-filtered), the resolved-question preview,
three panes (agentic_dense, dual-channel, tool_agent placeholder),
gold-badge / no-gold-badge, verdict aside with F1 + rationale +
recovery-narrative + methodology-note, and the placeholder result
card (per VP-Prod M1).

**Same DOM-id rename surface as F1** — every `scenario-b-*` id from
the predecessor becomes `modal-scenario-b-*`. Full table mirrors F1
(question-text, template-picker, resolved-question, gold-badge,
no-gold-badge, agentic-answer, agentic-progress, agentic-trace,
paper-answer, paper-progress, paper-text-history, paper-kg-history,
verdict, f1-block, rationale-block, recovery, run-btn,
analyze-btn, show-advanced-btn, status).

### F3. Delete the bottom-of-`/compare` standalone region

Remove the `data-region="prd004-v5"` block + its `<section
class="scenario-a">` and `<section class="scenario-b">` children.
Leave the comment trail saying "moved to modal Why tab in fixup".

### F4. Domain-filter the scenario template picker

**Per VP-Eng B1:** `let currentDomain = 'financial'` in
`domain-switch.js:7` does NOT bind to `window` (top-level `let`
script-scope only). Reading `window.currentDomain` returns
`undefined`. The fix is to reference the **bare identifier**
`currentDomain` from inside any handler that lives in the same
script-scope — that's how `slots.js:944 triggerModalWhyThisMatters`
already does it. The IIFE wrapper in
`scenario-{a,b}-runner.js` requires either (a) un-wrapping the
IIFE so the outer-scope identifier is reachable, or (b) reading
through a getter (`document.body.dataset.currentDomain` set by
`domain-switch.js` on every flip).

**Decision:** option (b). Cleanest cross-script handshake without
removing existing IIFE encapsulation. `domain-switch.js` writes
`document.body.dataset.currentDomain = currentDomain` on every
domain flip; runners read `document.body.dataset.currentDomain ||
'financial'`.

**Per VP-Prod M2 / VP-Eng M3:** modal **locks to slot's domain at
open time**. The slot's pipeline meta carries domain (per
`PIPELINE_META` in `slots.js`). On modal open we capture
`modalDomain = slotState[expandedSlot].domain` (or fall back to
`document.body.dataset.currentDomain`); subsequent domain-flips
mid-modal-session do NOT re-fetch the picker. The picker is
re-fetched only on next modal open. Code:

```js
// In modal-open handler:
const slotDomain = (slotState[expandedSlot] || {}).domain
                 || document.body.dataset.currentDomain
                 || 'financial';
modalState.lockedDomain = slotDomain;
fetchTemplatesForDomain(slotDomain);
```

`fetchTemplatesForDomain(domain)` calls `/api/scenario-b/templates`,
filters server-side or client-side (server returns all 10; client
filters to ones whose `domain === lockedDomain`), populates picker.

**Defensive default per VP-Prod S5:** if `slotState[expandedSlot]`
is missing AND `document.body.dataset.currentDomain` is missing,
the runner shows an **explicit error state** ("No slot context —
close and retry") rather than silently defaulting to financial.

### F4a. ScenarioTemplate dataclass + DTO change (per VP-Eng M3)

**File:** `src/kgspin_demo_app/services/scenario_resolver.py`

Add `status: str = "ready"` field to `ScenarioTemplate` dataclass.
Default `"ready"` keeps every existing test passing without changes.
The 4 clinical scaffolds set `status: "scaffold"` in the YAML.

**File:** `demos/extraction/demo_compare.py:scenario_b_templates`

Templates DTO must serialize the new `status` field so the picker
can disable Run on scaffold selection. One-line addition to the
existing list-comprehension that builds the DTO list.

This is the only **additive backend change** in the fixup. Surfaced
explicitly because scope §"out of scope" originally said "no
backend changes" — that wording is updated to "no backend changes
beyond a single additive `status` field on the templates DTO."

### F5. Add 4 clinical scaffold entries (CTO clarification: scaffold-only)

`demos/extraction/multihop_scenarios_v5.yaml` adds 4 scaffold
entries with `status: "scaffold"` and placeholder copy. CTO replaces
each with a real scenario design once clinical v0 is correct.

```yaml
- scenario_id: clinical_scaffold_1
  domain: clinical
  status: scaffold
  display_label: "Phase progression × endpoints (TBD)"
  expected_hops: 0
  expected_difficulty: medium
  placeholders: []
  key_fields: []
  question_template: "Scenario design pending — clinical v0 in progress."
  talking_track: "Scaffold entry. CTO replaces with real scenario design when clinical v0 lands."
- scenario_id: clinical_scaffold_2
  ... (Adverse events × dose (TBD))
- scenario_id: clinical_scaffold_3
  ... (Cross-trial inclusion overlap (TBD))
- scenario_id: clinical_scaffold_4
  ... (Regulatory submission basis (TBD))
```

**Scaffold contract (per CTO clarification 2026-04-30):**
- YAML field `status: scaffold` flags an entry; default unset →
  `status: ready`.
- Picker shows scaffold entries with the `(TBD)` suffix in the
  visible label. The `<option>` element itself is NOT
  `disabled` — operator can read the (TBD) label and select it
  to see the helper text (per VP-Eng nit on `<option disabled>`
  vs button-disabled).
- The frontend's picker-change handler reads `status`. When
  `status === "scaffold"`, the Run button gets `disabled = true`
  + helper-text caption "Scenario design pending — clinical v0 in
  progress."
- **No backend rejection.** Per CTO Conflict #3 resolution
  2026-04-30: this is a laptop demo, not public-web; curl threat
  model doesn't apply. Frontend disable is sufficient. (VP-Eng
  B3's contradiction with "no backend changes" is resolved by
  removing the backend-reject clause.)
- ZERO gold fixtures authored for the 4 scaffolds.
- The existing JNJ-Stelara hedge keeps `status: ready` (default)
  and remains the real clinical scenario.

### F6. Analyze buttons + placeholder result card (per VP-Prod M1)

PRD-004 v5 #13 calls for an Analyze Results button per scenario.
Predecessor commits added them but used `hidden` attribute, so they
appear only after Run completes. CTO feedback: "I didn't see the
analyze button requested."

VP-Prod flagged: greyed-out buttons in a dense modal are easy to
miss. Combined fix:

1. Change `hidden` → `disabled` on both Analyze buttons (always
   visible; greyed until Run completes).
2. **Add a placeholder result card** under each sub-tab's panes
   with the copy: "Run a scenario or type a question, then click
   Analyze for an LLM judge verdict (Single-shot) / F1 vs gold
   (Multi-hop)." The card replaces itself with the verdict-aside
   content on Analyze success.

Concrete markup addition:

```html
<div class="modal-scenario-placeholder-card" id="modal-scenario-{a,b}-placeholder">
  Run a scenario or type a question, then click <strong>Analyze
  Results</strong> for an LLM judge verdict.
</div>
<aside class="scenario-{a,b}-verdict" id="modal-scenario-{a,b}-verdict" hidden>
  ...
</aside>
```

Runner toggles `placeholder.hidden = true` + `verdict.hidden =
false` when Analyze completes.

### F7. Drop "arXiv:2509.22009" badge wording (per VP-Prod M4)

Replace `<span class="badge badge-paper">arXiv:2509.22009</span>` with
`<span class="badge badge-paper" title="Two retrieval channels —
document text and the knowledge graph — combined with cross-check
and refinement.">ℹ</span>`.

The pane header text becomes plain "Dual-channel" instead of
"Paper-mirror GraphSearch". **No "RAGSearch" / "arXiv" / "paper"
copy in any operator-visible string** (per VP-Prod M4). Provenance
links stay in source comments + `_graphsearch_prompts.py`
attribution header (file is not operator-visible).

VP-Eng nit-S3: re-check `.badge-paper` color scheme (`#2a3a15`/gold)
in CSS. With the `ℹ` glyph instead of paper-id text, it might read
as "info success" rather than "advanced". Swap to a neutral
grey-blue if eyeball-check fails during F1 manual smoke.

### F8. Remove Agentic Q&A frontend (backend stays alive)

**Delete (full enumeration per VP-Eng B2 — no line numbers, drift-safe):**

`static/compare.html`:
- `#tab-agentic` orphan block (~22 LOC).
- The `impact-subtab` button labeled "Agentic Q&A" inside
  `#impact-subtab-bar`.
- `#impact-sub-agentic` sub-tab content (~150 LOC).

`static/js/impact.js`:
- The `// Tab 3: Graph Impact — Agentic Q&A` block + every helper
  it calls (`runImpact`, `loadImpactRun`, `renderImpactQA`, plus
  any `qa-result` SSE handler that's not used elsewhere).
- The `data-action="ask-agentic-question"` and
  `data-action="navigate-qa-run"` action registrations.

`static/js/graph.js`:
- The `// --- Agentic Q&A ---` block at line ~1378 + any helpers
  it calls.

`static/js/compare-runner.js`:
- The `/api/compare-qa/{ticker}` fetch call (line ~2513) and the
  immediately-surrounding handler that called it (only that
  handler — NOT any neighboring code).

`static/js/slots.js`:
- `triggerModalWhyThisMatters()` function definition.
- `data-action="trigger-modal-why-this-matters"` registration at
  bottom of file.
- `WTM_DEFAULT_QUESTIONS` const and `showWhyThisMattersSection()`
  no-op (the entire `// --- compare.html lines 8044-8104: WTM_*` block).

**Verify before each deletion** that no other site references the
deleted symbol (`grep -rn`). VP-Eng B2: line numbers drift; symbol
names don't.

**Keep alive (backend, per VP-Prod #4 multihop posture):**
- `/api/compare-qa/{doc_id}` route handler + helpers in
  `demo_compare.py`.
- `/api/why-this-matters/{ticker}` route handler in
  `demo_compare.py` and any helpers it calls.
- Backend test coverage stays green: existing
  `tests/integration/test_multihop_endpoint.py` references stay; new
  alive-but-unused tests added per F10 (M5).

### F9. Update Impact tab welcome copy

Change `<p>Prove the value of deterministic knowledge graphs:
<strong>reproducibility</strong>, <strong>data lineage</strong>, and
<strong>agentic task quality</strong>.</p>` to drop "agentic task
quality" — leaving reproducibility + data lineage as the two sub-tabs.

### F10. Test updates (per VP-Eng M2, M4, M5)

- `tests/integration/test_scenario_endpoints.py` — backend
  endpoints unchanged; existing tests stay valid as-is. **Add**
  `test_templates_returns_ten_with_status_field` covering: returns
  10 entries, 4 carry `status="scaffold"`, 6 carry `status="ready"`.
- `tests/integration/test_phase5a_smoke.py` — endpoints unchanged;
  smoke is API-only (no DOM assertions); stays valid.
- `tests/unit/test_scenario_resolver.py` adjustments:
  - **Rename** `test_loads_six_phase5a_templates` →
    `test_loads_ten_phase5a_templates`. Bump count assertion 6 → 10.
  - **Update** `test_each_template_has_required_fields` to skip
    scaffolds for the placeholders/key_fields non-empty asserts:
    `if t.status == "scaffold": continue` before the truthy
    checks. (Scaffolds have empty `placeholders=[]` /
    `key_fields=[]`.)
  - **Add** `test_scaffold_templates_have_status_flag` covering
    the 4 scaffolds load with `status="scaffold"` and the other 6
    with `status="ready"`.
  - **Update** `test_template_placeholders_match_yaml_declaration`
    to skip scaffolds (their templates are placeholder copy without
    `{placeholder}` markers — already matches `placeholders=[]`,
    but skipping keeps the test rigor for ready entries).
- **Per VP-Eng M5 — alive-but-unused route guards.** Verify and
  add if missing:
  - `tests/integration/test_multihop_endpoint.py::test_multihop_run_*`
    (existing) stays green.
  - Add `tests/integration/test_why_this_matters_alive.py` — one
    test that POSTs to `/api/why-this-matters/{ticker}` with a
    mocked LLM and asserts 200 + non-empty payload. Documents the
    "alive but unused-by-UI" posture.
  - Add `tests/integration/test_compare_qa_alive.py` — same shape
    for `/api/compare-qa/{doc_id}`.
- Manual UI smoke — captured in `demo-output.md` screenshots /
  click-through transcript.

### F10a. Pre-EXECUTE deprecation audit (per VP-Prod B3)

Before writing any code in commit 1: grep `docs/`, `scripts/`,
demo-script artifacts, and any committed talking-track files for
the strings `"with KG"`, `"without KG"`, `"with-graph"`,
`"without-graph"`, `"Why This Matters"`, `"Agentic Q&A"`. Inventory
hits in a one-page audit at
`docs/sprints/sprint-prd-004-v5-phase-5a-fixup-20260430/deprecation-audit.md`.
For each hit, decide: (a) update wording to match Scenario A's
"Dense RAG vs GraphRAG" framing, (b) preserve as historical record
(date-stamped), or (c) leave alone (irrelevant). Block EXECUTE on
the audit being on disk.

### F11. Dev-report + demo-output (per VP-Eng nit S4)

New `dev-report.md` and `demo-output.md` in the fixup sprint dir.
**Do NOT edit** the predecessor sprint's
`sprint-prd-004-v5-phase-5a-20260428/dev-report.md` — leave
predecessor history archive-correct. The fixup's own dev-report
notes the supersession.

### F12. Modal Why-tab header carries graph identity (per VP-Prod S1)

The modal Why-tab header currently has:
```
💡 Why This Matters · {ticker} · {pipeline-label} · {status}
```

VP-Prod watch S1: the user opens a modal from a slot but loses
orientation as to which graph this Why tab is *for*. Add **slot
identity** to the header:

```
💡 Why This Matters · Slot N · {pipeline-label} ({backend}) · {ticker} · {status}
```

Reads "the Why tab for Slot 0's KGSpin graph on JNJ". Operator
always knows which graph their question is being scored against.

### F13. Visual-regression screenshots (per VP-Prod M3)

During F1 manual smoke, screenshot:
- Each `narrative_recovery` line surfacing in the modal verdict
  aside on a Multi-hop Analyze where any pane scores F1 < 0.3.
  The DRAFT gold strings were authored for a wider canvas; modal
  is narrower with sub-tab chrome above.
- Each scaffold's "Scenario design pending — clinical v0 in
  progress" helper-text in modal context.
- The `.badge-paper` ℹ glyph (eyeball check the color scheme reads
  right; per VP-Eng nit-S3).

File-and-eyeball follow-ups for any awkward truncation/wrapping
into `dev-report.md` "Surprising findings" section.

### F14. Modal-domain lock + defensive-default (per VP-Prod M2 / S5)

Already specified inside F4. Re-stated here for F-deliverable
completeness:

- On modal open, capture `modalState.lockedDomain = slotState[expandedSlot].domain || document.body.dataset.currentDomain`.
- All picker-fetches inside the open modal session use
  `lockedDomain`; ignore mid-session `currentDomain` flips.
- If both `slotState[expandedSlot]` and
  `document.body.dataset.currentDomain` are missing/falsy:
  **explicit error state** in the Why tab body — "No slot context.
  Close and re-expand a slot from the page above." NOT a silent
  financial fallback (per VP-Prod S5: silent fallback was the
  original cross-domain failure mode).

## 4. Test strategy

| Layer | What | Location |
|---|---|---|
| Unit | Template count goes 6 → 10; per-domain split | `tests/unit/test_scenario_resolver.py` (update existing test) |
| Unit | Gold fixture invariants unchanged (still 11) | `tests/unit/test_gold_fixtures.py` (no change) |
| Unit | Backend services unchanged | All existing service unit tests pass as-is |
| Integration | Endpoints unchanged | `tests/integration/test_scenario_endpoints.py` stays valid |
| Integration | Full Phase 5A smoke | `tests/integration/test_phase5a_smoke.py` stays valid |
| Manual | Modal Why tab opens in fin slot → fin scenarios; clinical slot → clinical scenarios | dev-report screenshots |
| Manual | Analyze button visible (disabled) before Run | dev-report screenshots |
| Manual | Bottom of `/compare` is empty (no Scenario A/B regions) | dev-report screenshots |
| Manual | Graph Impact tab shows Reproducibility + Data Lineage only (no Agentic Q&A) | dev-report screenshots |

## 5. Non-goals (re-stated to prevent scope creep)

- No new gold fixtures for the 4 new clinical templates.
- No backend changes.
- No live LLM smoke (post-merge validation pass per the predecessor
  sprint's plan §K).
- No changes to corpus build or any of the 5 services.
- No changes to the F1 scorer or the LLM-extract callable.
- No deletion of v4 multihop backend routes (`/api/multihop/*`,
  `/api/compare-qa/*`) — those remain alive until 5B.

## 6. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Modal Why tab CSS conflicts with the global `.scenario-a` styles | Low | Low | Same class names; differentiated only by parent context (`#modal-why-content .scenario-a` selectors when needed). Test in both fin + clinical modal slots. |
| Existing `triggerModalWhyThisMatters` handler has dependents we're missing | Low | Medium | Search shows only `data-action="trigger-modal-why-this-matters"` registration. Removing both is safe. Fall back is keeping the registration alive (no-op if button is gone). |
| 4 clinical templates draft-quality is too rough to demo | Medium | Medium | Mark them DRAFT with `confidence: "partial"` (same as the existing JNJ-Stelara hedge). CTO HITL pass replaces with verifiable values. UI shows the "no gold available — qualitative-only" badge so demo presenters know. |
| `currentDomain` global timing — not set yet when modal opens | Low | Low | `currentDomain` is set on initial DOM load by `domain-switch.js`; the modal can only open after the user enters a ticker. Per §F4 + §F14, both `slotState[expandedSlot].domain` AND `document.body.dataset.currentDomain` missing → **explicit error state in the Why tab body**, NOT a silent financial fallback. (Updated v2 per VP-Prod S5.) |
| Test count drift breaks `test_loads_six_phase5a_templates` | Low | Low | Update the assertion to 10. Trivial. |
| User wanted gold drafts authored too (vs deferred) | Medium | Low | Surface in plan + ask before execute. |

## 7. CTO clarifications (resolved 2026-04-30)

Five Socratic Q&A's resolved scope. Decisions:

1. **Picker UX** → Dropdown picker stays for scenario selection. No
   ticker picker. ("`Or anything`" in CTO's earlier message was
   ticker-only, not scenarios.)
2. **Sub-tab vs stacked** → **Sub-tabs** inside the Why tab.
   Default landing on "One-shot Q&A" (Scenario A); operator clicks
   "Multi-hop scenarios" for Scenario B.
3. **Analyze buttons "didn't see them"** → Visibility fix only.
   Predecessor sprint had them but `hidden` until Run. Change to
   `disabled` so they're always discoverable, greyed until enabled.
4. **Clinical templates** → **5-entry picker: 1 real + 4 TBD
   scaffolds.** The existing JNJ-Stelara hedge is the real one.
   The 4 scaffolds get a `status: "scaffold"` flag in the YAML; the
   picker shows them with a "(TBD)" suffix. **The Run button
   disables when a TBD entry is selected**, with helper copy
   "Scenario design pending — clinical v0 in progress." No backend
   roundtrip burned on scaffolds. CTO replaces the 4 scaffolds with
   real designs once clinical v0 is correct.
5. **Existing Why-tab Q&A flow → replaced** by Scenario A (same
   comparison axis, upgraded). Backend
   `/api/why-this-matters/{ticker}` stays alive until 5B (matches
   multihop / compare-qa posture).
6. **`/api/compare-qa/*` and `/api/why-this-matters/*` backend
   routes → stay alive until 5B.** Frontend wiring deleted.
7. **Bottom-of-`/compare` standalone Scenario A/B region → deleted.**
8. **"arXiv:2509.22009" badge → dropped.** Replaced with
   "Dual-channel" + an ℹ-tooltip (no academic-citation wording in
   the demo UI).

## 8. Wall-clock plan (6h cap)

```
H 0   ─ kick off; commit 1 starts (modal Why-tab markup + sub-tabs)         ┐
H 0.5 ─                                                                      │
H 1   ─ commit 1 lands; commit 2 starts (delete /compare bottom region)     │
H 1.5 ─ commit 2 lands; commit 3 starts (modal-context-aware runners)        │
H 2.5 ─                                                                      │
H 3   ─ commit 3 lands; commit 4 starts (4 clinical templates + tests)      │
H 4   ─ commit 4 lands; commit 5 starts (Agentic Q&A removal + dev-report)  │
H 5   ─                                                                      │
H 5.5 ─ commit 5 lands; manual UI smoke begins                              │
H 6   ─ branch pushed                                                       ┘
```

Critical path is mostly serial (markup → runners → tests → dev-report).
Buffer ~30 min if the modal CSS quirks need extra debugging.

## 9. What "done" looks like

**This (PLAN) sprint:**
- [x] Plan + scope on disk at
      `docs/sprints/sprint-prd-004-v5-phase-5a-fixup-20260430/`.
- [ ] CTO reviews the plan + answers open questions.
- [ ] Both VP reviews APPROVED.
- [ ] CTO authorizes EXECUTE.

**EXECUTE phase (not yet started):**
- All 11 deliverables (F1–F11) on branch.
- All unit + integration tests green (445-pass baseline preserved).
- Manual UI smoke confirmed with screenshots in dev-report.
- Branch pushed, NOT merged.

## 10. Commit plan (6 commits — split per VP-Eng nit)

0. `chore(audit): pre-EXECUTE deprecation grep + write deprecation-audit.md (F10a)`
1. `feat(modal): move Scenario A/B into Why tab; sub-tab scaffold; graph-identity header (F1, F2, F12)`
2. `refactor(scenario): runners read from modal context; no ticker picker (F3)` ← split
3. `feat(scenario): domain-filter + modal-domain-lock + status field on dataclass/DTO (F4, F4a, F14)` ← split
4. `feat(scenarios): 4 clinical scaffolds + alive-route guards + scenario-resolver test updates (F5, F10 partial)`
5. `chore(ui): Analyze visibility + placeholder card + drop arXiv badge wording (F6, F7, F13 screenshots)`
6. `chore(impact): remove Agentic Q&A frontend; keep backends alive; dev-report + demo-output (F8, F9, F10 final, F11)`

Each commit ~200 LOC max (commit 2 was the >300 LOC concern;
splitting F3 from F4 keeps both reviewable). Standard
`Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
trailer.

---

## 11. Plan v2 revisions (response to VP review)

This v2 incorporates fixes for **all** VP-Eng + VP-Prod blockers and
majors raised during the 2026-04-30 first review pass.

**VP-Eng blockers (resolved):**
- B1 (`window.currentDomain` doesn't exist) → §F4 specifies
  `document.body.dataset.currentDomain` getter pattern; `domain-switch.js`
  writes the dataset on every flip. Avoids removing existing IIFE
  encapsulation in runners.
- B2 (F8 deletion surface under-specified) → §F8 enumerates by
  filename + symbol, not line number. Adds `triggerModalWhyThisMatters`,
  `WTM_DEFAULT_QUESTIONS`, the action registration, and the no-op
  `showWhyThisMattersSection` to the deletion list.
- B3 (backend reject contradicts "no backend changes") → resolved
  per CTO Conflict #3 decision: **drop the backend reject**. Frontend
  disable is sufficient for laptop-demo threat model.

**VP-Eng majors (resolved):**
- M1 (DOM-id rename surface) → §F1 + §F2 enumerate the full table.
- M2 (`test_loads_six_phase5a_templates` mis-spec) → §F10 specifies
  rename, count bump, scaffold-skip in iteration tests, new
  `test_scaffold_templates_have_status_flag`.
- M3 (ScenarioTemplate dataclass + DTO need `status`) → new §F4a.
- M4 (no test for /templates returning 10 with status) → §F10 adds
  `test_templates_returns_ten_with_status_field`.
- M5 (no alive-but-unused tests for /why-this-matters and
  /compare-qa) → §F10 adds two new integration tests.

**VP-Eng nits (resolved):**
- Wall-clock buffer slim → §8 wall-clock notes "if commit 1 slips
  past H1, drop F11 docs to a follow-up."
- Commit 2 too large → split into commit 2 (F3 only) + commit 3
  (F4, F4a, F14).
- `.badge-paper` color check → §F7 inline VP-Eng nit-S3.
- Don't edit predecessor dev-report → §F11.
- Picker `<option disabled>` vs button-disabled → §F5 explicit
  on button-disabled.

**VP-Prod blockers (resolved):**
- B1 (default sub-tab should be Multi-hop) → CTO Conflict #1
  resolution **overrides** VP-Prod B1: hold on Single-shot first;
  the "increasing impact" story (single-shot → multi-hop on the
  same question set) is the operator's intended demo flow.
- B2 (4 scaffolds is a demo-day landmine) → CTO Conflict #2
  resolution **overrides** VP-Prod B2: hold on 5-entry picker.
  Operator runs the demo and is in control; landmine concern doesn't
  apply.
- B3 (deprecation audit) → new §F10a — pre-EXECUTE grep audit
  blocks code-write until on disk.

**VP-Prod majors (resolved):**
- M1 (Analyze button placeholder card) → §F6 adds the placeholder
  card.
- M2 (stale picker on mid-session domain switch) → §F4 + §F14:
  modal locks to slot's domain at open; no mid-session re-fetch.
- M3 (narrative_recovery in modal context) → new §F13 visual
  regression step.
- M4 (tooltip simplification, drop "RAGSearch") → §F7 updates
  tooltip copy.
- M5 (5A vs 5B scope blur) → next-sprint scope carve-out: 5B
  replaces clinical scaffolds with real designs + authors gold +
  deletes zombie routes (`/api/multihop/*`, `/api/compare-qa/*`,
  `/api/why-this-matters/*`). Documented at end of dev-report.

**VP-Prod soft watches (acknowledged):**
- S1 (graph identity in header) → new §F12.
- S2 (CSS bleed) → §F1 manual smoke includes both fin and clinical
  modal slot screenshots.
- S3 (zombie route sprawl) → 5B carve-out per M5.
- S4 (wall-clock tight) → §8 buffer note.
- S5 (defensive default to financial) → §F4 + §F14 explicit error
  state, not silent fallback.

— Dev team
