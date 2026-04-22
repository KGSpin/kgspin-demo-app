# Wave F — Backlog Sweep — Dev Report

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-22
**Branch:** `wave-f-backlog-sweep`
**Baseline:** `main` @ `12298da` (post-Wave-E dev-report)
**Interface contract:** `kgspin-interface 0.8.1` (unchanged)

---

## TL;DR

Wave F swept the Wave E §6 follow-up: the ~45 inline `onclick=` / `onchange=`
attributes baked into **JS-generated HTML template strings** across
`graph.js`, `compare-runner.js`, and `slots.js`. All migrated to `data-action`
markup using the Wave E delegation infrastructure; **zero inline `on*=`
attributes** remain anywhere in the JS bundle or the live DOM.

Registered-action count: **64 → 89** (+25 Wave F actions). Pytest parity with
Wave E (**246 passed, 1 pre-existing failure**). Playwright smoke confirms
delegation dispatches cleanly into dynamically-injected DOM (3 injected
buttons → 3 handler calls, zero new JS errors).

1 commit on `wave-f-backlog-sweep`.

---

## 1. Scope — what the prior dev-reports flagged

Reading Waves A–E dev-reports, the deferred items that remained were:

| Source | Item | Outcome |
|---|---|---|
| Wave E §6 first bullet | 45 inline handlers in JS template strings | **Shipped** (this report) |
| Wave E §6 second bullet | `data-*` naming contract / schema | Flagged — cosmetic, needs its own doc pass |
| Wave E §6 third bullet | E2E suite (promote `/tmp/wave-*.mjs` into repo) | **Flagged, not landed** — needs pytest-playwright infra |
| Wave D §5 | Inline handler migration | Landed in Wave E (superseded) |
| Wave C spillover §1 | `kgenskills` runtime identifier sweep (~70 sites) | **Flagged, not landed** — cross-repo (logger namespace + pipeline wire-format string match kgspin-core emits) |
| Wave C spillover §2 | `ticker`/`nct` parameter-name carryover | **Flagged, not landed** — bundled with Wave B §3b pipeline-orchestrator carve |
| Wave C spillover §3 | Test-fixture domain literals | **Flagged, not landed** — depends on §2 |
| Wave C spillover §4 | Dense sprint-ID comment sweep in `demo_compare.py` | **Flagged, not landed** — pure editorial, 2-3h scope |
| Wave C spillover §5 | `bundles/legacy/` empty directory | **No-op** — already gone on disk |
| Wave B §3b | Pipeline orchestrator extraction (`run_comparison` et al., ~3,400 LOC) | **Flagged, not landed** — single-area carve needs its own sprint |
| Wave B §3c | `auto_flag_graph` / `auto_discover_tp` feedback orchestrators | **Flagged, not landed** — couples with §3b |

Per CTO directive ("Land anything cleanly shippable in a session; flag
bigger / cross-repo items"), only the Wave E §6.1 target was in-scope.

---

## 2. Migration — template-string handlers → data-action

### 2a. graph.js (20 sites)

| Template function | Action(s) added | Count |
|---|---|---:|
| `buildFeedbackButton` | `retract-feedback`, `open-fp-modal`, `open-fn-modal` | 6 |
| `buildEntityFeedbackButton` | `retract-entity-feedback`, `open-entity-fp-modal`, `flag-entity-tp` | 4 |
| `buildEntityFNButton` | `retract-entity-feedback` (with `data-feedback-type="fn"`), `open-entity-fn-modal` | 2 |
| `buildAutoFlagAlert` | `confirm-auto-flag`, `confirm-auto-flag-with-edits`, `dismiss-auto-flag` | 3 |
| `showNodeDetail` connected-list | `navigate-to-edge` (×2: row + flag icon) | 2 |
| `showEdgeDetail` subject/object list | `navigate-to-node` (×2) | 2 |
| `buildLegend` entity-type / rel-type items | `toggle-entity-type-filter`, `toggle-rel-highlight` | 2 (repeated per-type at render) |

`event.stopPropagation()` pattern on the flag-icon span collapses naturally
into delegation: `document.addEventListener('click', ...)` + `closest('[data-action]')`
resolves the **innermost** matching element, so only one handler fires per
click. The explicit stopPropagation call is no longer necessary.

### 2b. compare-runner.js (23 sites)

| Template function | Action(s) added | Count |
|---|---|---:|
| `renderFlagExplorer` bulk toolbar | `toggle-all-auto-flags` (change), `bulk-confirm-auto-flags`, `bulk-dismiss-auto-flags` | 3 |
| `renderFlagItem` | `confirm-auto-tp`, `dismiss-auto-tp`, `go-to-flag`, plus reuse of `confirm-auto-flag` / `confirm-auto-flag-with-edits` / `dismiss-auto-flag` | 6 |
| `renderStoredFeedbackItem` retract button | `retract-stored-feedback` | 1 |
| Retry badges (error + truncation + refresh paths) | `gem-refresh`, `mod-refresh`, `kgen-refresh` | 13 |

The `goTo` string-concat variable (previously interpolated into `onclick="${goTo}"`)
is retired; `go-to-flag` now carries `data-pipeline` + `data-flag-type` +
`data-flag-id` and the handler parses the id back to an int when
`data-flag-type === 'node'`.

`retractStoredFeedback(feedbackId, this)` previously took `this` (the button
DOM node) as its second arg — delegated form passes `el` (the element that
matched `data-action`), identical semantics.

### 2c. slots.js (2 sites)

| Template function | Action(s) added | Count |
|---|---|---:|
| `filterModalData` entity + rel rows | `toggle-data-detail` (handler receives the `<tr>` as `el`) | 2 |

### 2d. New Wave F action registrations — 25 in total

```
// graph.js additions (14)
retract-feedback, open-fp-modal, open-fn-modal
confirm-auto-flag, confirm-auto-flag-with-edits, dismiss-auto-flag
retract-entity-feedback, open-entity-fp-modal, flag-entity-tp, open-entity-fn-modal
navigate-to-edge, navigate-to-node
toggle-entity-type-filter, toggle-rel-highlight

// compare-runner.js additions (10)
toggle-all-auto-flags, bulk-confirm-auto-flags, bulk-dismiss-auto-flags
confirm-auto-tp, dismiss-auto-tp, go-to-flag
retract-stored-feedback
gem-refresh, mod-refresh, kgen-refresh

// slots.js additions (1)
toggle-data-detail
```

Running total: Wave E 64 + Wave F 25 = **89 action handlers**, matching the
live count probed via Playwright.

---

## 3. Design notes

- **escapeHtml on keys with apostrophes.** `confirm-auto-tp` and
  `dismiss-auto-tp` store the full flag key (e.g. `auto_tp_slot-0_a_b_c`)
  in a `data-key` attribute. Apostrophes in entity text (`"Moody's"`) that
  previously needed `.replace(/'/g, "\\'")` for JS string embedding are
  now handled by `escapeHtml(key)` for HTML attribute embedding. This is
  a strict improvement — the old JS-string-escape was fragile around
  HTML-special characters; the new HTML-attr-escape isn't.
- **Numeric vs string IDs.** `go-to-flag` stashes the id as a string in
  `data-flag-id`; the handler parses it back to int when `flag-type === 'node'`
  (matching `parseFlagKey`'s contract where node ids are always ints).
- **Unused handlers removed.** `goTo` string concat and `idArg`/`typeArg`
  helpers in `renderFlagItem` are retired — replaced by direct data-attr
  emission.
- **Kebab-case, event-type-specific attrs** — consistent with Wave E
  conventions. The one `onchange` (the bulk select-all checkbox) uses
  `data-change-action`; the 14 retry buttons + 8 feedback buttons + 2
  row-click handlers all use `data-action`.
- **No delegation-infra changes.** The `state.js` registry from Wave E is
  untouched — all Wave F did was add handler registrations and swap markup.

---

## 4. Verification

### 4a. Inline `on*=` attribute count across the JS bundle

```
$ grep -c 'onclick=\|onchange=\|oninput=\|onkeypress=\|onkeydown=' demos/extraction/static/js/*.js
0
```

### 4b. `node --check` on all 9 JS modules

All pass (identical to Wave E baseline).

### 4c. Live DOM probe (Playwright headless boot against `compare.html`)

```
has registerAction + __actionHandlers: true
Registered actions: 89
Inline on*= attributes in live DOM: 0
Template-string data-action counts: {
  buildFeedbackButton:   2,   // FP+FN fallback pair
  buildEntityFeedbackButton:   2,
  buildEntityFNButton:   1,
  buildAutoFlagAlert_node: 3,   // confirm + edit + dismiss
  buildAutoFlagAlert_edge: 3,
  renderFlagItem_auto:   4,   // go-to + confirm + edit + dismiss
  renderFlagItem_autoTP: 2,   // confirm gold + dismiss
}
Delegation-via-injected-DOM-clicks: { buttons: 3, log: { confirm: 1, withEdits: 1, dismiss: 1 } }

=== SUMMARY ===
Total JS errors (excluding expected backend 404s): 1
  pageerror: Unexpected token '<', "<!DOCTYPE "... is not valid JSON
```

The 1 pageerror is pre-existing from Wave D/E (static `http.server` doesn't
implement `/api/tickers`, `init()` tries to parse the 404 HTML as JSON).
Not caused by Wave F.

**Dynamic-injection click test** is the key proof: three Wave-F-template-string-emitted
buttons injected into a `<div>`, clicked via `.click()` DOM events, and
all three handlers fired with the correct data-attrs threaded through.
Confirms the migration works end-to-end, not just the static markup.

### 4d. Pytest

```
246 passed, 1 failed, 15 warnings in 49.74s
```

Identical to Wave E baseline. The 1 failure (`test_try_corpus_fetch_no_match`)
is the pre-existing `"kgspin-demo-lander-sec"` assertion that Wave E also
flagged as pre-existing.

### 4e. Static action-name resolution check

```
Used action names (distinct):       89
Registered action names (distinct): 90  (+ 1 'kebab-name' in a comment)
Used but NOT registered:            <empty>
```

Every `data-action="..."` / `data-change-action="..."` / `data-input-action="..."` /
`data-enter-action="..."` / `data-close-on-backdrop="..."` value in
`compare.html` and the 9 JS modules resolves to a `registerAction('...')`
call somewhere in the bundle.

---

## 5. Deferred — flagged for later, reasoning

### 5a. E2E test suite promotion (Wave E §6.3)

The three smoke harnesses (`/tmp/wave-d-smoke.mjs`,
`/tmp/wave-d-interact.mjs`, `/tmp/wave-e-delegation-smoke.mjs`, plus the
new Wave F probe) are still ad-hoc. Promoting them into `tests/e2e/` under
pytest-playwright would need:

1. Add `pytest-playwright` to `pyproject.toml` dev deps.
2. Boot the real demo server (not `python -m http.server`) so `/api/*` resolves.
3. Seed a test fixture for `doc_id` / ticker extraction so SSE paths fire.

This is a full sprint's worth of infra work — out of Wave F's "cleanly
shippable" scope.

### 5b. `kgenskills` runtime-identifier sweep (Wave C spillover §1)

~70 sites cross Python SSE payloads, HTML element IDs, CSS selectors, the
cache directory `~/.kgenskills/logs/`, and the Python logger namespace
`logging.getLogger("kgenskills")`. The logger name MUST match what
`kgspin-core` emits — that's a cross-repo coordinated change. No-go for
Wave F per the "does NOT require cross-repo coordination" rule.

### 5c. Pipeline orchestrator extraction (Wave B §3b)

`run_comparison`, `run_single_refresh`, `_run_clinical_comparison`,
`run_intelligence`, `run_impact`, `_run_lander_subprocess` — ~3,400 LOC
of tight coupling to ~15 helpers in `demo_compare.py`. One-area,
single-commit constraint doesn't fit. Needs its own sprint.

### 5d. `ticker`/`nct` parameter carryover (Wave C spillover §2)

Bundled with §5c — rename is mechanical once the pipeline orchestrators
are carved. Attempting ahead of §5c would touch ~27 shims across
`demo_compare.py`, `_run_kgenskills`, and call sites in 5 modules.
Wrong shape for a one-commit sweep.

### 5e. `data-*` naming contract (Wave E §6.2)

Cosmetic — naming is now load-bearing (`data-slot`, `data-pipeline`, etc.)
but there's no schema. A single constants module or JSDoc block would
formalize it. Not shippable as "sweep leftovers" — wants a proper ADR.

### 5f. Sprint-ID comment sweep (Wave C spillover §4)

~80 `# Sprint N:` / `# INIT-001 Sprint N:` comments in `demo_compare.py`.
Pure editorial — worth doing as a one-shot before the next carve, but
not fitting the backlog-sweep mandate.

### 5g. `bundles/legacy/` directory (Wave C spillover §5)

No-op — the directory is already gone on disk (only `bundles/domains/`
remains). Nothing to clean up.

### 5h. `docs/sprints/_templates/dev-report.md` (Wave C spillover §6)

Not creating — we now have 6 dev-reports (A/B/C/D/E/F) whose structure
is consistent; a template file would duplicate what's already grep-able.

---

## 6. compare.html byte / LOC delta

```
before Wave F:   114,153 bytes (2,430 LOC)
after Wave F:    114,153 bytes (2,430 LOC)
delta:           +0 bytes, +0 LOC
```

Wave F only touched JS modules — `compare.html` is unchanged. The migration
reshaped template strings inside the JS files, so the Wave E-era HTML
(2,430 LOC, clean of inline handlers) is already in its final shape.

---

## 7. Commits

```
<SHA> refactor(demo): migrate template-string inline handlers to data-action delegation
```

One commit — infra (25 new `registerAction` calls across 3 modules) and
markup (45 template-string substitutions) are codependent. Same atomicity
constraint as Wave E's 146-handler commit.

---

## 8. File-size trajectory

```
graph.js           1,588 → 1,599 LOC  (+11)   [14 new registerAction calls + migrated templates]
compare-runner.js  2,588 → 2,598 LOC  (+10)   [10 new registerAction calls; 'goTo' concat retired]
slots.js           1,367 → 1,370 LOC  (+3)    [1 new registerAction + 2 migrated row handlers]
```

Total JS bundle: +24 LOC. The template-string migration itself is byte-roughly-neutral
(`data-action="x" data-slot="N"` vs. `onclick="x(N)"`) — the +24 LOC is
purely the new registration block at the bottom of each file.

---

## 9. Sweep conclusion

Every Wave-A-through-E-deferred item either landed this sprint (the
template-string handler migration), was flagged with rationale (cross-repo,
bigger-than-one-commit, or architectural), or was already a no-op (legacy
dir removed).

— Dev team
