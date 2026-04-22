# Wave G — kgspin-demo-app: Demo Close — Dev Report

**From:** Dev team (kgspin-demo-app) via CTO mop-up
**To:** CTO
**Date:** 2026-04-22
**Branch:** `wave-g-demo-close`
**Status:** DONE (5 commits landed, 282 tests green, manual UI smoke green)

## Commits landed

1. `20c22c2` — `feat(demo): multi-hop scenario YAML + loader + scenarios endpoint`
2. `7ed0e83` — `feat(demo): LLM-as-judge Gemini Flash evaluator with blinded ranking`
3. `c48aaa7` — `feat(demo): topological health adapter + LLM-answer micro-graph builder` (+ contract self-test)
4. `b360345` — `feat(demo): /api/multihop/run parallel pipeline fan-out endpoint`
5. `f6b5318` — `feat(demo): viewer-first multi-hop UI + topology health badge & drawer`

Single branch; plan's 5a/5b split threshold (~600 LOC) was breached only narrowly (621 LOC) so commit 5 stayed atomic per the data-action-registry + markup atomicity argument in the plan.

## PRD coverage

- **PRD-004 v4 Must-Haves:** #9 (multi-hop scenario pack, commit 1), #10 (all-displayed-plays parallel execution, commit 4), #11 (LLM-as-judge, commit 2), #12 (viewer-first comparison flow, commit 5).
- **PRD-055 Must-Haves:** #1 (score badge, commit 5), #2 (four-metric drilldown drawer, commit 5), #3 (LLM-vs-KG comparison — both panels get a score, commit 3 + commit 5), #5 (RAGSearch citation footer, commit 5).

## Cross-repo handshake

- kgspin-core's `wave-g-topological-health` branch merged to main (`0fce341`) with the full `TopologicalHealth` + `compute_health` contract shipped.
- Contract self-test at `tests/unit/test_topological_health_contract.py` passes.
- Sentinel shim in `src/kgspin_demo_app/services/topology_health.py` still present (harmless; graceful degrade path).

## Tests

- `pytest` full suite: **282 passed, 4 skipped** (pre-Wave-G was 247 passed — +35 new tests for scenarios loader, judge parser, micro-graph builder, topology contract self-test, /api/multihop/run integration test).
- Playwright smoke at `tests/e2e/test_multihop_smoke.py` — gated on `PLAYWRIGHT_E2E=1`, not run by default.

## Scope deliberately NOT attempted (deferred per assignment non-goals)

- No extractor pipeline changes.
- No historical trend storage for Topological Health.
- No judge-model swap UI.
- No bridge-entity graph colorization.
- No per-strategy spider chart.
- No judge-disagreement surfacing.

## Known issues / follow-ups

- **SEC lander submission-bundle bug** (surfaced during CEO demo 2026-04-22): `landers/sec.py` fetches the full EDGAR submission bundle (`.txt`) instead of the primary 10-K HTML. JNJ's 2026-02-11 filing is 24 MB with 167 concatenated documents, producing 5,773 extraction chunks and multi-hour runs. **Being fixed in a follow-up patch** outside Wave G scope: restore edgartools + `filing.html()` + full Company metadata capture.
- Playwright infrastructure is new to the repo. One smoke test ships; expanding coverage is a future sprint.
- Background `cross-repo PRD-058/059/060` work for Intelligence (PRD-056) is next.

## Session budget note

Three back-to-back `claude -p` invocations hit the 50-turn limit (commits 5 UI + Playwright took the lion's share of the turn budget due to HTML/CSS verbosity). CTO mopped up the final commit + dev-report manually. No blocking issues.

— Dev (via CTO)
