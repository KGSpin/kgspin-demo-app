# Pre-EXECUTE Deprecation Audit (F10a)

**Date:** 2026-04-30
**Sprint:** `sprint-prd-004-v5-phase-5a-fixup-20260430`
**Required by:** plan §F10a (per VP-Prod B3); EXECUTE blocks on this
artifact being on disk.

## Scope

Grep `docs/`, `scripts/`, `demos/` for the strings:
- `with KG`
- `without KG`
- `with-graph`
- `without-graph`
- `Why This Matters`
- `Agentic Q&A`

Categorize each hit and decide: (a) update wording to match
Scenario A's "Dense RAG vs GraphRAG" framing, (b) preserve as
historical record (date-stamped), or (c) leave alone (irrelevant
or in code being deleted by this fixup).

## Hits — operator-visible (deleted by F8 in this sprint)

These strings live in DOM ids / CSS classes / JS handlers that F8
deletes outright. No standalone wording fix needed; deletion takes
care of them.

| Hit | Disposition |
|---|---|
| `demos/extraction/static/compare.html:876,877` — `.impact-qa-answer-header.with-graph/without-graph` CSS classes | Deleted by F8 (CSS for `#impact-sub-agentic` Agentic Q&A pane) |
| `demos/extraction/static/compare.html:2690,2695` — `modal-wtm-with-graph` / `modal-wtm-without-graph` divs | Deleted by F8 (existing Why-tab Q&A markup; replaced by Scenario A in F1) |
| `demos/extraction/static/js/slots.js:534,535` — `wtm-with-graph` element refs in `triggerWhyThisMatters` | Deleted by F8 (whole function goes; see F8 enumeration) |
| `demos/extraction/static/js/slots.js:966,967` — `modal-wtm-with-graph` refs in `triggerModalWhyThisMatters` | Deleted by F8 (whole function goes) |
| `demos/extraction/static/js/impact.js:525,535` — `with-graph`/`without-graph` classes in impact-Q&A renderer | Deleted by F8 (`#impact-sub-agentic` JS block) |

## Hits — internal (left alone; backend stays alive per VP-Prod #4)

These are Python comments / docstrings in `demos/extraction/demo_compare.py`
for the **Impact pipeline backend** that stays alive until 5B
(matches multihop posture). Backend wording lags but is not
operator-visible.

| Hit | Disposition |
|---|---|
| `demos/extraction/demo_compare.py:6872` — comment "(happens when cache was populated from disk cache with KG only)" | Leave alone — internal cache comment, not operator-visible |
| `demos/extraction/demo_compare.py:9220` — Impact pipeline docstring "compare LLM answers with vs without KG context" | Leave alone — Impact backend stays alive; docstring describes its behavior accurately |
| `demos/extraction/demo_compare.py:9391` — comment "Quality analysis — compare with-graph vs without-graph answers" | Leave alone — same reason |

These get cleaned up alongside the full backend deletion in **5B**
(per the §11 5B carve-out).

## Hits — historical artifacts (date-stamped; leave alone)

These are predecessor sprint reports / PRDs / architecture audits
that document the state of the system at a specific date. They are
**archive-correct as written**; editing them would damage historical
record.

| Hit | Disposition |
|---|---|
| `docs/reproducibility-by-triple-hash.md:3` — "integrating with KGSpin's extraction API" | Coincidental match; "with KG" is part of "with KGSpin's"; not the target framing |
| `docs/sprints/hotfix-why-tab-doc-id-20260427/dev-report.md:13` — historical reference to Why This Matters domain rename | Date-stamped historical record; leave |
| `docs/architecture/llm-call-sites.md:30` — references `/api/why-this-matters/{ticker}` "with-KG vs without-KG" framing | Architecture audit; route is alive-but-unused per VP-Prod #4. **Optional follow-up:** add a "2026-04-30: alive but unused-by-UI" annotation; deferred to 5B alongside route deletion. |
| `docs/roadmap/prds/PRD-044-centralized-llm-routing.md:18` — references "Agentic Q&A & Impact Refactor" | Historical PRD section header; leave |

## Hits — self-references (skip)

The plan + scope themselves reference these strings to describe the
fixup work. Not real hits.

- `docs/sprints/sprint-prd-004-v5-phase-5a-fixup-20260430/sprint-plan.md` — multiple
- `docs/sprints/sprint-prd-004-v5-phase-5a-fixup-20260430/scope.md` — multiple

## Conclusion

**Zero operator-visible regression risk.** Every operator-visible
string match lives in code being deleted by F8. Internal Python
comments + historical sprint artifacts + architecture audits stay
unchanged (architecture-correct as written; backend routes stay
alive until 5B).

**EXECUTE may proceed.** Optional 5B follow-up: annotate
`docs/architecture/llm-call-sites.md:30` with the 2026-04-30
unused-by-UI status, alongside the full backend route deletion.

— Dev team, 2026-04-30
