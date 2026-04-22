# PRD-004: KG vs LLM Comparison Demo

**Status:** Approved (v4 — Multi-Hop Scenario + LLM-as-Judge)
**Milestone:** 4
**Effort:** M → L (v4 adds the multi-hop scenario engine + judge call)
**Dependencies:** PRD-001 (Operational DB + Entity Resolution in kgspin-core), PRD-002 (KG Explorer — for citation links), PRD-055 (Topological Health — score renders next to each answer)
**Last Updated:** 2026-04-22

---

## 1. Goal

Build a side-by-side comparison page at `/compare` that answers the same question using the knowledge graph (instant, cited, deterministic, free) and a raw LLM (slow, uncited, non-deterministic, paid) — proving why deterministic KGs are superior for structured knowledge retrieval.

## 2. Background

The single most common objection from potential design partners is: "Why not just use ChatGPT/Claude with our documents?" This page is the answer.

We must prove this not just on highly structured tables (like 10-K filings) but also on unstructured narratives (like financial news). Currently, pure RAG outperforms our Graph RAG on unstructured news because we fail to extract causal events (e.g., "Revenue dropped due to supply chain issues"). 

To deliver a high-fidelity demo today for FinServ and tomorrow for AdTech, we must build scalable, domain-agnostic extraction logic to capture these narrative events without hardcoding industry-specific rules.

## 3. Requirements

### Must Have

1. **Split-screen layout**: Left = KG answer, Right = LLM answer
2. **Pre-loaded demo questions** as clickable buttons:
   - "Who is the CEO of AMD?"
   - "What companies has J&J acquired?"
   - "What adverse events are associated with Stelara?"
   - "Why did revenue drop in Q3?" *(Requires unstructured event extraction)*
3. **Free-text query input** for custom questions
4. **Generic Causal Extraction:** The L-Module (or a new deterministic module) must be capable of extracting `[Actor/Entity] -> caused -> [Event]` relationships from narrative text. This logic must be driven by the `.yaml` bundle, ensuring the exact same code can extract FinServ causality ("demand caused revenue spike") as AdTech causality ("cookie deprecation caused CPM drop").
5. **KG answer panel** showing:
   - Answer text
   - Citations (clickable links to ProvenanceView in explorer)
   - Confidence score(s)
   - Latency (ms)
   - Cost ($0.00)
6. **LLM answer panel** showing:
   - Answer text
   - "No structured citations available" note
   - Latency (ms)
   - Cost estimate (~$0.001)
7. **"Run Again" button** that re-executes both queries and highlights any differences
8. **Comparison callout cards** below: Determinism, Provenance, Cost, Speed

### Must Have (v4 additions — Multi-Hop Scenario + LLM-as-Judge)

9. **Multi-hop scenario pack** — a curated set of questions per domain that each require ≥2 relational hops to answer, modeled on the atomic-decomposition style used in the RAGSearch benchmark (arXiv:2604.09666).
   - Scenarios are **qualitative** in the demo — no labeled ground-truth, no scorecard. The demo viewer forms their own judgment, aided by the LLM-as-judge rendering (req. 11).
   - Minimum one scenario per live domain at ship:
     - **Financial:** "Which of J&J's acquired companies since 2020 are now facing active litigation, and what product lines does that touch?" (hops: J&J → acquired companies → litigation events → product mapping)
     - **Clinical:** "Which adverse events reported in Stelara trials also appear in other J&J immunology trials in the same patient age bracket?" (hops: Stelara → adverse events → other trials in same program → age-filter)
   - Acceptance: each scenario runs end-to-end and produces a final answer from every pipeline in the compare view without the operator stitching queries together manually.

10. **All-displayed-plays parallel execution** — one click ("Run Multi-Hop") runs the selected scenario across **all currently-displayed pipelines** in the compare slots (typically 3: e.g. Signal Fan-Out, Agentic Flash, Agentic Analyst). Each pipeline executes its own decomposition/retrieval/synthesis using its native machinery — KG pipelines traverse the graph; LLM pipelines do free-text reasoning over the raw corpus.
    - Acceptance: the 3 answers land in the same view, same moment, labeled by pipeline. Cost + latency per answer is displayed so the viewer sees the cost-of-quality tradeoff.

11. **LLM-as-judge evaluator** — after the 3 answers render, a **single simple LLM call** (cheap model, e.g. Gemini 2.5 Flash) receives: (a) the question, (b) the 3 answers labeled A/B/C (pipeline identities hidden from the judge to reduce bias), (c) a rubric asking which answer is most specific, most complete, and least speculative. The judge returns a ranked verdict + one-sentence rationale per answer.
    - Acceptance: verdict renders under the 3 answers within 5s of the last answer completing. The viewer can reveal which pipeline produced which answer after forming their own view. Judge call is idempotent (same inputs → deterministic-ish verdict; we pin temperature=0).
    - Anti-gaming: the judge never sees pipeline labels, topology scores, or citations — only the raw answer text. This keeps the judge narrative ("it just looked better") aligned with how a human reader would evaluate.

12. **Viewer-first comparison flow** — the UI ordering deliberately shows the 3 raw answers *before* the LLM judge's verdict. The viewer forms their opinion, then the judge verdict appears, then (on click) the pipeline identities are revealed. This sequencing is the pitch — it lets the demo viewer confirm with their own reasoning that the graph-RAG answer reads best, instead of being told.
    - Acceptance: default render hides pipeline labels on answers; a "Reveal pipelines" button unmasks them. Judge verdict renders between the answers and the reveal.

### Nice to Have

- Accuracy scoring (human-labeled ground truth for demo questions) — deferred; v4 is intentionally qualitative per CEO direction 2026-04-22. Re-visit if a design partner asks for a benchmarked scorecard.
- Visual diff highlighting between LLM runs
- **Judge-disagreement surfacing** — when the judge's ranking disagrees with the Topological Health Score ordering from PRD-055, flag it as a "discussion point" in the UI. Most of the time they'll agree (topology → answer quality); when they don't, that's a teaching moment about our extraction quality.
- **Judge model swap** — allow the operator to swap the judge model (Gemini Flash / Opus / Claude Sonnet) to show that the verdict is robust across judges, not a single model's idiosyncrasy.

## 4. Technical Design (Demo Specific)

### New Files

| File | Purpose |
|------|---------|
| `src/kgspin_demo/query_engine.py` | Natural language -> SurrealQL translator (template-based) |
| `src/kgspin_demo/api/compare_routes.py` | `/api/v1/compare/*` endpoint implementations |
| `demos/extraction/multihop_scenarios.yaml` *(v4)* | Curated per-domain multi-hop scenarios — question text + expected hop structure for qualitative narration, not scoring |
| `demos/extraction/judge.py` *(v4)* | Single-call LLM-as-judge: prompt template, answer-labeling (A/B/C blinding), rubric, deterministic verdict parser |
| `/api/multihop/run` *(v4)* | New endpoint: fan the scenario across all displayed pipelines, gather answers, call the judge, return a merged payload for the UI |

### v4 Architecture Note: RAGSearch-Inspired Flow

The v4 multi-hop flow mirrors the four-stage graph-search loop from arXiv:2604.09666 *within each pipeline*, but the comparison itself is flat:

- **Per pipeline:** decompose → retrieve → reason → synthesize (native to each pipeline's machinery; KG pipelines do this via graph traversal, LLM pipelines via their own prompting).
- **Across pipelines:** render the 3 synthesis outputs side-by-side → LLM-as-judge ranks them → viewer reveals pipeline identities.

The judge is deliberately kept **simple and single-shot** — no multi-turn, no agent, no retrieval augmentation. The point is not to build a better agent; it is to show that even a naive LLM-as-judge, given only the answer text, consistently prefers the structurally-grounded answer. This is the demonstrable echo of the RAGSearch finding that topology carries the load.

### 5. RICE Analysis (v4)

| Factor | Value | Rationale |
|---|---|---|
| Reach | 9 | v3 was 8; v4 raises Reach because the multi-hop scenario + judge is the exact shape of the question every technical evaluator asks after the first-call demo ("OK but can it reason?"). Now we answer it in one click. |
| Impact | 5 | Unchanged — still transformative. The judge verdict turns "we think graph is better" into "an independent model agrees, here's the one-sentence rationale." |
| Confidence | 0.8 | v3 was 0.9; v4 steps down 0.1 because LLM-as-judge introduces a small prompt-engineering surface and a non-zero chance of demo-day flakiness (judge returns degenerate ranking). Backed by the RAGSearch benchmark for the underlying claim, but the judge UX itself is unvalidated. |
| Effort | 3.0 (L) | v3 was 2.0. +1 sprint for the scenario pack + parallel-run wiring + judge call + blinded-reveal UI. |
| **Score** | **12.0** | v3 was 18.0. The headline drop reflects the wider effort, not reduced importance — v4 *replaces* v3's pitch with a stronger one and stays the critical First-Call asset. |

---

## Changelog

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Relocated to kgspin-demo and updated RICE score. | Prod |
| 2026-04-19 | Sprint 11: RICE Confidence 1.0 → 0.9 per VP Prod 2026-04-17 consultation — real news backends add integration surface worth factoring in. Status unchanged (Approved). | Dev |
| 2026-04-22 | v4: added Must-Have #9–12 (multi-hop scenario pack, all-displayed-plays parallel execution, LLM-as-judge with blinded ranking, viewer-first comparison flow). Approach mirrors RAGSearch benchmark (arXiv:2604.09666, Shanghai NYU, April 2026) — qualitative rather than benchmarked per CEO direction. RICE drops 18 → 12 on effort increase. PRD-055 (Topological Health) added as soft dependency — its score renders next to each answer to reinforce why the judge ranks them as it does. | Prod |
