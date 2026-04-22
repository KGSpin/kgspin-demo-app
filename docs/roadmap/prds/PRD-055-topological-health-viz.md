# PRD-055: Topological Health Visualization

**Status:** Draft
**Author:** VP of Product
**Created:** 2026-04-22
**Last Updated:** 2026-04-22
**Milestone:** Demo Completeness — Multi-Hop Story
**Initiative:** Backlog

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 9 | Every design-partner conversation opens with "why not just use ChatGPT/Claude with our docs?" This viz is the answer every pipeline run carries forward. Surfaces in the main compare view, the KG Explorer (PRD-002), and the "why this matters" benchmark story. |
| **Impact** | 5 | Transformative. Converts the "feels better" graph-RAG pitch into a quantitative, shareable artifact grounded in a 2026 Shanghai NYU benchmark. Makes the asymmetry between KG extraction and LLM extraction visible at a glance instead of narrated. |
| **Confidence** | 0.85 | Backed by published empirical result (Dongzhe et al., arXiv:2604.09666, April 2026): graph-RAG wins multi-hop Q&A by 26–32 points over dense-RAG even *with* agentic retrieval loops. Topological metrics (connectivity, bridge count, degree distribution) are standard graph-theoretic measures — low implementation risk. Small confidence discount for UX discovery on which metrics land best with non-technical viewers. |
| **Effort** | 2 (M) | One sprint for the metric-computation service + per-graph badge; one sprint for the side-by-side compare drawer + the multi-hop Q&A scoring hook in PRD-004's "Why did revenue drop in Q3?" flow. |
| **RICE Score** | **(9 × 5 × 0.85) / 2 = 19.1** | Highest-scoring Backlog PRD; clears the bar to join the active milestone. |

---

## Goal

Surface a per-extraction **Topological Health Score** plus drilldown metrics that make graph-RAG's structural advantage over dense/agentic-RAG visible to non-technical viewers. Demo operators should be able to point at two panels (KG and LLM) and say: "This number is why the first one is correct on multi-hop questions and the second one isn't."

The score is grounded in the finding from *Dongzhe et al., "Do We Still Need Graph RAG? Benchmarking RAG in the Age of Agentic Search"* (arXiv:2604.09666, April 2026): graph-based retrieval delivers 26–32 point multi-hop Q&A gains over dense retrieval + agentic orchestration, because agents cannot induce a topological bridge that isn't already present in the retrieved evidence.

## Background

Our demo currently tells this story narratively. The operator explains that graph-RAG is better for multi-hop. The viewer nods. Nobody walks away with a shareable artifact.

The April 2026 Shanghai NYU RAGSearch benchmark (github.com/FanDongzhe123/RAGSearch) produced the most cite-able evidence to date that **explicit topology is the load-bearing element** in multi-hop reasoning. Key findings from the paper's results table:

- Dense-RAG + agentic loop on two-hop Wiki Q&A: **47%**
- Graph-RAG + agentic loop on the same benchmark: **80%**
- Post-training with GRPO reinforcement learning reduces the gap only marginally (~1pp on training-free → ~1pp on RL-tuned)

The paper's mechanistic explanation maps directly onto what a Topological Health viz would surface:

1. **Recall ceiling** — dense retrievers fail when the bridge entity has low cosine similarity to the query; the chain of thought then has "missing nodes" the agent can't discover. A health score that counts *bridge entities* (articulation points connecting otherwise-disconnected subgraphs) makes this concrete.
2. **Auto-regressive error propagation** — each missing bridge causes downstream steps to drift. A score that measures *connectivity* (largest connected component as a fraction of all nodes) shows whether the graph will support multi-hop traversal at all.
3. **Attention conflict** — when the retrieved context lacks the bridge, the LLM either refuses or hallucinates. A health score side-by-side with the LLM's "answer" panel makes the structural asymmetry obvious.

Our Wave-A…F cleanup landed the extraction pipelines and domain neutralization; the next move is to close the demo narrative loop by *showing the topology*, not describing it.

## Requirements

### Must Have

1. **Topological Health Score** — a single 0–100 score per extracted KG, displayed as a badge on every `/compare` slot panel.
   - Acceptance: every slot in the compare view shows the score within 2s of KG extraction completing. Score is deterministic for a given KG (same extraction → same score). Score is higher for graphs that would support multi-hop Q&A and lower for graphs that are just node lists.

2. **Four metric drilldown panel** — clicking the badge opens a drawer that breaks the score into its four component metrics, each with a one-line "what this means for your question":
   - **Connectivity** (largest connected component / total nodes): "Can the agent walk between related facts?"
   - **Bridge density** (articulation points / total nodes): "How many entities link otherwise-isolated clusters?"
   - **Multi-hop reach** (mean shortest-path length across sampled node pairs): "How many steps to connect any two facts?"
   - **Degree distribution health** (Gini coefficient of node degrees vs. power-law baseline): "Is this a real knowledge structure or just a list?"
   - Acceptance: each metric is numeric, has a ≥3-sentence plain-English explanation, and renders in under 500ms for a 10k-node graph.

3. **LLM-vs-KG comparison mode** — on `/compare`, the "LLM answer" panel also gets a Topological Health Score, computed from whatever structure can be parsed from the LLM's free-text output (named entities + declared relationships). This score will typically be very low; that asymmetry is the pitch.
   - Acceptance: running "What companies has J&J acquired?" on JNJ renders two scores side-by-side. KG score should be visibly higher (hypothesized ≥40pp gap based on RAGSearch benchmark pattern). If the gap is <10pp, we have an extraction problem we need to see.

4. **PRD-004 integration — score-next-to-answer rendering** — the multi-hop scenarios themselves live in PRD-004 v4 (Must-Have #9). PRD-055's job is to render a compact Topological Health badge next to each of the 3 answers in the parallel-run view, so the viewer can form a structural intuition *before* the LLM-as-judge verdict renders.
   - Acceptance: in PRD-004's viewer-first comparison flow, each answer panel shows the pipeline's KG Topological Health score at the top. The LLM-as-judge never sees this score (it is blinded to signals beyond the raw answer text); the *viewer* does. Most often the judge's ranking and the score ranking will agree — when they don't, it's a discussion point (see PRD-004 Nice-to-Have "Judge-disagreement surfacing").

5. **Citable in the benchmark narrative** — the drawer has a footer link to the RAGSearch paper + the repo, and a one-sentence claim like: *"Graph-RAG wins multi-hop by 26pp in the April 2026 Shanghai NYU benchmark — this score is why."*
   - Acceptance: paper + repo links are live; claim text is the demo operator's talking-track anchor.

### Nice to Have

1. **Per-strategy topology overlay** — when the operator has run multiple KGSpin strategies (Discovery Rapid, Deep, Signal Fan-Out) on the same ticker, overlay the four metrics as a spider chart. Different strategies produce different topologies; the overlay shows each one's strengths visually.
   - Acceptance: operator can eyeball which strategy gives the highest bridge density for their corpus.

2. **Historical trend** — track the Topological Health Score of each extracted KG over time in the run log, so we can see whether Wave-A…F extraction changes regressed or improved structure.
   - Acceptance: admin endpoint returns last-N scores for a given (doc_id, strategy); no regression gate yet, just observability.

3. **"Bridge entity" highlight in the graph viz** — colorize articulation points in the graph rendering so the viewer can visually trace which entities are doing load-bearing work.
   - Acceptance: hovering a highlighted node shows "this entity connects N subgraphs" with the counts.

## Technical Design (High-Level)

This is a product PRD, not an implementation spec — VP of Engineering owns the ADR. But the integration shape:

- **Metric-computation service** lives in `kgspin-core` (new module: `graph_topology/health.py`). Pure function: `(kg: KnowledgeGraph) → TopologicalHealth { score, connectivity, bridge_density, mean_hop_length, degree_gini }`. No new deps needed — `networkx` is already in the kgspin-core requirements.
- **Demo-app frontend** calls the score via a new `/api/topology-health/{slot}` endpoint that reads from the existing slot KG state (no separate cache; piggyback on the Wave-F cache-key fix).
- **LLM-side score** piggybacks on the existing `_fetch_newsapi_articles` parsing path — pull named entities + declared relations from the LLM response, build a micro-graph, score it.
- **Cache invalidation** is automatic — the score is a pure function of the KG, and the KG cache is now correctly keyed per strategy (see the Apr-22 fix commit `ee8be81`).

> Requires ADR — **ADR-012: Topological Health Score Definition**. VP of Engineering owns the choice of which four metrics enter the score and their weighting. Options: equal-weight mean; learned-weighting from a small benchmark set; domain-specific weighting. Recommend equal-weight for v1 to avoid over-fitting to demo corpus.

## Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| % of demo operators who reference the Topological Health Score in their pitch within 30 days of ship | ≥70% | Self-report post-demo survey + sales call transcript review |
| Multi-hop Q&A Topological Health gap (KG − LLM) on the RAGSearch-style benchmark subset | ≥25pp mean gap across 20 questions | Internal benchmark harness, run weekly |
| Operator-reported "felt more concrete" rating on the pitch compared to the pre-PRD-055 demo | ≥4/5 | Post-demo internal NPS |
| Render-time p95 for a 10k-node graph | <500ms | Frontend perf instrumentation |
| Regression gate: no existing `/compare` flow regresses on `kgs_vs_llm` token/latency | 0 regressions | Existing Playwright boot probe |

## Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| PRD-004 (KG vs LLM Comparison Demo) | **Blocks** — PRD-055 extends its narrative, requires the compare UI scaffolding | Approved |
| PRD-002 (KG Explorer) | Soft — nice-to-have "bridge entity highlight" renders best in the explorer | Approved |
| ADR-012 (Topological Health Score Definition) | **Blocks** — VP Eng needs to pick the four metrics + weighting | Not yet written |
| networkx dependency in kgspin-core | Soft — already present; confirm version supports articulation-point detection | Present |
| RAGSearch paper + repo access for the footer citation | Reference | arXiv:2604.09666, github.com/FanDongzhe123/RAGSearch |

## Open Questions

1. **Q1:** Should the Topological Health Score penalize graphs with **too many** components equally to graphs with **too few** edges? A fragmented graph and a sparse graph both fail multi-hop, but they fail differently. **Owner:** VP of Engineering (ADR-012).

2. **Q2:** For the LLM-side score, how do we parse "structure" from free text reliably? Options: (a) prompt the LLM to emit entities + relations in JSON, (b) run our own extractor on the LLM's answer, (c) use a small deterministic NER + relation parser. Option (b) risks circular logic (we grade the LLM by running our pipeline on its output). Option (a) makes the comparison unfair (we're asking the LLM to adopt our format). **Recommend option (c).** **Owner:** VP of Engineering (ADR-012).

3. **Q3:** The RAGSearch paper uses a 7B model for both graph-RAG and dense-RAG baselines. Our demo uses Gemini 2.5 Flash (much larger). Does the 26–32pp gap hold at our scale? **Owner:** VP of Product — commission a small internal benchmark replication as part of the "nice to have #2" historical trend work. If the gap compresses to <10pp at Gemini scale, the narrative changes and we should refine the messaging before shipping.

4. **Q4:** ~~Do we ship the per-domain multi-hop questions (Must-Have #4) in this PRD, or spin that into a separate PRD-056 "Multi-Hop Benchmark Suite"?~~ **Resolved 2026-04-22 by CEO:** bundle it all — multi-hop scenarios live in **PRD-004 v4** (the narrative), Topological Health scores live in **PRD-055** (the structural evidence). No PRD-056. Must-Have #4 here pivots to "render the PRD-055 score next to each PRD-004 multi-hop answer" — the score lights up the narrative instead of carrying its own scenario pack.

5. **Q5:** What happens to the score when extraction legitimately produces a *small* graph (e.g., a very short filing, a single-page trial summary)? A 3-node graph is trivially well-connected and would score 100/100, which is misleading. **Recommend:** gate the score behind a minimum-size threshold (e.g., ≥20 nodes), show "insufficient structure to score" below that. **Owner:** VP of Engineering (ADR-012).

## Changelog

| Date | Change | By |
|------|--------|-----|
| 2026-04-22 | Created — grounded in arXiv:2604.09666 (Shanghai NYU RAGSearch benchmark, April 2026) and transcript commentary. | VP of Product |
| 2026-04-22 | Q4 resolved by CEO: bundle with PRD-004 v4 (multi-hop scenarios live there; PRD-055 renders the score next to each answer). Must-Have #4 rewritten to focus on the score-next-to-answer rendering, not a scenario pack. No PRD-056. | VP of Product |

— Prod
