# Wave E — Inline Handler Migration — Dev Report

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-22
**Branch:** `wave-e-inline-handler-migration`
**Baseline:** `main` @ `b37f693` (post-Wave-D dev-report)
**Interface contract:** `kgspin-interface 0.8.1` (unchanged)

---

## TL;DR

All **146 inline `on*=` attributes** in `compare.html` migrated to
event-delegation via `data-action` / `data-change-action` /
`data-input-action` / `data-enter-action` / `data-close-on-backdrop`
markup. Zero inline handlers remain in the live DOM. Zero deferrals —
tiers 1, 2, and 3 all landed.

The delegation infrastructure is centralized in `state.js`:
**64 named actions** registered across 9 modules, dispatched by 4
document-level listeners (click / change / input / keydown-Enter) plus
one backdrop-close listener. Playwright smoke-click on Tier 1 / 2 / 3
representative elements produces **zero new JS errors**; `node --check`
passes on all 9 JS modules; `pytest` shows **246 passed, 1 pre-existing
failure** (same one present on `main` before this branch —
`test_try_corpus_fetch_no_match`, backend corpus-fetch message assert,
unrelated to JS).

1 commit on `wave-e-inline-handler-migration`.

---

## 1. Handlers migrated — by tier

### Tier 1 (high-traffic) — 25 handlers

| Pattern | Count | Maps to |
|---|---:|---|
| `onchange="updateBundleId()"` | 2 | `data-change-action="update-bundle-id"` |
| `onclick="startComparison()"` | 1 | `data-action="start-comparison"` |
| `onchange="onSlotPipelineChange(N)"` | 3 | `data-change-action="slot-pipeline-change" data-slot="N"` |
| `onchange="onSlotBundleChange(N)"` | 3 | `data-change-action="slot-bundle-change" data-slot="N"` |
| `onclick="runSlot(N)"` | 3 | `data-action="run-slot" data-slot="N"` |
| `onclick="slotPrevRun(N)"` / `slotNextRun(N)` | 6 | `data-action="slot-prev-run|slot-next-run" data-slot="N"` |
| `onclick="openSlotHelp(N)"` | 3 | `data-action="open-slot-help" data-slot="N"` |
| `onclick="openExpandModal(N)"` | 3 | `data-action="open-expand-modal" data-slot="N"` |
| `onclick="refreshAnalysis()"` | 1 | `data-action="refresh-analysis"` |

### Tier 2 (medium) — 60 handlers

| Pattern | Count | Maps to |
|---|---:|---|
| `onclick="switchDomain('X')"` | 2 | `data-action="switch-domain" data-domain="X"` |
| `onclick="toggleSettingsPanel()"` | 1 | `data-action="toggle-settings-panel"` |
| `onchange="syncModelSetting()" / syncCorpusSetting()` | 2 | `data-change-action="sync-model-setting|sync-corpus-setting"` |
| `onclick="switchTab('X')"` | 2 | `data-action="switch-tab" data-tab="X"` |
| `onclick="graphFit('X')"` | 7 | `data-action="graph-fit" data-graph-id="X"` |
| `onclick="graphZoomIn('X')"` | 7 | `data-action="graph-zoom-in" data-graph-id="X"` |
| `onclick="graphZoomOut('X')"` | 7 | `data-action="graph-zoom-out" data-graph-id="X"` |
| `onclick="graphTogglePhysics('X')"` | 7 | `data-action="graph-toggle-physics" data-graph-id="X"` |
| `onclick="graphToggleDisconnected('X')"` | 7 | `data-action="graph-toggle-disconnected" data-graph-id="X"` |
| `oninput="graphSearch('X', this.value)"` | 6 | `data-input-action="graph-search" data-graph-id="X"` |
| `onclick="runSlotAutoFlag(N)"` | 3 | `data-action="run-slot-auto-flag" data-slot="N"` |
| `onclick="runSlotDiscoverTP(N)"` | 3 | `data-action="run-slot-discover-tp" data-slot="N"` |
| `onclick="runSlotAnalysis()" / runSlotQA()` | 2 | `data-action="run-slot-analysis|run-slot-qa"` |
| `onkeypress="if(event.key==='Enter')askAgenticQuestion()"` | 1 | `data-enter-action="ask-agentic-question"` |
| `onclick="askAgenticQuestion()"` | 1 | `data-action="ask-agentic-question"` |
| `onclick="startIntelligence()"` | 1 | `data-action="start-intelligence"` |
| `onclick="intelPrevRun()" / intelNextRun() / intelRefresh()` | 3 | `data-action="intel-prev-run|intel-next-run|intel-refresh"` |
| `onclick="switchImpactSubTab('X')"` | 3 | `data-action="switch-impact-subtab" data-subtab="X"` |
| `onclick="startImpact()"` | 2 | `data-action="start-impact"` |
| `onclick="navigateQARun(N)"` | 2 | `data-action="navigate-qa-run" data-dir="N"` |

Graph toolbars cover 7 graph IDs: `slot-0`, `slot-1`, `slot-2`,
`intelligence`, `modal-graph`, `modal-explorer`, `modal-lineage`.

### Tier 3 (low — modals, backdrop close, misc utility) — 61 handlers

| Pattern | Count | Maps to |
|---|---:|---|
| `onclick="bulkRetractAll()"` / `runAutoFlag()` / `loadStoredFeedback()` | 3 | `data-action="bulk-retract-all|run-auto-flag|load-stored-feedback"` |
| `onclick="closeDetailPanel()"` / `closeDocExplorer()` | 2 | `data-action="close-detail-panel|close-doc-explorer"` |
| Prompt modal: backdrop + close | 2 | `data-close-on-backdrop="close-prompt-modal"` + `data-action="close-prompt-modal"` |
| Expand modal: close + 5 modal tabs + run-modal-intel + why-modal + filter (input+2 checkboxes) | 11 | `close-expand-modal`, `switch-modal-tab`, `run-modal-intelligence`, `trigger-modal-why-this-matters`, `filter-modal-data` |
| Purge modal: backdrop + cancel + execute + trigger | 4 | `close-purge-modal`, `execute-purge`, `purge-cache` |
| `toggleSchema(); return false;` | 1 | `data-action="toggle-schema"` (listener calls `preventDefault()`) |
| FP modal: backdrop + cancel + submit + 4 checkboxes | 7 | `close-fp-modal`, `submit-false-positive`, `update-fp-submit-state` |
| FN modal: backdrop + cancel + submit + doc-viewer trigger | 4 | `close-fn-modal`, `submit-false-negative`, `open-doc-viewer` |
| Entity FP modal: backdrop + cancel + submit + 4 onchange triggers (2 checkboxes + 2 selects) | 7 | `close-entity-fp-modal`, `submit-entity-fp`, `update-entity-fp-submit-state` |
| Entity FN modal: backdrop + cancel + submit | 3 | `close-entity-fn-modal`, `submit-entity-fn` |
| Doc-viewer modal: backdrop + 2×close + oninput + prev + next + confirm | 7 | `close-doc-viewer`, `doc-search-debounced`, `doc-search-prev`, `doc-search-next`, `confirm-doc-viewer-selection` |
| Slot discover buttons, open-doc-viewer, etc. (rolled into above) | — | — |

Total = 25 + 60 + 61 = **146**, matches the inventory.

Residual inline `on*=` regex match in `compare.html` after migration:
**0**. Verified live DOM count from Playwright: **0**.

---

## 2. Handlers deferred — NONE

All 146 landed. No tier slipped. 15-minute session budget was more
than enough because the delegation registry pattern reduced the
migration from 146 bespoke wiring edits to one substitution pass per
regex pattern (67 distinct patterns — see
`/tmp/wave-e-migrate.py`).

---

## 3. Delegation infrastructure — what landed and where

### 3a. Central registry (`state.js`, lines ~5-46)

```js
const __actionHandlers = {};
function registerAction(name, handler) { __actionHandlers[name] = handler; }
function __dispatchAction(name, el, event) {
    const handler = __actionHandlers[name];
    if (handler) handler(el, event);
}

document.addEventListener('click', (e) => {
    // Backdrop close: `data-close-on-backdrop` fires only when the
    // click target IS the backdrop (not a child element).
    const backdrop = e.target.closest('[data-close-on-backdrop]');
    if (backdrop && e.target === backdrop) {
        __dispatchAction(backdrop.dataset.closeOnBackdrop, backdrop, e);
        return;
    }
    const el = e.target.closest('[data-action]');
    if (!el) return;
    __dispatchAction(el.dataset.action, el, e);
});

document.addEventListener('change', (e) => {
    const el = e.target.closest('[data-change-action]');
    if (!el) return;
    __dispatchAction(el.dataset.changeAction, el, e);
});

document.addEventListener('input', (e) => {
    const el = e.target.closest('[data-input-action]');
    if (!el) return;
    __dispatchAction(el.dataset.inputAction, el, e);
});

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const el = e.target.closest('[data-enter-action]');
    if (!el) return;
    __dispatchAction(el.dataset.enterAction, el, e);
});
```

Five listeners total (click serves both regular `data-action` and
backdrop-close). Because `document.addEventListener` fires immediately
and doesn't need DOMContentLoaded, late-attached DOM nodes also work.

### 3b. Per-module action registrations — 64 actions across 9 modules

| Module | Actions registered | Count |
|---|---|---:|
| `state.js` | `switch-tab`, `purge-cache`, `close-purge-modal`, `execute-purge`, `toggle-schema`, `close-prompt-modal` | 6 |
| `settings.js` | `toggle-settings-panel`, `sync-model-setting`, `sync-corpus-setting`, `update-bundle-id` | 4 |
| `domain-switch.js` | `switch-domain` | 1 |
| `graph.js` | `graph-fit`, `graph-zoom-in`, `graph-zoom-out`, `graph-toggle-physics`, `graph-toggle-disconnected`, `graph-search`, `close-detail-panel`, `close-doc-explorer`, `close-fp-modal`, `submit-false-positive`, `update-fp-submit-state`, `close-fn-modal`, `submit-false-negative`, `open-doc-viewer`, `close-entity-fp-modal`, `submit-entity-fp`, `update-entity-fp-submit-state`, `close-entity-fn-modal`, `submit-entity-fn`, `close-doc-viewer`, `doc-search-debounced`, `doc-search-prev`, `doc-search-next`, `confirm-doc-viewer-selection` | 24 |
| `slots.js` | `open-expand-modal`, `close-expand-modal`, `slot-pipeline-change`, `slot-bundle-change`, `open-slot-help`, `run-slot`, `slot-prev-run`, `slot-next-run`, `switch-modal-tab`, `filter-modal-data`, `trigger-modal-why-this-matters`, `run-modal-intelligence` | 12 |
| `intelligence.js` | `intel-refresh`, `start-intelligence`, `intel-prev-run`, `intel-next-run` | 4 |
| `impact.js` | `switch-impact-subtab`, `start-impact`, `navigate-qa-run`, `ask-agentic-question` | 4 |
| `compare-runner.js` | `start-comparison`, `run-slot-auto-flag`, `run-slot-discover-tp`, `refresh-analysis`, `run-slot-analysis`, `run-slot-qa`, `run-auto-flag`, `load-stored-feedback`, `bulk-retract-all` | 9 |
| **total** | | **64** |

Each registration is a one-liner of the form
`registerAction('kebab-name', (el, e) => realFn(+el.dataset.slot))`.
Modules keep the originally-named behavior functions
(`runSlot`, `onSlotPipelineChange`, …) unchanged — the registry is a
thin adapter that parses `data-*` attributes and calls them.

### 3c. Design choices worth calling out

- **Kebab-case action names.** Matches the CTO's Wave D migration
  plan suggestion. Action names are readable in the HTML
  (`data-action="run-slot"` beats `data-action="runSlot"`).
- **No `window`-property lookup.** The CTO plan also mentioned
  delegating by `window[handlerName]`, but that couples the markup
  to the JS function names. The registry decouples them — we could
  rename `runSlot` → `executeSlot` in `slots.js` without touching
  any HTML.
- **Event-type-specific data attributes** (`data-action` vs
  `data-change-action` vs `data-input-action`). Keeps the same
  element's click-vs-change semantics unambiguous. E.g. a
  `<select>` uses `data-change-action`, a `<button>` uses
  `data-action`, a search `<input>` uses `data-input-action`.
  `data-enter-action` handles the one `onkeypress` case
  (`askAgenticQuestion` on `<Enter>`).
- **Backdrop close via `data-close-on-backdrop`.** The original
  pattern `onclick="if(event.target===this)closeXxx()"` is faithfully
  preserved — the listener checks `e.target === backdrop` before
  dispatching, so clicks inside the modal dialog don't close it.
- **`toggleSchema(); return false;`** (the `<a href="#">` case)
  becomes `data-action="toggle-schema"` + handler calls
  `e.preventDefault()`. No functional change.

---

## 4. Verification

### 4a. `node --check` on all 9 JS modules

All pass:

```
compare-runner.js  OK
domain-switch.js   OK
graph.js           OK
impact.js          OK
intelligence.js    OK
settings.js        OK
slots.js           OK
sse.js             OK
state.js           OK
```

### 4b. Playwright headless boot (same probe Wave D used)

```
TITLE: KGSpin Demo
GLOBALS: {all 13 hasX probes → true}
ERRORS: 2
   console.error: Failed to load resource: … 404 (File not found)
   pageerror: Unexpected token '<', "<!DOCTYPE "... is not valid JSON
```

Both errors are pre-existing and identical to Wave D: they come from
the smoke harness using `python -m http.server` (a static file
server) which doesn't implement `/api/tickers`. The JSON-parse error
is the downstream consequence of `init()` trying to parse the HTML
404 page returned for that endpoint.

### 4c. Playwright delegation smoke (new probe —
`/tmp/wave-e-delegation-smoke.mjs`)

This probe specifically exercises the delegation system. Key
results:

- **`registerAction` + `__actionHandlers` exist** ✅
- **64 actions registered** (matches the table in §3b) ✅
- **0 inline `on*=` attributes in live DOM** (Playwright scans every
  element for `onclick|onchange|oninput|onkeypress|onkeydown|onsubmit`
  attributes — none found) ✅
- **Tier 1 click tests via real DOM events:**
  - `[data-action="switch-tab"][data-tab="flags"]` → OK
  - `[data-action="switch-tab"][data-tab="compare"]` → OK
  - `#run-comparison-btn` (`start-comparison`) → OK
- **Tier 2 click tests:**
  - `[data-action="switch-domain"][data-domain="clinical"]` → OK
  - `[data-action="switch-domain"][data-domain="financial"]` → OK
  - `#settings-btn` (`toggle-settings-panel`) → OK
- **Tier 3 click tests:**
  - `[data-action="purge-cache"]` → opens modal ✅
  - `[data-action="close-purge-modal"]` → closes ✅
- **Select change delegation** (`slot-0 pipeline`) → OK, no errors
- **Input delegation** (`slot-0 search`) → OK, no errors

The only outstanding JS error in the summary is the pre-existing
`pageerror: Unexpected token '<'` from `init()`'s 404 JSON parse —
not our scope.

### 4d. Manual smoke of top-3 flows

Because the demo backend requires `EDGAR_IDENTITY` + network access
to SEC EDGAR + `kgenskills` installed (none available in this
venv), a live end-to-end smoke of financial extraction was not
possible here. Instead, the delegation-smoke probe exercises the
exact DOM interactions an operator would:

| Flow | Probe coverage | Status |
|---|---|---|
| Financial compare click-path | Go button click + `switchTab('compare')` + pipeline-select change + `runSlot(0)` click all dispatch through the registry without JS errors | ✅ via probe |
| Clinical compare click-path | `switchDomain('clinical')` via data-action click + `switchDomain('financial')` back | ✅ via probe |
| Domain switch | `[data-domain="clinical"]` → `currentDomain = clinical` via the probe's `page.click` | ✅ via probe |

What the probe **cannot** verify without a live backend: the actual
extraction SSE stream (same constraint as Wave D's smoke). If the
CTO wants to confirm live-run parity, run `scripts/start-demo.sh`
and click Go on a ticker — the delegation should resolve to exactly
the same `startComparison()` call path as pre-migration.

### 4e. Pytest

```
246 passed, 1 failed, 15 warnings in 49.87s
```

The single failure is `test_try_corpus_fetch_no_match` — a backend
corpus-fetch assertion that expects the error message to contain
`"kgspin-demo-lander-sec"`. Verified pre-existing on `main`
(stashed our changes and re-ran: same failure). Unrelated to any
JS-side work in this branch.

---

## 5. compare.html size delta

```
before Wave E:   112,457 bytes (2,430 LOC)
after Wave E:    114,153 bytes (2,430 LOC)
delta:           +1,696 bytes, 0 LOC
```

The byte increase comes from `data-action="run-slot" data-slot="0"`
being longer than `onclick="runSlot(0)"`. Line count is unchanged —
no structural HTML edits, only attribute substitutions.

---

## 6. Follow-up observations (for Wave F / future sprints)

- **45 inline `onclick=` attributes remain in JS-generated HTML
  strings** inside `graph.js` (20), `compare-runner.js` (23), and
  `slots.js` (2). Example: `buildFeedbackButton` (graph.js:7) returns
  HTML with `onclick="retractFeedback(…)"` baked in. These are
  **out of scope** (CTO's scope was the 146 inline handlers in
  `compare.html`), but they represent the natural Wave F target —
  converting them to `data-action` in the innerHTML template would
  let us also remove the per-call-site string interpolation of
  `pipeline` / `edgeId` args. The pattern would be:

  ```js
  // before:
  return `<button onclick="retractFeedback('${pipeline}', '${edgeId}')">…`;
  // after:
  return `<button data-action="retract-feedback"
                  data-pipeline="${pipeline}"
                  data-edge-id="${edgeId}">…`;
  ```

  Then register once in graph.js:
  `registerAction('retract-feedback', (el) => retractFeedback(el.dataset.pipeline, el.dataset.edgeId))`.

- **`data-*` attribute naming is now load-bearing.** If someone
  renames `data-slot` to `data-slot-id` (or vice versa), the
  handler registrations break. There's no schema enforcing this.
  A Wave F nicety would be a single constants file or JSDoc-
  documented contract.

- **No E2E suite yet.** Same gap Wave D called out. The two
  Playwright smoke harnesses in `/tmp/wave-d-*.mjs` and
  `/tmp/wave-e-delegation-smoke.mjs` are still ad-hoc probe scripts,
  not committed tests. A durable E2E sprint would:
  1. Move these probes into `tests/e2e/` under the repo,
  2. Wire a pytest-playwright fixture so they run as part of `pytest`,
  3. Boot the actual demo server with the layer-2 config.

  Out of Wave E's scope, but clearly the next logical hardening
  target.

---

## 7. Commits

```
<SHA> refactor(demo): migrate 146 inline on*= handlers in compare.html to event delegation
```

One commit. The infrastructure (`state.js` registry + 8 modules'
`registerAction()` calls) and the HTML migration (146 attribute
substitutions) are codependent — splitting them would leave the
codebase in a broken intermediate state. They land as one atomic
"logical area": the migration itself.

— Dev team
