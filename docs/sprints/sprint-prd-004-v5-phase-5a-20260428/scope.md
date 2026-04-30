# PRD-004 v5 Phase 5A — Scope

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-28
**Branch:** `sprint-prd-004-v5-phase-5a-20260428` (off `main` @ `f99bb7e`)
**Spec source:** PRD-004 v5 on branch `sprint-prd-004-v5-rag-vs-graphrag-20260428` (not yet merged; treated as authoritative)
**Reference impl:** `/Users/apireno/repos/RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning` (arXiv:2509.22009)

---

## What we are building

A reframing of `/compare` from "compare 3 KG pipelines on multi-hop" (v4) to "compare RAG retrieval strategies" (v5). Two scenarios live on `/compare`:

- **Scenario A** — one-shot question; left pane = pure dense RAG; right pane = GraphRAG with toggle A1/A2/A3.
- **Scenario B** — multi-hop templated scenario with placeholders; three panes (left = agentic dense, center = paper-mirror GraphSearch, right = tool-agent placeholder for Phase 5B).

The deliverable shifts the comparison axis from extraction quality (which moves to the per-graph view in PRD-002 territory) to retrieval quality.

## What is in scope (Phase 5A)

| ID | Deliverable | LOC est. | Wall-time est. |
|---|---|---|---|
| A | `scripts/build_rag_corpus.py` — chunker + embedder + BM25 builder + graph node/edge embedder + manifest | ~250 | 3h |
| B | `services/dense_rag.py` — BM25 + cosine + RRF over chunks | ~120 | 1.5h |
| C | `services/graph_rag.py` — three modes A1/A2/A3 with `aquery_context` API | ~250 | 2h |
| D | `services/agentic_dense_rag.py` — Search-o1-style decompose/retrieve/iterate | ~180 | 2h |
| E | `services/graphsearch_pipeline.py` — paper-mirror dual-channel pipeline + vendored prompts | ~400 | 4h |
| F | `services/scenario_resolver.py` + `demos/extraction/multihop_scenarios_v5.yaml` (5 fin + 1 clinical hedge) | ~120 + YAML | 1.5h |
| G | `tests/fixtures/multi-hop-gold/{scenario}/{ticker}.gold.json` × 11 (5 fin × 2 tickers + 1 clinical × JNJ) | ~JSON only | 4h (CTO HITL, incremental) |
| H | 4 new `/api/scenario-{a,b}/{run,analyze}` endpoints + SSE + F1 with key_fields | ~380 | 2h |
| I | Frontend rework: remove v4 multi-hop UI, add Scenario A view + Scenario B view (2-pane default + Show-advanced toggle) + ticker dropdown + per-graph deep-link | ~700 JS+HTML/CSS | 3h |
| J | v4 frontend deletion only (backend routes kept alive — see plan §2.3 / VP-Prod major #4) | ~−400 frontend | 0.5h |
| K | Smoke test + dev report | ~80 | 1.5h |

**Total:** ~2,700 LOC net add, ~24h tasks, 16h wall-clock cap. Parallel-tractable subset: A/B/C/D/E (services + corpus) parallel with F/G (templates + gold) parallel with I (frontend scaffolding). H glues services to UI.

## What is explicitly out of scope (Phase 5B / 5C)

- **Tool-agent pane LIVE wiring** (Scenario B right pane) — Phase 5B. The pane exists in HTML behind a "Show advanced" toggle (default hidden) with Phase-5B-preview content per PRD §3 #10.
- **Additional clinical scenario templates (4 of the 5)** — Phase 5B; 5 fin + 1 clinical hedge in 5A.
- **Clinical gold fixtures beyond 1** — Phase 5B (after additional clinical templates land).
- **Multi-doc / cross-ticker GraphRAG** — Phase 5C.
- **Microsoft GraphRAG community-summary variant** — Phase 5C.
- **Real Layer-2 evaluation infra** beyond hand-authored gold for 2 fin tickers × 5 scenarios.
- **Judge-disagreement surfacing vs. Topological Health** — nice-to-have per PRD-004 v5 §3, deferred.
- **Judge-model swap UI** — nice-to-have, deferred.
- **Visual diff highlighting between LLM runs** — v3-era nice-to-have, deferred.

## Hard caps (per CTO assignment)

- **Wall-clock cap on EXECUTE: 16 hours.**
- **Turn budget for EXECUTE: 350.**
- **Commits expected: 12** (per plan §10, VP-Eng split applied). Standard `Co-Authored-By` trailer.
- **Push branch, NOT merge.**

## Non-goals (re-stated from spec to prevent scope creep)

- No accuracy benchmarking beyond F1 vs hand-authored gold for fin scenarios.
- No new Gemini alias registration; reuse `gemini_flash` alias already used by Wave G judge.
- No DB migration — per-doc JSON + numpy + `rank_bm25` only (CEO direction 2026-04-28).
- No persistence of in-session `_kg_cache`; the corpus builder produces graph fixtures separately via the new `run_fan_out_extraction` public helper (see plan §2.2, §3.A).
- No changes to extraction pipelines — graph artifacts come from running existing fan_out extraction once at corpus-build time.
- **No deletion of v4 backend routes in 5A** — only UI buttons removed; backend routes stay alive until 5B (per VP-Prod major #4 mitigation).

## Branch and commit strategy

- New branch `sprint-prd-004-v5-phase-5a-20260428` off `main`.
- Spec PR (`sprint-prd-004-v5-rag-vs-graphrag-20260428`) is read but NOT rebased onto — too much risk of pulling unrelated docs changes mid-sprint. Spec is treated as authoritative reading material.
- 12 commits per plan §10; one per logical area, with v4 deletion (commit 9a) split from Scenario A addition (commit 9b) per VP-Eng major #1 to keep diffs reviewable.
- Push to origin, do NOT merge.

## Completion criteria

- [ ] All 11 deliverables landed on branch
- [ ] All unit tests green (per-service + scenario resolver + judge)
- [ ] End-to-end smoke: corpus build for AAPL + JNJ → Scenario A run → Scenario A analyze → Scenario B run → Scenario B analyze → 200s with non-empty payloads
- [ ] Manual UI smoke: load `/compare`, run Scenario A in all 3 modes, run Scenario B for all 5 templates × 2 tickers, analyze both, screenshots in dev-report
- [ ] Dev report at `docs/sprints/sprint-prd-004-v5-phase-5a-20260428/dev-report.md` with top 5 surprising findings
- [ ] Branch pushed to `origin/sprint-prd-004-v5-phase-5a-20260428`

— Dev team
