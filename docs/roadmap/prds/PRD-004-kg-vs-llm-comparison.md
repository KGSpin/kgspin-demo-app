# PRD-004: KG vs LLM Comparison Demo

**Status:** Approved (v5 — RAG-vs-GraphRAG axis with Scenario A one-shot + Scenario B multi-hop, paper-mirror pipeline + optional tool-agent toggle)
**Milestone:** 4
**Effort:** L → XL (v5 reframes the comparison axis from cross-KG to RAG-vs-GraphRAG; mirrors the RAGSearch / GraphSearch paper pipeline)
**Dependencies:** PRD-001 (Operational DB + Entity Resolution), PRD-002 (KG Explorer — for citation links), PRD-055 (Topological Health — score renders next to each answer)
**Last Updated:** 2026-04-28

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

### Must Have (v5 — Reframed Comparison Axis: RAG vs GraphRAG)

**v5 scope shift (2026-04-28):** v4's "compare 3 KG variants on multi-hop" is removed from `/compare`. The KG-quality comparison stays in the existing 3-graph side-by-side view (PRD-002 territory). The `/compare` page now compares **retrieval strategies**, not extraction pipelines. Two scenarios:

#### Scenario A — One-shot Open RAG vs GraphRAG (the "Why" demo)

9. **Single-question open RAG comparison.** Free-text question; two answer panes:
   - **Pane left: Dense RAG** — pure chunk → embed → cosine retrieve → LLM answer. ZERO graph. The vanilla baseline a typical AI engineer would build.
   - **Pane right: GraphRAG** — toggleable between three patterns:
     - **A1.** Dense over chunks (baseline; same as left pane — for sanity)
     - **A2.** Dense over chunks + extracted KG context retrieved via chunk-to-graph correlation (chunk → matching entities → 1-hop neighborhood added to context)
     - **A3.** Semantic + BM25 search over the graph itself (nodes + edges); subgraph rendered as the result; the **evidence text spans** referenced in matched edges/nodes are pulled into the LLM context window
   - **Scope:** per-document. User picks a ticker (or trial document); both panes operate on that document's extraction artifacts.
   - **UI:** text input + side-by-side answer panes + retrieved-context viewer (chunks for left; subgraph + spans for right) + "Analyze Results" button.
   - **Replaces:** v4's per-graph "Why this matters" tab on each individual graph view (the value-prop content moves from per-graph WTM to this comparison page; per-graph view retains a single-shot example that links to this page).

#### Scenario B — Multi-hop with Decomposition: Agentic Dense RAG vs Paper-Mirror GraphRAG

10. **Multi-hop decomposition comparison.** Templated multi-hop scenario with `{company}`, `{ticker}`, etc. variables; three answer panes:
    - **Pane left: Agentic Dense RAG (Search-o1-style)** — agent decomposes question into sub-queries; for each, retrieves chunks via dense vector search; iterates. **No graph.** This is the strong baseline per RAGSearch's claim — graph-RAG must beat agentic dense, not just naive dense.
    - **Pane center: Paper-Mirror GraphSearch (RAGSearch architecture)** — implements the deterministic dual-channel pipeline from `arXiv:2509.22009` / `RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning`:
      - Seed retrieval (`graph.aquery_context(question)`) → text + KG dual summary
      - **Dual decomposition:** text-side sub-queries (with `#` placeholders for chaining) + KG-side relational sub-queries
      - **Text channel iterative loop:** complete placeholders, retrieve, semantic-filter, summarize, answer; accumulate history
      - Text draft answer + evidence verification + optional query expansion
      - **KG channel iterative loop:** same structure but relational filter, KG-style decomposition
      - Merge both channels → final answer
      - **Faithful to the paper.** This is the primary value-prop demo.
    - **Pane right: Tool-Agent GraphRAG (advanced toggle, hidden by default)** — modern AI-engineer-pattern alternative: an agent with `retrieve_text(query)` and `retrieve_graph(query)` tools (with `bm25_search_graph` + `cosine_search_graph` variants) that picks tools iteratively. Toggle revealed via "Show advanced" button. Acknowledges that the paper doesn't use tools while still surfacing the pattern most modern AI engineers would build.
    - **UI:** scenario dropdown (10 templated scenarios) + ticker selector + "Run Multi-Hop" button + side-by-side panes + "Analyze Results" button.

11. **Scenario templates (CRITICAL: company is a variable, not a literal).** All scenarios are TEMPLATES with `{company}`, `{ticker}`, `{jurisdiction_filter}`, `{sponsor}` placeholders. Templates resolve at runtime against the selected document's metadata. Anti-pattern (do NOT do): hardcoding "JNJ" or "Stelara" in scenario text. Same lesson as the multi-hop YAML genericization landed 2026-04-27.
    - **Financial scenarios (5 templates):** must operate on any 10-K with no code change. Example shapes:
      - "Among {company}'s subsidiaries listed in Exhibit 21, which operate in jurisdictions where {company} reports active litigation in Item 3?"
      - "Of {company}'s named executives whose total compensation exceeded the median of S&P 500 NEOs in fiscal {year}, how many had stock awards as the largest single component?"
      - "Which of {company}'s reportable operating segments have revenue exceeding the largest single litigation accrual disclosed in the contingencies note?"
      - "Among {company}'s top suppliers/customers concentrations disclosed in MD&A, which carry termination/renegotiation rights triggered by {company}'s ongoing M&A activity?"
      - "What is the intersection of {company}'s outstanding warrants/options and the executives mentioned in the proxy compensation discussion who hold those securities?"
    - **Clinical scenarios (5 templates):** similar shape, sponsor / drug / phase / endpoint as variables. Examples deferred to a clinical-domain pass.
    - **Acceptance:** dropdown shows all 10; user picks ticker + scenario; system resolves placeholders and runs all 3 panes.

12. **Deterministic ground-truth answers (per scenario × per ticker).** Each scenario's answer for a given document is hand-derivable from the document itself. v5 ships hand-authored gold answers for ≥2 exemplar tickers per scenario (≥10 fin-side answer keys + ≥10 clin-side once clinical scenarios authored). Gold lives at `tests/fixtures/multi-hop-gold/{scenario_id}/{ticker}.gold.json`.
    - This is what makes Scenario B **scientific** rather than purely qualitative. F1 vs gold is computable.
    - For Scenario A (open-ended), no gold — qualitative LLM-judge only.

13. **"Analyze Results" button (per scenario).** Two distinct behaviors:
    - **Scenario A button:** blind-taste-test LLM judge. Sees the question + 2 answers (A/B blinded); produces a qualitative verdict on which is more thorough, better-grounded, less speculative. No F1 scoring.
    - **Scenario B button:** dual evaluation —
      - (a) F1 vs hand-authored gold (when available for the selected ticker × scenario combo)
      - (b) Blind-taste-test LLM judge on top, for cases where gold is partial or for human-readable rationale
      - Display both: F1 score box + judge rationale block per pane.
    - Single click triggers; produces a separate result card per scenario.

14. **Move v4's "all-displayed-plays" cross-KG comparison out of `/compare`.** The current "run scenario across 3 graphs simultaneously" feature lives in the per-graph viewer (PRD-002). On `/compare`, the 3 panes are RAG STRATEGIES, not pipelines.
    - The 3-graph cross-pipeline comparison is a separate UX in the per-graph view, used to compare extraction quality between fan_out / agentic_flash / agentic_analyst. **It does NOT compare RAG quality.**
    - Per-graph "Why this matters" tabs each get a single multi-hop demo example (resolved against that ticker) that links to the new `/compare` for the full demo.

15. **Phased delivery (LOE-driven):**
    - **Phase 5A (ship first):** Scenarios A1, A2, A3 + Scenario B Pane Left (agentic dense) + Pane Center (paper-mirror GraphSearch). Hand-authored gold for 2 fin tickers × 5 scenarios = 10 answer keys.
    - **Phase 5B (follow-on):** Scenario B Pane Right (tool-agent toggle) + clinical 5 templates + clinical gold authoring.
    - **Phase 5C (later):** multi-document graph extension (cross-ticker), Microsoft GraphRAG community-summary variant.

### Nice to Have

- Accuracy scoring (human-labeled ground truth for demo questions) — deferred; v4 is intentionally qualitative per CEO direction 2026-04-22. Re-visit if a design partner asks for a benchmarked scorecard.
- Visual diff highlighting between LLM runs
- **Judge-disagreement surfacing** — when the judge's ranking disagrees with the Topological Health Score ordering from PRD-055, flag it as a "discussion point" in the UI. Most of the time they'll agree (topology → answer quality); when they don't, that's a teaching moment about our extraction quality.
- **Judge model swap** — allow the operator to swap the judge model (Gemini Flash / Opus / Claude Sonnet) to show that the verdict is robust across judges, not a single model's idiosyncrasy.

## 4. Technical Design (Demo Specific)

### New / Updated Files (v5)

| File | Purpose |
|------|---------|
| `src/kgspin_demo/query_engine.py` | Natural-language → SurrealQL translator (template-based; v3) |
| `src/kgspin_demo/api/compare_routes.py` | `/api/v1/compare/*` endpoint implementations (v3) |
| `demos/extraction/multihop_scenarios.yaml` *(v5 — TEMPLATED)* | 10 templated scenarios with `{company}` / `{ticker}` / `{sponsor}` placeholders. Anti-pattern: hardcoded ticker literals. Resolves at runtime against the selected document. |
| `tests/fixtures/multi-hop-gold/{scenario_id}/{ticker}.gold.json` *(v5)* | Hand-authored deterministic ground-truth answers per scenario × ticker. ≥2 fin tickers shipped in Phase 5A. |
| `src/kgspin_demo_app/services/dense_rag.py` *(v5)* | Pure dense-RAG retrieval (chunk → embed → cosine). Used by Scenario A left pane and Scenario B pane left. |
| `src/kgspin_demo_app/services/graph_rag.py` *(v5)* | GraphRAG retrieval with three patterns A1/A2/A3. Provides `aquery_context(question, mode)` API mirroring the GraphSearch paper's interface. |
| `src/kgspin_demo_app/services/agentic_dense_rag.py` *(v5)* | Agentic dense-RAG (Search-o1-style: decompose → retrieve → answer → loop). Scenario B pane left. |
| `src/kgspin_demo_app/services/graphsearch_pipeline.py` *(v5)* | Faithful mirror of `RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning` — dual-channel (text + KG), iterative, with evidence verification + query expansion. Scenario B pane center. |
| `src/kgspin_demo_app/services/tool_agent_graph_rag.py` *(v5, Phase 5B)* | ReAct-style agent with `retrieve_text`, `retrieve_graph`, `bm25_search_graph`, `cosine_search_graph` tools. Scenario B pane right (advanced toggle). |
| `src/kgspin_demo_app/services/scenario_resolver.py` *(v5)* | Resolves `{company}` / `{ticker}` / etc. placeholders in scenario templates against selected document metadata. |
| `demos/extraction/judge.py` *(v5 — extended)* | Two judge modes: blind-A/B for Scenario A, F1-vs-gold + LLM-rationale for Scenario B. |
| `/api/scenario-a/run` *(v5)* | Run Scenario A (one-shot RAG comparison): {question, ticker, mode (A1/A2/A3)} → {dense_answer, graphrag_answer, retrieved_context_left, retrieved_context_right} |
| `/api/scenario-b/run` *(v5)* | Run Scenario B (multi-hop): {scenario_id, ticker, panes (left/center/right)} → {pane_outputs: [{name, answer, decomposition_trace, retrieval_history}]} |
| `/api/scenario-a/analyze` *(v5)* | Blind LLM-judge on Scenario A two answers |
| `/api/scenario-b/analyze` *(v5)* | F1-vs-gold + LLM-rationale on Scenario B three pane outputs |

### v5 Architecture Note: Faithful Mirror of RAGSearch / GraphSearch

The v5 Scenario B PRIMARY pane (center) mirrors `arXiv:2509.22009 / GraphSearch` exactly — code reference: `RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning`. The paper does NOT use tool-using agents; instead it runs a **deterministic multi-stage prompt pipeline** with dual channels (text + KG) that perform iterative decomposition + retrieval + verification + expansion. v5 implements this faithfully.

The "tool-agent" pane (right) is **advanced/optional** — included as a toggle for AI-engineer audiences who expect a ReAct-style agent. It's NOT the paper's architecture; it's surfaced for completeness. Default UX shows the paper's mirror.

The Scenario A judge stays "single simple LLM call, blind A/B" — same idea as v4's qualitative judge but operating on RAG vs GraphRAG (not pipeline-A vs pipeline-B). Scenario B judge ADDS F1-vs-gold scoring on top of the qualitative judge — graduating from purely-qualitative to scientifically-grounded where gold exists.

### Data Layer (v5) — Per-Doc JSON + Numpy + BM25, No DB

**Decision (CEO 2026-04-28):** demo scale (per-doc, 11 tickers) doesn't need a database yet. Pure JSON + numpy + `rank_bm25` keeps zero infra, fast iteration, and fits memory.

#### Per-doc footprint

| Item | Size |
|---|---|
| Plaintext (already vendored in `tests/fixtures/extracted-10k-text/`) | ~5 MB |
| Chunks (256-token w/ 32-token overlap, ~1000-3000 chunks) | ~2 MB JSON |
| Chunk embeddings (`all-MiniLM-L6-v2`, 384-dim, float32) | ~4.5 MB numpy |
| BM25 inverted index | ~1-2 MB pickled |
| Graph-side derived structures (nodes, edges, span offsets) | ~1-2 MB |
| **Total per ticker** | **~10-12 MB** |
| × 11 tickers | **~130 MB total** in process memory at server start |

#### Stack

| Component | Implementation | Justification |
|---|---|---|
| Chunking | Custom `RecursiveCharacterTextSplitter`-style (256-token w/ 32-token overlap; preserve span offsets back to source for citations) | Standard pattern; need offsets for evidence-rendering in the GraphRAG pane |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2` (384-dim, ~80MB model, fast) | Already used in kgspin-tuner fixtures; consistent across the codebase. Cache at fixture-build-time, NOT at request-time. |
| Cosine retrieval | `numpy.dot(embeddings, query_embed.T)` + `argpartition` for top-k | <50ms for 3000 chunks; matrix-multiply scales linearly. No external dependency. |
| BM25 | `rank_bm25` package (`BM25Okapi`, in-memory, pure Python) | ~30 LOC; fast for 3000 chunks; well-tested |
| Hybrid (BM25 + cosine) for A2/A3 | Reciprocal Rank Fusion (RRF) — reuse the `RRF_K` module constant from kgspin-core | Same RRF heuristic the extraction pipeline uses; consistent ranking semantics across the stack |
| Graph-side BM25/cosine for A3 | Same primitives applied to (node_text, node_type, semantic_definition) tuples and (edge_predicate, edge_evidence_span) tuples. Per-doc graph already in extraction artifacts. Build once at fixture-build time. | Treats the graph as another searchable corpus — this is what makes A3 a fair "search-the-graph" comparison rather than a hand-tuned graph traversal |

#### Storage layout

```
tests/fixtures/rag-corpus/
├── {ticker}/
│   ├── chunks.json                  # [{id, text, char_offset_start, char_offset_end, source_section}]
│   ├── chunk_embeddings.npy         # 2D float32: (n_chunks, 384)
│   ├── bm25_index.pkl               # pickled BM25Okapi instance
│   ├── graph_nodes.json             # [{id, text, type, parent_doc_offsets, embedding_index}]
│   ├── graph_edges.json             # [{id, src, tgt, predicate, evidence_char_span, embedding_index}]
│   ├── graph_node_embeddings.npy
│   ├── graph_edge_embeddings.npy
│   └── manifest.json                # build provenance: source SHA, embedding model id, chunk-config version
```

#### Build pipeline (one-shot, run before demo server starts)

`scripts/build_rag_corpus.py --ticker AAPL`:
1. Load source text from `tests/fixtures/extracted-10k-text/AAPL.txt`
2. Load extraction artifacts (graph nodes/edges) from existing extraction output
3. Chunk text → emit `chunks.json` with span offsets
4. Embed chunks → emit `chunk_embeddings.npy`
5. Build BM25 index → pickle to `bm25_index.pkl`
6. Embed graph node texts + edge predicate-evidence spans → emit `graph_*_embeddings.npy`
7. Write `manifest.json` (source SHA, embedding model id, chunk config version)

Idempotent: re-running with same source SHA + embedding model id is a no-op. Extension: `--ticker all` builds for all 11.

#### Demo server startup

At server start, lazy-load per-ticker corpus into a dict keyed by ticker:
```python
RAG_CORPUS = {}  # {ticker: {chunks, chunk_emb, bm25, graph_nodes, graph_edges, ...}}

def load_rag_corpus(ticker):
    if ticker not in RAG_CORPUS:
        # mmap'd numpy + pickle.load for BM25
        ...
    return RAG_CORPUS[ticker]
```

#### Retrieval API

`src/kgspin_demo_app/services/dense_rag.py`:
- `search(ticker, query, top_k=5) → list[{chunk_id, text, score, source_offset}]`

`src/kgspin_demo_app/services/graph_rag.py`:
- `aquery_context(ticker, question, mode='A1'|'A2'|'A3', top_k=5) → ContextBundle`
  - A1: returns chunks (same as dense_rag)
  - A2: returns chunks + their correlated 1-hop graph neighborhoods (chunks → matching entities → connected entities + edges)
  - A3: returns matching graph nodes + edges + the source spans referenced

Mirrors the `GraphSearch.aquery_context` signature in the paper for swap-compatibility with `graphsearch_pipeline.py`.

#### When this layer breaks (and migration triggers)

- **50+ tickers OR 30+ MB embeddings per ticker:** switch to **SQLite + `sqlite-vec`** (single file, ~1M vec cap, FTS5 for BM25, single dependency)
- **Multi-process server (gunicorn workers):** embeddings duplicated per worker; mmap'd numpy survives, but consider lightweight DB
- **Cross-ticker queries (Phase 5C — multi-doc graph):** need a global index, not per-ticker; SQLite path or DuckDB+VSS at this point
- **Production deployment with PII / auth scope:** vector DB with proper access control (Qdrant, ChromaDB, etc.)

For Phase 5A scope (per-ticker, single demo server), **JSON + numpy + rank_bm25 is the right answer.** No DB.

### v4 → v5 Migration Notes

- **DELETE from `/compare` UI:** v4 multi-hop "all-displayed-plays" pane (cross-KG-pipeline comparison)
- **DELETE from `/compare` UI:** v4 agentic Q&A comparison
- **MIGRATE to per-graph view (PRD-002):** the cross-pipeline (fan_out vs agentic_flash vs agentic_analyst) comparison; lives there as part of "Why this graph" content
- **MIGRATE to per-graph "Why this matters" tab:** a single sample multi-hop demo question THAT GRAPH supports; clickable link → routes to `/compare` Scenario B with that scenario pre-selected
- **CHANGE scenario YAML:** all 4 existing scenarios in `multihop_scenarios.yaml` already genericized to "the company" (landed 2026-04-27). v5 promotes that to formal `{company}` template variables + adds 6 more (5 fin + 5 clinical templates total = 10).

### 5. RICE Analysis (v5)

| Factor | Value | Rationale |
|---|---|---|
| Reach | 10 | v4 was 9; v5 raises Reach because the RAG-vs-GraphRAG axis is the exact comparison every AI-engineer evaluator already runs in their own POC. v5 lets them see it ON THEIR DOMAIN with their own document, side-by-side. The pitch lands in their existing mental model. |
| Impact | 5 | Unchanged — transformative. v5 adds scientific F1 vs deterministic gold for Scenario B (graduating from pure-qualitative). |
| Confidence | 0.85 | v4 was 0.8; v5 raises 0.05 because the paper's reference implementation (`RAGSearch/GraphSearch/pipeline.py`) is now read and proven to be a deterministic pipeline (low novelty risk). Small discount for tool-agent pane (Phase 5B; not paper-validated). |
| Effort | 5.0 (XL) | v4 was 3.0 (L). v5 adds: dense_rag service, graph_rag service with 3 patterns, agentic dense-RAG, paper-mirror GraphSearch (dual channel + decomp + verification + expansion), scenario template engine, hand-authored gold fixtures, 4 new endpoints. Phased delivery (5A → 5B → 5C) keeps individual sprint sizes M-L. |
| **Score** | **(10 × 5 × 0.85) / 5.0 = 8.5** | v4 was 12.0. The headline drop reflects v5's wider effort. v5 is a more rigorous, more scientific, more AI-engineer-fluent demo than v4 — earning the effort cost. v5 is still the critical First-Call asset; the pitch is sharper. |

---

## Changelog

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Relocated to kgspin-demo and updated RICE score. | Prod |
| 2026-04-19 | Sprint 11: RICE Confidence 1.0 → 0.9 per VP Prod 2026-04-17 consultation — real news backends add integration surface worth factoring in. Status unchanged (Approved). | Dev |
| 2026-04-22 | v4: added Must-Have #9–12 (multi-hop scenario pack, all-displayed-plays parallel execution, LLM-as-judge with blinded ranking, viewer-first comparison flow). Approach mirrors RAGSearch benchmark (arXiv:2604.09666, Shanghai NYU, April 2026) — qualitative rather than benchmarked per CEO direction. RICE drops 18 → 12 on effort increase. PRD-055 (Topological Health) added as soft dependency — its score renders next to each answer to reinforce why the judge ranks them as it does. | Prod |
| 2026-04-28 | v5: REFRAMED axis from cross-KG to RAG-vs-GraphRAG. Removed v4 #9–12 from `/compare`. Added Scenario A (one-shot RAG vs GraphRAG with 3 toggleable GraphRAG patterns A1/A2/A3) + Scenario B (multi-hop with 3 panes: agentic dense, paper-mirror GraphSearch, optional tool-agent). Mirrors `RAGSearch/GraphSearch/pipeline.py` (arXiv:2509.22009) deterministic dual-channel pipeline. Added templated scenarios with `{company}` / `{ticker}` placeholders (10 total: 5 fin + 5 clin) — anti-pattern of hardcoded literals explicitly called out. Added deterministic ground-truth gold (≥10 fin answer keys in Phase 5A) for F1 scoring on Scenario B. Phased delivery 5A/5B/5C. Cross-KG-pipeline comparison migrated out of `/compare` to per-graph view (PRD-002). RICE 12.0 → 8.5 reflecting effort increase; scientific rigor gained. | CTO |
| 2026-04-28 | v5 Data Layer: per-doc JSON + numpy + `rank_bm25` (no DB). ~12 MB per ticker × 11 tickers = ~130 MB in-process. Embeddings via `sentence-transformers` `all-MiniLM-L6-v2`. RRF for hybrid BM25+cosine (reuses kgspin-core's `RRF_K`). One-shot fixture build via `scripts/build_rag_corpus.py`. Migration triggers (SQLite+sqlite-vec, etc.) documented for later scale. | CTO |
