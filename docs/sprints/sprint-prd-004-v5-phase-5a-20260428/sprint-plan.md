# PRD-004 v5 Phase 5A — Sprint Plan

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-28
**Branch:** `sprint-prd-004-v5-phase-5a-20260428` (off `main` @ `f99bb7e`)
**Spec:** PRD-004 v5 (on branch `sprint-prd-004-v5-rag-vs-graphrag-20260428`)
**Reference impl:** `RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning` (arXiv:2509.22009)
**Revision:** v2 — incorporates VP-Eng + VP-Prod review fixes (see §11)

---

## TL;DR

This is the first of three sprints (5A → 5B → 5C). Phase 5A delivers:
- A per-doc retrieval corpus (chunks + embeddings + BM25 + graph node/edge embeddings) for two fin tickers (AAPL, JNJ) and one clinical doc (JNJ-Stelara, hedge per VP-Prod review).
- Three retrieval services on top: `dense_rag` (pure baseline), `graph_rag` (3 patterns A1/A2/A3), and `agentic_dense_rag` (Search-o1-style decompose/iterate).
- A faithful mirror of the RAGSearch/GraphSearch paper pipeline (`graphsearch_pipeline.py`) — dual-channel, deterministic, decompose → retrieve → verify → expand. Self-reflection on by default; flag-controllable for demo-day latency.
- Five fin templated scenarios + one clinical hedge template + 11 hand-authored gold answers (5 fin × 2 tickers + 1 clinical × 1 ticker) for F1 scoring on Scenario B.
- Four new endpoints (`/api/scenario-{a,b}/{run,analyze}`) — Scenario B run streams progress via SSE. /compare UI rework: remove v4 multi-hop UI buttons, add Scenario A view + Scenario B view (2 panes by default; tool-agent right pane hidden behind a "Show advanced" toggle per PRD §3 #10 framing).
- Migration: v4 frontend buttons removed, but `/api/multihop/run` route kept alive for one sprint until per-graph "all-displayed-plays" view (PRD-002 territory, currently un-shipped) actually exists. Per-graph "why" tab gets a deep-link to `/compare#scenario-b?template=...&ticker=...&autorun=1`.

The single substantive risk is **graph artifact production** — the corpus builder calls the existing `_run_kgenskills(...)` entry point with a `bundle + PipelineConfigRef + registry_client` triple constructed identically to the demo's runtime path. Then it persists the resulting `kg_dict` to disk. The corpus build is a 5–10 min/ticker operation.

The sprint is large (~2,700 net LOC, ~24h of tractable work) but **parallel-tractable**: services A–E are independent of frontend I/J, and gold authoring G runs in parallel with both. The 16h wall-clock cap is tight but holds via the parallel plan in §8.

Targeting **12 commits** on `sprint-prd-004-v5-phase-5a-20260428`.

---

## 1. Reading and source-of-truth

The PRD-004 v5 spec lives on `sprint-prd-004-v5-rag-vs-graphrag-20260428`. We **do not rebase** onto that branch — too much risk of pulling docs churn mid-sprint and tying our delivery to a docs-only PR's lifecycle. Instead:

- We read PRD-004 v5 from that branch (already done; archived to `/tmp/PRD-004-v5.md` for the duration of the sprint).
- Our branch is cut from `main`. When PRD-004 v5 lands on main, we'll resolve any trivial merge fallout in the dev-report; given v5 is docs-only, no functional conflict is expected.

The reference implementation at `RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning` is the canonical algorithm shape for `services/graphsearch_pipeline.py`; the prompts in `RAGSearch/GraphSearch/deepsearch/prompts.py` are vendored verbatim with attribution comments. RAGSearch is MIT licensed (verified at `/Users/apireno/repos/RAGSearch/LICENSE`).

---

## 2. Reconnaissance findings (drives the rest of the plan)

### 2.1 Source corpus

- **Spec assumes `tests/fixtures/extracted-10k-text/{ticker}.txt`** — these files **do not exist**. The repo has only `tests/fixtures/corpus/JNJ.html` (the JNJ raw 10-K).
- **What does exist:** raw HTML 10-Ks under `~/.kgspin/corpus/financial/sec_edgar/{TICKER}/{date}/10-K/raw.html` for **7 tickers** — AAPL, AMD, GOOGL, JNJ, MSFT, NVDA, UNH. Phase 5A only needs **2** for gold (AAPL + JNJ) but the UI exposes all 7 with a "no F1 available" badge for non-gold tickers (per VP-Prod).
- **Decision:** `build_rag_corpus.py` accepts a ticker; resolves the most recent 10-K HTML from `~/.kgspin/corpus/financial/sec_edgar/{TICKER}/`; strips HTML to plaintext using BeautifulSoup and writes plaintext to `tests/fixtures/rag-corpus/{ticker}/source.txt` as a build artifact (not source-vendored — added to `.gitignore` excluded only the embeddings + source.txt; the manifest IS committed for reproducibility tracking).

### 2.2 Graph artifacts — the single substantive risk

**Confirmed entry point (verified in `demos/extraction/extraction/kgen.py:12`):**
```python
_run_kgenskills(
    text=..., company_name=..., ticker=...,
    bundle=...,                        # construct via resolve_bundle_path(bundle_name)
    pipeline_config_ref=...,           # construct via _pipeline_ref_from_strategy('fan_out')
    registry_client=...,               # via _get_registry_client()  [demo_compare.py:130]
    on_chunk_complete=None, ...        # all callbacks optional
    document_metadata={...},
) -> dict
```

This is reachable from a `scripts/` entrypoint without running the FastAPI server: import the three helpers directly from `demos.extraction.demo_compare` (yes, they're underscore-private but they're stable enough to import — Wave G already did this for similar reuse). To formalize, **commit A also extracts a public helper** `demos/extraction/extraction/public_api.py:run_fan_out_extraction(text, company_name, ticker, raw_html=None, document_metadata=None) -> dict` (~40 LOC) that wraps the four inputs, so the corpus builder doesn't depend on `demo_compare.py` private internals. The demo's existing call site is unaffected (it continues to call `_run_kgenskills` directly).

**Manifest fingerprint** includes `kgspin_core_sha` (read at build time from `kgspin_core.__version__` or `git -C $(python -c 'import kgspin_core; print(kgspin_core.__path__[0])') rev-parse HEAD`) so an idempotency check correctly invalidates when the extractor changes mid-sprint.

### 2.3 Existing v4 multi-hop infrastructure (to migrate)

- `/api/multihop/scenarios` (demo_compare.py:2469)
- `/api/multihop/run` (demo_compare.py:2510) — parallel fan-out across 3 KG pipelines + judge
- `/api/compare-qa/{doc_id}` (demo_compare.py:2258) — slot-level Q&A
- Frontend: `.multihop-bar`, `.multihop-answers`, `data-action="run-multihop"`, judge verdict panel (`compare-runner.js:2725+`)

**Migration decision (per VP-Prod):** the per-graph "all-displayed-plays" view (PRD-002 territory) **does NOT yet exist** — confirmed via grep. Deleting v4 endpoints in 5A risks leaving the pitch surface empty. Therefore in 5A we:
- **Delete from frontend:** the v4 `.multihop-bar` controls, `.multihop-answers` panel, judge verdict panel — they'd visually conflict with Scenario A/B.
- **Keep alive on backend:** the `/api/multihop/run`, `/api/multihop/scenarios`, `/api/compare-qa/{doc_id}` route handlers. No UI calls them in 5A, but they remain functional (and tested) so a hotfix branch can reintroduce a button if a clinical pitch needs the old UX before 5B lands.
- **Schedule full deletion** (route handlers + helpers + tests + scenarios YAML) for the 5B follow-up sprint, gated on the per-graph view actually shipping in PRD-002 territory.

### 2.4 Existing reusable infrastructure

- `services/topology_health.py` — kept; reused on each Scenario B answer pane footer.
- `services/micrograph.py` — kept; not used by Phase 5A directly.
- `demos/extraction/judge.py:rank_answers(question, answers, backend=None) → JudgeVerdict` — extended with `rank_two(question, answer_a, answer_b)` for Scenario A. The 3-way version remains.
- `demos/extraction/scenarios.py:load_scenarios()` — extended with `load_v5_templates()`. The v4 `Scenario` API stays loaded but unused; full deletion alongside route handlers in 5B.

### 2.5 Cross-repo imports

- `from kgspin_core.constants import RRF_K` → `60.0` (kgspin-core/src/kgspin_core/constants.py:12). Used by `dense_rag.py` for hybrid BM25+cosine fusion.
- `from kgspin_core.graph_topology.health import compute_health` — already wrapped via `services/topology_health.py`.

### 2.6 LLM aliases

- The actual registered alias is **`gemini_flash`** (not `gemini-flash-2.5` as the spec language implies). Used by `judge.py` and `multihop` already.
- Phase 5A's three new LLM call paths (agentic_dense decomposition, paper-mirror prompts, scenario judge) all use `gemini_flash`. No new alias registrations.

### 2.7 New dependencies

- `sentence-transformers` — **NOT in `pyproject.toml`**. Adding to `[project] dependencies`. ~80MB model `all-MiniLM-L6-v2` cached under `~/.cache/huggingface/`.
- `rank_bm25` — **NOT in `pyproject.toml`**. Adding.
- `beautifulsoup4` — confirmed as transitive via fetcher code (already in `uv.lock`); no explicit add needed but verifying once before commit A is mandatory.

`uv.lock` will bump in commit A; flagged as expected churn.

**CI implication (per VP-Eng):** unit tests use a `FakeEmbedder` shim (~25 LOC in `tests/conftest.py`) that returns deterministic 384-dim vectors from a hash of the input string. Only the integration smoke (`KGSPIN_LIVE_LLM=1`) and the actual corpus build use the real `sentence-transformers` model. This avoids forcing a 80MB download in the default `pytest .` run.

---

## 3. Deliverables

### A. RAG corpus builder (`scripts/build_rag_corpus.py`)

**Files:**
- `scripts/build_rag_corpus.py` (new, ~250 LOC)
- `demos/extraction/extraction/public_api.py` (new, ~40 LOC) — wraps `_run_kgenskills` for non-FastAPI callers
- `pyproject.toml` (add `sentence-transformers`, `rank_bm25`)
- `uv.lock` (regen)
- `tests/conftest.py` (add `FakeEmbedder` fixture)
- `.gitignore` (add `tests/fixtures/rag-corpus/*/source.txt`, `*.npy`, `*.pkl`)

**CLI:**
```
python -m scripts.build_rag_corpus --ticker AAPL [--pipeline fan_out] [--force]
python -m scripts.build_rag_corpus --ticker phase5a   # builds AAPL + JNJ + JNJ-Stelara
```

**Output layout** (per spec §4 Data Layer):
```
tests/fixtures/rag-corpus/
├── {ticker}/
│   ├── source.txt                 # build artifact (HTML→plaintext); .gitignore'd
│   ├── graph.json                 # build artifact (fan_out kg_dict); .gitignore'd
│   ├── chunks.json                # [{id, text, char_offset_start, char_offset_end, source_section}]
│   ├── chunk_embeddings.npy       # 2D float32: (n_chunks, 384); .gitignore'd
│   ├── bm25_index.pkl             # pickled BM25Okapi; .gitignore'd
│   ├── graph_nodes.json           # [{id, text, type, semantic_definition?, parent_doc_offsets, embedding_index}]
│   ├── graph_edges.json           # [{id, src, tgt, predicate, evidence_char_span, embedding_index}]
│   ├── graph_node_embeddings.npy  # .gitignore'd
│   ├── graph_edge_embeddings.npy  # .gitignore'd
│   └── manifest.json              # {source_sha, embedding_model, chunk_config, pipeline, kgspin_core_sha, built_at}  ← committed
```

Only `chunks.json`, `graph_nodes.json`, `graph_edges.json`, and `manifest.json` are tracked in git (small, deterministic, useful for diffing schema changes). Embeddings + BM25 + source.txt + graph.json are build artifacts, gitignored, regenerated by `build_rag_corpus.py`.

**Build steps:**
1. Resolve ticker → newest dated 10-K HTML under `~/.kgspin/corpus/financial/sec_edgar/{TICKER}/`. (For `JNJ-Stelara` clinical hedge, pull from `~/.kgspin/corpus/clinical/clinicaltrials_gov/`.) Hash source bytes (sha256). Write `source.txt` (BeautifulSoup `get_text("\n", strip=True)`).
2. Chunk: 256-token sliding window with 32-token overlap, preserving `(char_offset_start, char_offset_end)`. Tokens via tiktoken `cl100k_base`. Write `chunks.json`.
3. Embed chunks: `sentence-transformers` `all-MiniLM-L6-v2`, batch 64, output 384-dim float32. Write `chunk_embeddings.npy`.
4. BM25 index: `rank_bm25.BM25Okapi`, simple lowercase whitespace tokenization. Pickle.
5. Run extraction: `run_fan_out_extraction(text=source_text, company_name=metadata["canonical_name"], ticker=ticker, raw_html=html, document_metadata={...})` → `graph.json`.
6. Walk `graph.json`:
   - For each entity: text = `entity.text`, type = `entity.type`, semantic_definition = `entity.get('semantic_definition', '')`. Concatenate `f"{text} [{type}] {semantic_definition}"` and embed. Track `parent_doc_offsets` from `entity.char_span`.
   - For each relationship: text = `f"{predicate} {evidence_text}"` where `evidence_text` is the substring of `source.txt` at the relationship's `evidence_char_span`. Embed.
7. Write `manifest.json`. Idempotent: if manifest matches `(source_sha, embedding_model, chunk_config, pipeline, kgspin_core_sha)`, skip and log "no-op".

**Tests:** `tests/unit/test_build_rag_corpus.py`
- Smoke (uses `FakeEmbedder`): build for a tiny synthetic input → all artifacts exist with expected shapes.
- Idempotency: second invocation with same fingerprint returns no-op without re-embedding.
- `--force`: re-runs regardless.
- Manifest fingerprint: changing any of source/embedding/chunk/pipeline/core_sha invalidates idempotency.

### B. Dense RAG service (`services/dense_rag.py`)

**File:** `src/kgspin_demo_app/services/dense_rag.py` (new, ~120 LOC)

**Public API:**
```python
@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    score: float
    source_offset: tuple[int, int]
    source_section: str | None

def search(ticker: str, query: str, top_k: int = 5) -> list[Chunk]:
    """Hybrid BM25 + cosine retrieval over the per-doc chunks corpus."""

def serialize_chunks(chunks: list[Chunk]) -> str:
    """Format chunks for inclusion in an LLM prompt. See §3.E for serialization contract."""
```

**Implementation:**
- Lazy-loads `chunks.json + chunk_embeddings.npy + bm25_index.pkl` on first call (module-level dict cache keyed by ticker; mmap'd numpy).
- BM25 top-50 + cosine top-50 → RRF fusion using `kgspin_core.constants.RRF_K = 60.0`. Return top-k by fused score.
- Embeds query at request time (single `model.encode([query])` call).

**Tests:** `tests/unit/test_dense_rag.py` (uses `FakeEmbedder`)
- Round-trip: build corpus on synthetic input; `search(ticker, "...", top_k=3)` returns 3 chunks ordered by descending score.
- Empty corpus path: missing fixtures → `CorpusNotBuilt(ticker)`.
- Single chunk: corpus with 1 chunk → top_k=5 returns 1 chunk.
- RRF fairness: BM25 winner and cosine winner both appear in top-3 when they disagree.

### C. GraphRAG service with 3 patterns (`services/graph_rag.py`)

**File:** `src/kgspin_demo_app/services/graph_rag.py` (new, ~270 LOC)

**Public API:**
```python
@dataclass(frozen=True)
class ContextBundle:
    mode: str  # 'A1' | 'A2' | 'A3'
    text_chunks: list[Chunk]
    graph_nodes: list[dict]
    graph_edges: list[dict]
    evidence_spans: list[tuple[int, int]]  # back to source.txt

async def aquery_context(
    ticker: str,
    question: str,
    mode: str = 'A2',
    top_k: int = 5,
) -> ContextBundle: ...

def context_filter(context: ContextBundle, filter_type: str, query: str | None = None) -> ContextBundle:
    """filter_type in ('semantic', 'relational')."""

def serialize_bundle_for_prompt(bundle: ContextBundle) -> str:
    """The bundle→string contract used by graphsearch_pipeline. See §3.E."""
```

**Mode behaviors:**
- **A1:** delegate to `dense_rag.search()`. Returns chunks in `text_chunks`; empty graph fields. Sanity baseline.
- **A2:** `dense_rag.search()` for chunks; for each chunk, find graph entities whose `parent_doc_offsets` overlap the chunk's `(char_offset_start, char_offset_end)`; expand to 1-hop graph neighborhood (matching nodes + edges + connected nodes).
- **A3:** BM25 + cosine over `graph_nodes.json` and `graph_edges.json`. RRF fuse. Resolve matched nodes/edges back to source spans via `parent_doc_offsets` and `evidence_char_span` and return spans as text chunks.
- **`context_filter`** — semantic = re-embed and cosine-rank items by similarity to `query`; relational = restrict to items connected via 1-hop edges.

**Tests:** `tests/unit/test_graph_rag.py`
- Per-mode round-trip: build small synthetic corpus + graph; `aquery_context(...)` returns expected shape per mode.
- Mode parity: A1 result chunks ⊆ A2 result chunks.
- Span resolution: A3 returns evidence_spans whose substrings appear in `source.txt`.
- Filter behaviors: semantic ranks by similarity; relational restricts to 1-hop neighbors.
- Serialization round-trip: `serialize_bundle_for_prompt(bundle)` is deterministic and machine-parseable.

### D. Agentic dense RAG (`services/agentic_dense_rag.py`)

**File:** `src/kgspin_demo_app/services/agentic_dense_rag.py` (new, ~180 LOC)

**Pattern** (Search-o1-style):
1. LLM decomposes the question into N sub-queries (cap N at 5).
2. For each sub-query: `dense_rag.search(ticker, sub_query, top_k=5)`; LLM answers given retrieved chunks.
3. Maintain `(sub_query, retrieved_chunks, sub_answer)` history.
4. Final answer using accumulated history.

**Public API:**
```python
@dataclass(frozen=True)
class AgenticResult:
    final_answer: str
    decomposition_trace: list[str]
    retrieval_history: list[dict]

async def answer(ticker: str, question: str, max_steps: int = 5) -> AgenticResult: ...
```

**LLM:** `gemini_flash`; `temperature=0.2`; per-call timeout 60s; total iteration cap 5.

**Tests:** `tests/unit/test_agentic_dense_rag.py`
- Mock the LLM backend; assert decomposition is called once, sub-queries dispatched in sequence, history accumulates, final answer call sees full history.
- Bounded iteration: when the LLM emits 7 sub-queries, only 5 execute.

### E. Paper-mirror GraphSearch pipeline (`services/graphsearch_pipeline.py`)

**Files:**
- `src/kgspin_demo_app/services/graphsearch_pipeline.py` (new, ~500 LOC) — re-baselined per VP-Eng review (paper has 213 LOC of pipeline + ~80 LOC of helpers + dataclasses + LLM-call wrappers)
- `src/kgspin_demo_app/services/_graphsearch_prompts.py` (new, ~400 LOC) — prompts vendored verbatim from `RAGSearch/GraphSearch/deepsearch/prompts.py` (385 LOC + attribution header)
- `src/kgspin_demo_app/services/_graphsearch_components.py` (new, ~150 LOC) — `text_summary`, `kg_summary`, `query_completer`, `kg_query_completer`, `evidence_verification`, `query_expansion`, `answer_generation`, `answer_generation_deep` — thin LLM-call wrappers mirroring `RAGSearch/GraphSearch/deepsearch/components.py` (130 LOC) + helper functions (`format_history_context`, `extract_words_str`, `normalize`, `parse_expanded_queries` from `RAGSearch/GraphSearch/utils.py`).

**Total revised LOC for E: ~1,050 LOC (was 650).** This is honest; the paper's surface is larger than initial estimate.

**Bundle → string serialization contract** (per VP-Eng blocker):
```
serialize_bundle_for_prompt(bundle) returns:

[TEXT CHUNKS]
chunk_id={id1} (offset {start}-{end}, section={section}):
{chunk text}
---
chunk_id={id2} (...):
{chunk text}

[GRAPH NODES]
- {node_id}: {text} [{type}] — {semantic_definition?}

[GRAPH EDGES]
- ({src_id}) --{predicate}--> ({tgt_id})
  evidence: "{evidence_text}" [span {evidence_start}-{evidence_end}]

[EVIDENCE SPANS]
(span {start}-{end}): "{span_text}"
```

This is the input format every paper prompt receives. Deterministic, machine-parseable, and preserves the dual-channel (text vs KG) distinction the paper exploits.

**Pipeline** (faithful mirror of `RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning`):
1. Seed retrieval: `graph_rag.aquery_context(ticker, question, mode='A2')` → `ContextBundle` → `serialize_bundle_for_prompt(...)` → string.
2. Initial dual summary: `text_summary(question, str_ctx)` + `kg_summary(question, str_ctx)`.
3. Dual decomposition: `question_decomposition_deep(question)` (text sub-queries with `#` placeholders) + `question_decomposition_deep_kg(question)` (KG sub-queries).
4. **Text channel iterative loop:**
   - `query_completer` if `#` placeholder present.
   - `aquery_context(sub_query, mode='A2')` → `context_filter('semantic', query=sub_query)` → serialize.
   - `text_summary(sub_query, str_ctx)` → `answer_generation(sub_query, history+summary)`.
   - Append `(sub_query, summary, answer)` to history.
5. Text draft: `answer_generation_deep(question, full_history_str)`.
6. Evidence verification: `evidence_verification(question, history, draft)`.
7. **Self-reflection (gated):** if `enable_self_reflection=True` and verification == "yes" (paper's "needs expansion" signal): `query_expansion` → loop expanded queries through retrieval + summary; append.
8. **KG channel iterative loop** — mirror of text channel with `kg_query_completer`, `context_filter('relational', query=...)`, `kg_summary(...)`.
9. KG draft + verification + optional expansion (mirror of 5–7).
10. Merge: `[grag_context_text_summary, grag_context_kg_summary, text_history_str, kg_history_str]` joined → `answer_generation_deep(question, combined)` → final answer.

**Public API:**
```python
@dataclass(frozen=True)
class GraphSearchResult:
    final_answer: str
    text_channel_history: list[dict]
    kg_channel_history: list[dict]
    text_evidence_verification: str
    kg_evidence_verification: str
    expansion_used: bool
    retrieval_count: int
    stage_timings_ms: dict[str, int]

async def run(
    ticker: str,
    question: str,
    enable_self_reflection: bool = True,
    progress_cb: Callable[[str, dict], None] | None = None,
) -> GraphSearchResult: ...
```

**LLM:** `gemini_flash`; `temperature=0`; per-call timeout 60s; total per-pipeline timeout 240s.

**Self-reflection default decision:** `True` (mirrors paper). For demo-day operator UX the SSE progress stream (deliverable H) makes the 30-60s wait tolerable; if not, the operator can pass `enable_self_reflection=False` via a request flag for faster-but-less-faithful runs.

**`progress_cb`** is invoked at every stage boundary (`seed_retrieval_done`, `decomposition_done`, `text_subquery_{i}_start/done`, `text_draft_done`, `text_verification_done`, `text_expansion_done`, `kg_*_done`, `merge_done`) and powers the SSE stream in deliverable H.

**Prompts:** vendored verbatim from `RAGSearch/GraphSearch/deepsearch/prompts.py` into `_graphsearch_prompts.py` with attribution header citing the paper, repo URL, and MIT license. Not modified in 5A.

**Tests:** `tests/unit/test_graphsearch_pipeline.py`
- End-to-end mocked: stub LLM backend with deterministic responses; assert pipeline emits expected sequence of calls.
- Verification = "no" → no expansion calls.
- Verification = "yes" → expansion runs N queries.
- Self-reflection off: skips evidence_verification + query_expansion stages.
- KG channel parity: text-channel history and KG-channel history both populated.
- Bundle-to-string round-trip: every prompt receives a string with `[TEXT CHUNKS]`, `[GRAPH NODES]`, `[GRAPH EDGES]` markers.

### F. Scenario template engine + 5 fin templates + 1 clinical hedge template

**Files:**
- `src/kgspin_demo_app/services/scenario_resolver.py` (new, ~120 LOC)
- `demos/extraction/multihop_scenarios_v5.yaml` (new, 5 fin templates per spec §11 + 1 clinical "Stelara adverse events" hedge)

**Public API:**
```python
@dataclass(frozen=True)
class ScenarioTemplate:
    scenario_id: str
    domain: str
    question_template: str
    expected_hops: int
    placeholders: list[str]
    talking_track: str
    expected_difficulty: str  # 'easy'|'medium'|'hard' — for F1 narrative framing (per VP-Prod)
    key_fields: list[str]     # F1 set-of-tuples key (per VP-Eng)

@dataclass(frozen=True)
class ResolvedScenario:
    scenario_id: str
    question: str
    bindings: dict[str, str]
    template: ScenarioTemplate

def load_v5_templates() -> list[ScenarioTemplate]: ...
def get_template(scenario_id: str) -> ScenarioTemplate: ...
def resolve(template: ScenarioTemplate, ticker: str) -> ResolvedScenario: ...
```

**Resolution:** ticker → `corpus_document` admin record (`~/.kgspin/admin-store/corpus_document/`) → metadata fields. Missing required placeholder → `ScenarioResolutionError(scenario_id, missing=['key'])`.

**5 fin templates** + **1 clinical hedge** (per VP-Prod review):
1. `subsidiaries_litigation_jurisdiction` (fin, expected_difficulty=hard, key_fields=[subsidiary, jurisdiction])
2. `neo_compensation_stock_awards` (fin, medium, [executive, total_compensation])
3. `segments_revenue_litigation_accrual` (fin, medium, [segment, revenue])
4. `supplier_concentration_ma_termination` (fin, hard, [counterparty, contract_term])
5. `warrants_options_proxy_executives` (fin, hard, [executive, security_count])
6. `stelara_adverse_events_cohort_v5` (clinical hedge, medium, [adverse_event, age_bracket, comparator_trial])

**Tests:** `tests/unit/test_scenario_resolver.py` — load (6 templates, all required fields), resolve happy path, resolve missing placeholder, multi-occurrence substitution, key_fields validation.

### G. Hand-authored gold (11 answer keys)

**Files:** `tests/fixtures/multi-hop-gold/{scenario_id}/{ticker}.gold.json` × 11

**Schema** (revised per VP-Eng + VP-Prod):
```json
{
  "scenario_id": "subsidiaries_litigation_jurisdiction",
  "ticker": "AAPL",
  "resolved_question": "Among APPLE INC's subsidiaries listed in Exhibit 21, ...",
  "bindings": {"company": "APPLE INC", "ticker": "AAPL"},
  "expected_answer": {
    "summary": "Two subsidiaries: ...",
    "structured": [
      {"subsidiary": "...", "jurisdiction": "...", "litigation_ref": "Item 3 paragraph X"}
    ]
  },
  "key_fields": ["subsidiary", "jurisdiction"],
  "source_spans": [
    {"section": "Exhibit 21", "char_offset_start": 12345, "char_offset_end": 12500},
    {"section": "Item 3", "char_offset_start": 67890, "char_offset_end": 68100}
  ],
  "expected_difficulty": "hard",
  "narrative_recovery": "If paper-mirror underperforms here, lead with: 'this scenario requires section-crossing — even paper-mirror's dual-channel can struggle when the litigation accrual is in a footnote'.",
  "notes": "CTO reasoning: ...",
  "authored_by": "CTO 2026-04-28",
  "confidence": "high"
}
```

`key_fields` provides the per-scenario tuple key for set-of-tuples F1. `narrative_recovery` (per VP-Prod) is the demo-presenter one-liner for when paper-mirror's F1 lands ugly on this scenario.

**Process (HITL — incremental review per VP-Prod):**
1. Dev team drafts AAPL × scenario 1 gold candidate with LLM-assisted scratch script.
2. CTO reviews (10–15 min); marks `confidence` and approves/rewrites.
3. Dev moves to AAPL × scenario 2 while CTO reviews. Rolling pipeline.
4. After AAPL × 5 done, repeat for JNJ. After JNJ × 5 done, do JNJ-Stelara × 1.

**Validation:** `tests/unit/test_gold_fixtures.py` — all 11 files present, valid against schema, `source_spans` resolve to non-empty substrings of `source.txt`, `key_fields` ⊆ keys of `expected_answer.structured[0]`.

### H. New endpoints (4 routes + SSE)

**Files:**
- `demos/extraction/demo_compare.py` (extend, ~280 LOC added)
- `demos/extraction/scenario_b_eval.py` (new, ~100 LOC) — F1 scoring

**Routes:**

```python
@app.post("/api/scenario-a/run")
async def scenario_a_run(req: ScenarioARunReq) -> ScenarioARunResp: ...
# Body: {question, ticker, mode in {'A1','A2','A3'}}
# Returns: {dense_answer, graphrag_answer, retrieved_context_left, retrieved_context_right}

@app.post("/api/scenario-a/analyze")
async def scenario_a_analyze(req: ScenarioAAnalyzeReq) -> ScenarioAAnalyzeResp: ...
# Body: {question, dense_answer, graphrag_answer}
# Returns: {verdict, rationale_a, rationale_b, winner in {'A','B','tie'}}

@app.get("/api/scenario-b/templates")
async def scenario_b_templates() -> list[ScenarioTemplateDTO]: ...
# Returns the 6 templates (5 fin + 1 clinical hedge) for the picker

@app.post("/api/scenario-b/run")
async def scenario_b_run(req: ScenarioBRunReq) -> StreamingResponse: ...
# Body: {scenario_id, ticker, panes: ['agentic_dense', 'paper_mirror'], enable_self_reflection?: bool}
# Returns SSE stream with events: stage_start, stage_done, pane_complete, all_done
# Final all_done payload: {pane_outputs: dict[pane_name -> PaneOutput]}
# 'tool_agent' value accepted in panes list but returns 501-equivalent SSE error event in 5A.

@app.post("/api/scenario-b/analyze")
async def scenario_b_analyze(req: ScenarioBAnalyzeReq) -> ScenarioBAnalyzeResp: ...
# Body: {scenario_id, ticker, pane_outputs: dict[pane_name -> PaneOutput]}
# Returns: {f1_per_pane: dict[pane_name -> {f1, precision, recall, f1_confidence}], llm_rationale_per_pane: dict[pane_name -> str], illustrative_n: 1, recovery_narrative?: str}
```

**Critical DTO change (per VP-Eng):** `pane_outputs` is `dict[pane_name -> PaneOutput]`, not `list`. This is forward-compatible with 5B's tool_agent pane addition (pure additive change).

**SSE for `scenario-b/run`** (per VP-Eng + VP-Prod):
- Streaming response. Server emits `data: {"event": "stage_start", "pane": "paper_mirror", "stage": "decomposition_done"}` and similar events as `progress_cb` fires inside the pipeline.
- Frontend (deliverable I) renders a per-pane progress dot/spinner that ticks through stages.
- Final event: `data: {"event": "all_done", "pane_outputs": {...}}`.

**F1 implementation** (per VP-Eng major #2):
- Set-of-tuples F1 keyed on `template.key_fields`. Each tuple = `(value_for_field_1, value_for_field_2, ...)` extracted from `expected_answer.structured` and from each pane's structured-answer parse (LLM-extracted from the pane's `final_answer`).
- Lenient string match: case-fold + whitespace-norm + alias dict (per scenario, optional, in template metadata).
- LLM-judge tiebreak when string match is ambiguous.
- `f1_confidence` is `'lenient'` when string match resolved everything cleanly, `'judge_assisted'` when LLM tiebreak fired, `'partial'` if gold has `confidence: 'partial'`.
- UI copy (deliverable I) labels the score "Illustrative F1 (n=11, see methodology)" — per VP-Prod blocker #3.

**Tests:** `tests/integration/test_scenario_endpoints.py`
- POST `/api/scenario-a/run` for AAPL with mode=A2 → 200, both answers non-empty.
- POST `/api/scenario-a/analyze` → 200, verdict in {'A', 'B', 'tie'}.
- GET `/api/scenario-b/templates` → 200, 6 templates.
- POST `/api/scenario-b/run` with panes=['agentic_dense', 'paper_mirror'] → SSE stream with stage events, terminating in `all_done` with both panes populated.
- POST `/api/scenario-b/analyze` for AAPL × subsidiaries_litigation_jurisdiction → 200, F1 ∈ [0,1] for each pane.
- Forward-compat: `panes=['tool_agent']` in `/api/scenario-b/run` → SSE error event with 5B-not-yet-shipped explanation.
- `enable_self_reflection=False` → stage_timings_ms shows no verification/expansion stages.

### I. Frontend rework

**Files:**
- `demos/extraction/static/compare.html` (modify)
- `demos/extraction/static/js/compare-runner.js` (modify)
- `demos/extraction/static/css/compare.css` (modify)

**Remove (v4 multi-hop UI):** `.multihop-bar`, `.multihop-answers`, judge verdict panel, `data-action="run-multihop"`, `data-action="reveal-pipelines"`. Backend routes stay alive (per §2.3) — only the UI buttons disappear.

**Add Scenario A view** with explanatory caption (per VP-Prod major #2):
```html
<section class="scenario-a" data-region="scenario-a">
  <header>
    <h2>Scenario A — One-shot RAG vs GraphRAG</h2>
    <p class="rubric">Same question, two retrieval strategies. Pure dense RAG on the left (no graph). GraphRAG on the right.</p>
  </header>
  <div class="scenario-a-controls">
    <input type="text" data-action="scenario-a-question-input" placeholder="Ask a question about the document...">
    <select data-action="scenario-a-ticker-picker">
      <!-- All 7 tickers; AAPL/JNJ get a 'gold available' badge -->
    </select>
    <fieldset class="mode-toggle">
      <legend class="caption">GraphRAG mode (right pane). A2 is the recommended comparison.</legend>
      <label title="Same as left pane; sanity check that GraphRAG match dense baseline when graph isn't used."><input type="radio" name="scenario-a-mode" value="A1"> Standard (sanity)</label>
      <label title="Recommended. Dense retrieval + 1-hop graph neighborhood added to context."><input type="radio" name="scenario-a-mode" value="A2" checked> +1-hop graph</label>
      <label title="Search the graph itself (BM25+cosine over nodes/edges); evidence spans pulled into context."><input type="radio" name="scenario-a-mode" value="A3"> Graph-as-corpus</label>
    </fieldset>
    <button data-action="scenario-a-run">Run</button>
    <button data-action="scenario-a-analyze" hidden>Analyze Results</button>
  </div>
  <div class="scenario-a-panes">
    <article class="pane pane-left">
      <header>Dense RAG <span class="badge">no graph</span></header>
      <div class="answer"></div>
      <details><summary>Retrieved chunks</summary><div class="retrieved-context"></div></details>
    </article>
    <article class="pane pane-right">
      <header>GraphRAG <span class="mode-badge"></span></header>
      <div class="answer"></div>
      <details><summary>Retrieved context</summary><div class="retrieved-context"></div></details>
    </article>
  </div>
  <aside class="scenario-a-verdict" hidden>...</aside>
</section>
```

**Add Scenario B view** with two-pane default + Show advanced toggle (per VP-Prod blocker #1):
```html
<section class="scenario-b" data-region="scenario-b">
  <header>
    <h2>Scenario B — Multi-hop Decomposition</h2>
    <p class="rubric">Templated multi-hop scenarios. Compare an agentic dense baseline vs. the paper-mirror dual-channel pipeline (arXiv:2509.22009).</p>
  </header>
  <div class="scenario-b-controls">
    <select data-action="scenario-b-template-picker"><!-- 6 templates --></select>
    <select data-action="scenario-b-ticker-picker"><!-- all 7 tickers; non-gold get badge --></select>
    <button data-action="scenario-b-run">Run Multi-Hop</button>
    <button data-action="scenario-b-show-advanced">Show advanced: tool-agent comparison</button>
    <button data-action="scenario-b-analyze" hidden>Analyze Results</button>
  </div>
  <div class="resolved-question">
    <span class="question-text"></span>
    <span class="gold-badge" hidden>Gold available — F1 will be computed</span>
    <span class="no-gold-badge" hidden>No gold for this ticker — qualitative LLM-judge only</span>
  </div>
  <div class="scenario-b-panes" data-pane-mode="two">  <!-- 'two' default; 'three' when advanced shown -->
    <article class="pane pane-left">
      <header>Agentic Dense RAG <span class="badge">Search-o1-style</span></header>
      <div class="answer"></div>
      <div class="progress-strip"><!-- SSE stage indicators --></div>
      <details><summary>Decomposition trace</summary><div class="trace"></div></details>
    </article>
    <article class="pane pane-center">
      <header>Paper-mirror GraphSearch <span class="badge badge-paper">arXiv:2509.22009</span></header>
      <div class="answer"></div>
      <div class="progress-strip"><!-- SSE stage indicators --></div>
      <details><summary>Text channel</summary><div class="text-history"></div></details>
      <details><summary>KG channel</summary><div class="kg-history"></div></details>
    </article>
    <article class="pane pane-right" hidden>
      <header>Tool-agent GraphRAG <span class="badge badge-advanced">advanced</span></header>
      <div class="phase-5b-preview">
        <p>Phase 5B preview. Architectural commentary: a ReAct-style agent with <code>retrieve_text</code> and <code>retrieve_graph</code> tools — the pattern most modern AI engineers would build. <strong>The paper does NOT use this pattern</strong>; the dual-channel pipeline (center) is the paper's architecture and the demo's primary value-prop.</p>
        <p class="coming-soon">Live wiring lands in Phase 5B.</p>
      </div>
    </article>
  </div>
  <aside class="scenario-b-verdict" hidden>
    <div class="f1-block">Illustrative F1 (n=11, see methodology) per pane: ...</div>
    <div class="rationale-block">LLM rationale per pane: ...</div>
    <div class="recovery-narrative" hidden>...</div>  <!-- shown if F1 < 0.3 anywhere -->
  </aside>
</section>
```

**`data-action` registrations** (added to compare-runner.js per Wave-E delegation pattern):
- `scenario-a-run`, `scenario-a-analyze`
- `scenario-b-run`, `scenario-b-analyze`, `scenario-b-show-advanced`
- `scenario-a-question-input` (input handler)
- `scenario-a-ticker-picker`, `scenario-b-template-picker`, `scenario-b-ticker-picker` (change handlers)

All namespaced; verified zero collisions with existing `slot-*` actions.

**State machine** (compare-runner.js):
- A run: POST `/api/scenario-a/run` → render both panes → reveal Analyze button.
- A analyze: POST `/api/scenario-a/analyze` → render verdict aside.
- B run: open `EventSource('/api/scenario-b/run', ...)` → on each `stage_*` event, tick the relevant pane's progress strip; on `all_done`, render both panes → reveal Analyze button.
- B analyze: POST `/api/scenario-b/analyze` → render F1 + LLM rationale per pane; if any pane's F1 < 0.3, surface `recovery_narrative` from the gold fixture in the verdict aside.
- B show-advanced: toggle `[data-pane-mode="three"]` on `.scenario-b-panes`, un-hide right pane (Phase 5B preview content).

**Per-graph "why" tab link** (per VP-Prod blocker #2):
- File: `demos/extraction/static/js/slots.js` and the per-graph "why" modal markup.
- Replace existing single-shot example with a teaser block: "Multi-hop demo: <resolved question>" + "Run on /compare →" link to `/compare#scenario-b?template={scenario_id}&ticker={ticker}&autorun=1`.
- The `/compare` page reads URL hash on load: pre-selects template + ticker; if `&autorun=1`, automatically clicks Scenario B's Run button (with a 200ms delay so the user sees the form auto-fill).

**F1 framing copy** in the verdict block: "Illustrative F1, n=11. This is a directional check, not a verdict. The value-prop is paper fidelity and dual-channel transparency, not benchmark dominance." Surfaced as a tooltip + footnote.

**Tests:** manual smoke (load each view, run each scenario, screenshots in dev-report). The Wave G Playwright test (`tests/e2e/test_multihop_smoke.py`) is updated to assert the new Scenario A/B regions exist; the v4 multi-hop assertions are removed.

### J. v4 frontend deletion (backend kept alive)

**File:** `demos/extraction/static/compare.html` + `compare-runner.js` (modify, ~−400 LOC frontend only)

- **Delete from frontend:** `.multihop-bar`, `.multihop-answers`, judge verdict panel (in `compare-runner.js` ~line 2725+), `data-action="run-multihop"` and `data-action="reveal-pipelines"` registrations.
- **Keep on backend (per §2.3):** `/api/multihop/run`, `/api/multihop/scenarios`, `/api/compare-qa/{doc_id}` route handlers and helpers stay tested and functional. They become unused-by-UI in 5A, scheduled for full deletion in the 5B follow-up sprint once the per-graph "all-displayed-plays" view (PRD-002 territory) actually ships.
- **Test posture:** existing `tests/integration/test_multihop_endpoint.py` stays green. We add a TODO comment with the 5B deletion link. **No flakiness allowed.**

### K. Smoke + dev report

**Files:**
- `tests/integration/test_phase5a_smoke.py` (new, ~80 LOC)
- `docs/sprints/sprint-prd-004-v5-phase-5a-20260428/dev-report.md` (new)

End-to-end: build corpus for AAPL → POST scenario-a/run → POST scenario-a/analyze → SSE-consume scenario-b/run → POST scenario-b/analyze → all 200 with non-empty payloads, F1 ∈ [0,1].

---

## 4. Test strategy

| Layer | What | Location |
|---|---|---|
| Unit (FakeEmbedder) | Corpus builder smoke + idempotency | `tests/unit/test_build_rag_corpus.py` |
| Unit (FakeEmbedder) | Dense RAG round-trip + RRF + edges | `tests/unit/test_dense_rag.py` |
| Unit (FakeEmbedder) | Graph RAG per-mode + parity + filters + serialization | `tests/unit/test_graph_rag.py` |
| Unit (mocked LLM) | Agentic dense RAG | `tests/unit/test_agentic_dense_rag.py` |
| Unit (mocked LLM) | Paper-mirror call sequence + verification paths + self-reflection toggle | `tests/unit/test_graphsearch_pipeline.py` |
| Unit | Scenario template loader + resolver | `tests/unit/test_scenario_resolver.py` |
| Unit | Gold fixture schema + span resolution + key_fields validation | `tests/unit/test_gold_fixtures.py` |
| Integration | Endpoint happy paths + SSE consumption + 5B forward-compat | `tests/integration/test_scenario_endpoints.py` |
| Integration | Full Phase 5A smoke (live `gemini_flash`, gated on `KGSPIN_LIVE_LLM=1`) | `tests/integration/test_phase5a_smoke.py` |
| Manual | Each view renders, each scenario runs, screenshots | dev-report |

LLM calls in unit tests are **all mocked**. `sentence-transformers` is mocked via `FakeEmbedder` in `conftest.py`. The integration smoke uses live deps and is gated.

---

## 5. Non-goals (explicit re-statement)

- No tool-agent pane LIVE wiring (Scenario B right pane) — Phase 5B. The pane exists in HTML behind "Show advanced" with phase-5B-preview content.
- No additional clinical scenario templates beyond the one hedge — Phase 5B.
- No multi-doc / cross-ticker GraphRAG — Phase 5C.
- No Microsoft GraphRAG community-summary variant — Phase 5C.
- No DB migration — JSON + numpy + rank_bm25 only.
- No accuracy benchmarking infra beyond illustrative F1 vs hand-authored gold for the 11 scenario × ticker pairs.
- No Gemini alias swap UI.
- No judge-disagreement-vs-Topological-Health surfacing.
- No changes to extraction pipelines — fan_out is invoked by build_rag_corpus.py via the new public_api wrapper.
- **No deletion of v4 backend routes** — UI buttons removed; route handlers stay alive until 5B.

---

## 6. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `build_rag_corpus.py` extraction step takes 15+ min/ticker | Medium | Medium | Time-box at 10 min/ticker; if exceeded, log + continue with chunks-only (graph_*.json marked stale in manifest). A2/A3 modes degrade to A1 with a warning. |
| `sentence-transformers` first-time model download fails (no internet, no cache) | Low | Medium | Document `huggingface-cli download sentence-transformers/all-MiniLM-L6-v2` as bootstrap; corpus builder fails fast. Unit tests use FakeEmbedder so default `pytest .` doesn't depend on the model. |
| Paper-mirror pipeline runs 30-60s/scenario on `gemini_flash` (10+ LLM calls) | Medium | High UX | SSE progress stream in deliverable H makes the wait observable. `enable_self_reflection=False` flag halves the latency; surfaced as a debug knob. |
| Demo-day F1 numbers land ugly on a specific scenario | Medium | Medium | Per-scenario `narrative_recovery` string in gold fixtures; `recovery_narrative` surfaced in UI when F1 < 0.3. UI copy frames F1 as "illustrative, n=11, directional check" not "benchmark." |
| Gold authoring (3-4h CTO HITL) slips past sprint window | Medium | Medium | Incremental review (per VP-Prod): dev drafts AAPL×1, CTO reviews, dev drafts AAPL×2 in parallel, etc. Endpoints + UI work without full gold (analyze returns null F1 with "no gold" message); LLM-judge alone is still useful. |
| Frontend rework + Wave G `compare-runner.js` (2,999 LOC) collide on `data-action` | Low | Medium | All new actions namespaced (`scenario-a-*`, `scenario-b-*`); zero existing collisions verified via grep. |
| `pane_outputs` shape change breaks 5B if list-vs-dict wasn't fixed | (resolved) | — | Fixed in v2 plan: dict, not list, per VP-Eng major #5. |
| Phase 5B placeholder pane reads as unfinished (per VP-Prod blocker #1) | (resolved) | — | Two-pane default; right pane behind "Show advanced" toggle; copy frames it as architectural commentary. |
| Per-graph "why" tab deep-link reads disconnected (per VP-Prod blocker #2) | (resolved) | — | Teaser block on per-graph side shows resolved question; deep-link includes `&autorun=1` so /compare auto-runs after pre-fill. |
| F1 schema lacks per-scenario tuple key (per VP-Eng major #2) | (resolved) | — | `key_fields` added to ScenarioTemplate + gold schema; F1 implementation deterministic. |
| SSE missing from H budget (per VP-Eng major #3) | (resolved) | — | SSE explicitly speced in §3.H; ~80 LOC budgeted. `progress_cb` plumbed through paper-mirror pipeline. |
| Bundle→string serialization unspecified (per VP-Eng blocker #3) | (resolved) | — | Specified in §3.E with concrete format. |
| Deliverable E LOC underbudgeted (per VP-Eng blocker #1) | (resolved) | — | Re-baselined to ~1,050 LOC across 3 files (pipeline + prompts + components). Wall-clock plan §8 updated. |
| Corpus-builder→fan_out call surface unspecified (per VP-Eng blocker #2) | (resolved) | — | New `demos/extraction/extraction/public_api.py:run_fan_out_extraction(...)` helper extracted in commit A. |
| v4 deletion strands clinical pitches if 5B slips (per VP-Prod major #4) | (resolved) | — | Backend routes kept alive; only UI buttons removed in 5A. Full deletion deferred to 5B. |
| `gemini_flash` rate limits during gold authoring or smoke | Low | Low | Cap concurrency at 3; document `KGSPIN_GEMINI_RPM` env var; serial-fallback. |
| Scope creep: someone adds tool-agent live wiring to 5A | Medium | High | DTO rejects `'tool_agent'` in `panes` (returns SSE error); UI right pane is preview-only behind toggle. |

---

## 7. Open questions for CTO (deciding ahead unless you stop me)

1. **Source-text strategy.** Default decision: `build_rag_corpus.py` strips HTML on the fly into `tests/fixtures/rag-corpus/{ticker}/source.txt` as build artifact (gitignored). If you want plaintext .txt files vendored as committed test fixtures, say so before commit A.

2. **Graph artifact production via `_run_kgenskills`.** Default decision: extract `run_fan_out_extraction(text, company_name, ticker, raw_html, document_metadata)` public helper from `demos/extraction/extraction/kgen.py` in commit A, call from corpus builder. Pipeline = `fan_out` (densest KG). If you want a different pipeline (`agentic_flash` / `agentic_analyst`), say so.

3. **F1 implementation.** Default decision: structured-answer set-of-tuples F1 keyed on per-scenario `key_fields`; lenient match + LLM-judge tiebreak. UI labels result "Illustrative F1, n=11". If you want stricter ROUGE/BLEU on the summary, say so.

4. **Paper-mirror prompts: vendor verbatim or paraphrase?** Default decision: vendor verbatim from `RAGSearch/GraphSearch/deepsearch/prompts.py` with attribution + MIT license note. If you want paraphrased prompts, say so.

5. **Self-reflection default.** Default decision: `enable_self_reflection=True` (mirrors paper) with SSE progress stream making the 30-60s wait tolerable. Operator can flip to `False` via request flag. If you want default-off for demo speed, say so.

6. **Clinical hedge — Stelara adverse events.** Default decision: ship 1 clinical template + 1 gold key (JNJ) in 5A so clinical pitches in the next 2 weeks have at least one resolvable scenario. Total adds ~1.5h CTO HITL on top of the 3h fin gold authoring. If you want zero clinical in 5A (strict per-spec phasing), say so.

7. **Ticker dropdown.** Default decision: show all 7 ingested tickers in both Scenario A and B selectors; AAPL/JNJ get a "gold available" badge, others get "qualitative-only". If you want only AAPL+JNJ in the dropdown, say so.

If I don't hear back, I proceed with these defaults.

---

## 8. Wall-clock plan (16h cap, revised for ~24h tractable work)

Sequencing and parallelism (one developer + CTO HITL slots):

```
H 0  ─ kick off; commit A starts (corpus builder + public_api helper + deps + uv.lock)         ┐
H 1  ─                                                                                          │ A in flight
H 2  ─ A lands; commit B starts (dense_rag); F starts in parallel                               │
H 3  ─ B lands; commit C starts (graph_rag, depends on B); F continues                          │ F in parallel
H 4  ─ C lands; F lands; D + E start in parallel                                                │ G drafting starts (dev side)
H 5  ─                                                                                          │ G interleaved
H 6  ─ D lands; E continues (E is the ~1,050 LOC heavyweight)                                   │ G AAPL×1 → CTO review
H 7  ─                                                                                          │ G AAPL×2 dev / AAPL×1 review
H 8  ─                                                                                          │
H 9  ─ E lands; H starts (endpoints + SSE + F1)                                                 │ G rolling
H 10 ─                                                                                          │
H 11 ─ H lands; I starts (frontend rework)                                                      │ G AAPL×5 done, JNJ×1 starts
H 12 ─                                                                                          │
H 13 ─ I lands; J + K start                                                                     │ G JNJ×3 review
H 14 ─ J lands; K continues; manual smoke begins                                                │ G clinical hedge dev
H 15 ─ K lands; G finalized                                                                     │
H 16 ─ branch pushed                                                                            ┘
```

**Critical path:** A → B → C → E → H → I (~12h). D, F, G run alongside.

**Buffer:** the 16h cap has ~4h of slack assuming the parallelism holds. If E balloons past 6h (unlikely but possible given the 1,050 LOC re-baseline), trade goes: drop self-reflection (commit it as default-off, ship the verification + expansion stages later as a 5B follow-up). This preserves the dual-channel core, which is the value-prop.

---

## 9. What "done" looks like (per CTO completion clause)

**This (PLAN) sprint:**
- [x] Sprint plan + scope on disk at `docs/sprints/sprint-prd-004-v5-phase-5a-20260428/`
- [x] Branch cut from main, no commits yet
- [ ] Both VP reviews APPROVED (or BLOCKER/MAJOR addressed in v2 plan revision) ← **this revision**
- [ ] Branch pushed to origin
- [ ] CTO authorizes EXECUTE separately

**EXECUTE phase (not yet started):**
- All 12 deliverables (A–K, with commit 9 split per VP-Eng) on branch
- All unit + integration tests green
- End-to-end smoke green for AAPL + JNJ
- Manual UI smoke confirmed with screenshots in dev-report
- Branch pushed, NOT merged

---

## 10. Commit plan (12 commits — split per VP-Eng major #1)

1. `feat(corpus): build_rag_corpus.py + public_api helper + deps + FakeEmbedder fixture (deliverable A)`
2. `feat(services): dense_rag — BM25+cosine RRF retrieval (deliverable B)`
3. `feat(services): graph_rag — A1/A2/A3 + paper-compatible aquery_context + bundle serialization (deliverable C)`
4. `feat(services): agentic_dense_rag — Search-o1-style decompose+iterate (deliverable D)`
5. `feat(services): graphsearch_pipeline — paper-mirror dual-channel + vendored prompts + components (deliverable E)`
6. `feat(scenarios): scenario_resolver + 5 fin templates + 1 clinical hedge (deliverable F)`
7. `feat(fixtures): hand-authored gold — 11 answer keys with key_fields + narrative_recovery (deliverable G)`
8. `feat(api): /api/scenario-{a,b}/{run,analyze} + SSE + F1 with key_fields (deliverable H)`
9a. `chore(ui): remove v4 multi-hop frontend buttons (deliverable J — frontend only)`  ← split
9b. `feat(ui): Scenario A view + ticker dropdown w/ gold badges (deliverable I, part 1)`  ← split
10. `feat(ui): Scenario B view + SSE progress + Show-advanced toggle + per-graph deep-link (deliverable I, part 2)`
11. `chore(sprint): end-to-end smoke + dev report (deliverable K)`

Commit 9a deletes only the frontend multi-hop UI (markup + JS handlers); backend routes and tests stay green. Commit 9b adds Scenario A markup/handlers in a separate commit. This keeps each commit reviewable (~600 LOC max).

---

## 11. Plan v2 revisions (response to VP review)

This v2 incorporates fixes for **all** VP-Eng + VP-Prod blockers and majors:

**VP-Eng blockers (resolved):**
- LOC re-baseline for E to ~1,050 (§3.E, §8 wall-clock).
- Corpus-builder→fan_out call shape pinned via new `run_fan_out_extraction` public helper (§3.A, §2.2).
- Bundle→string serialization specified (§3.E).

**VP-Eng majors (resolved):**
- Commit 9 split into 9a (delete) + 9b (add) (§10).
- Gold schema gains `key_fields` per scenario (§3.G).
- SSE explicitly budgeted in deliverable H (§3.H, +80 LOC).
- `FakeEmbedder` fixture for unit tests (§2.7, §3.A, §4).
- `pane_outputs` is `dict[pane_name -> ...]`, not list (§3.H).

**VP-Eng minor nits:**
- `kgspin_core_sha` added to manifest fingerprint (§2.2).
- F1 tests in commit 8 (not deferred) (§3.H).
- `beautifulsoup4` verification gated to commit A (§2.7).

**VP-Prod blockers (resolved):**
- Phase 5B placeholder reframed: 2-pane default, right pane behind "Show advanced" toggle, "Phase 5B preview" framing per PRD §3 #10 language (§3.I).
- Per-graph "why" deep-link adds resolved-question teaser + `&autorun=1` (§3.I).
- F1 recovery narrative: per-scenario `narrative_recovery` string in gold; `recovery_narrative` surfaced in UI when F1 < 0.3; UI labels score "Illustrative F1, n=11, directional check" (§3.G, §3.I).

**VP-Prod majors (resolved):**
- Clinical hedge: 1 clinical template + 1 gold key (JNJ-Stelara) added (§3.F, §3.G).
- A1/A2/A3 toggle gains explanatory caption + per-mode tooltips + renamed labels ("Standard / +1-hop / Graph-as-corpus") (§3.I).
- Ticker dropdown shows all 7 with "gold available" / "qualitative-only" badges (§3.I).
- v4 backend routes kept alive in 5A; only UI buttons removed (§2.3, §3.J).
- Self-reflection default `True` paired with SSE progress stream — wait is observable (§3.E, §3.H).

— Dev team
