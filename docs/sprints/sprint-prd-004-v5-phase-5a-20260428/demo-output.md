# PRD-004 v5 Phase 5A — Demo Output (mocked LLM smoke)

This file captures the headless smoke output for the Phase 5A
deliverables. **All LLM responses below are mocked** — the live
`gemini_flash` validation pass is post-merge per plan §K.

## Headless smoke commands

```bash
# Full unit + integration suite (mocked, $0 token spend):
uv run pytest tests/unit tests/integration -q

# Just the new Phase 5A tests:
uv run pytest \
  tests/unit/test_build_rag_corpus.py \
  tests/unit/test_dense_rag.py \
  tests/unit/test_graph_rag.py \
  tests/unit/test_agentic_dense_rag.py \
  tests/unit/test_graphsearch_pipeline.py \
  tests/unit/test_scenario_resolver.py \
  tests/unit/test_gold_fixtures.py \
  tests/integration/test_scenario_endpoints.py \
  tests/integration/test_phase5a_smoke.py \
  -q

# End-to-end smoke only (corpus build + 4 endpoint round-trips):
uv run pytest tests/integration/test_phase5a_smoke.py -v
```

## Sample SSE stream from `/api/scenario-b/run` (mocked)

The integration test `test_scenario_b_run_streams_sse_with_all_done`
exercises this path. Captured wire frames (mocked LLM, AAPL ticker,
single pane to keep the smoke fast):

```
event: stage
data: {"stage": "decomposition_start", "question": "Among Apple Inc.'s subsidiaries listed in Exhibit 21...", "pane": "agentic_dense"}

event: stage
data: {"stage": "decomposition_done", "sub_questions": ["Who is the CEO?"], "pane": "agentic_dense"}

event: stage
data: {"stage": "sub_query_start", "index": 0, "sub_question": "Who is the CEO?", "pane": "agentic_dense"}

event: stage
data: {"stage": "sub_query_retrieved", "index": 0, "n_chunks": 5, "pane": "agentic_dense"}

event: stage
data: {"stage": "sub_query_done", "index": 0, "sub_answer": "Tim Cook is CEO.", "pane": "agentic_dense"}

event: stage
data: {"stage": "final_answer_start", "pane": "agentic_dense"}

event: stage
data: {"stage": "final_answer_done", "final_answer": "Tim Cook is the Chief Executive Officer of Apple Inc.", "pane": "agentic_dense"}

event: stage
data: {"stage": "pane_complete", "pane": "agentic_dense"}

event: all_done
data: {"resolved_question": "Among Apple Inc.'s subsidiaries listed in Exhibit 21, which operate in jurisdictions where Apple Inc. reports active litigation in Item 3?", "scenario_id": "subsidiaries_litigation_jurisdiction", "ticker": "AAPL", "pane_outputs": {"agentic_dense": {"name": "agentic_dense", "final_answer": "Tim Cook is the Chief Executive Officer of Apple Inc.", "decomposition_trace": ["Who is the CEO?"], "retrieval_history": [...]}}}
```

## Sample `/api/scenario-b/analyze` response (with pre-parsed structured rows)

Test `test_scenario_b_analyze_with_pre_parsed_structured` shows the
F1 scoring against the AAPL subsidiaries gold:

```json
{
  "scenario_id": "subsidiaries_litigation_jurisdiction",
  "ticker": "AAPL",
  "f1_per_pane": {
    "agentic_dense": {
      "f1": 0.5,
      "precision": 0.5,
      "recall": 0.5,
      "f1_confidence": "partial",
      "n_gold": 2,
      "n_pred": 2,
      "n_overlap": 1
    },
    "paper_mirror": {
      "f1": 1.0,
      "precision": 1.0,
      "recall": 1.0,
      "f1_confidence": "partial",
      "n_gold": 2,
      "n_pred": 2,
      "n_overlap": 2
    }
  },
  "llm_rationale_per_pane": {
    "agentic_dense": "F1=0.50 (n_gold=2, n_pred=2, overlap=1); confidence=partial.",
    "paper_mirror": "F1=1.00 (n_gold=2, n_pred=2, overlap=2); confidence=partial."
  },
  "illustrative_n": 1,
  "key_fields": ["subsidiary", "jurisdiction"]
}
```

When agentic_dense scores below 0.3, the gold's `narrative_recovery`
string surfaces in `recovery_narrative` per the test
`test_scenario_b_analyze_returns_recovery_narrative_on_low_f1`.

## Manual UI smoke (operator-pending)

The `/compare` page renders both Scenario A and Scenario B sections
under the existing slot panel. Operator verification path (post-CEO
merge):

1. **`/compare` loads** → existing slot panel + Compare flow renders.
2. **Scroll to "Scenario A — One-shot RAG vs GraphRAG"**.
3. **Enter "who is the CEO of Apple"** in the text input.
4. **Pick "AAPL — Apple Inc. ⭐ gold available"** in the ticker
   dropdown.
5. **Pick "+1-hop graph"** radio (default).
6. **Click "Run"** → both panes populate; Analyze button reveals.
7. **Click "Analyze Results"** → blinded judge verdict aside opens.
8. **Scroll to "Scenario B — Multi-hop Decomposition"**.
9. **Pick "subsidiaries_litigation_jurisdiction"** in the scenario
   picker.
10. **Pick "AAPL"** in the ticker picker → resolved-question preview
    appears with ⭐ gold-available badge.
11. **Click "Run Multi-Hop"** → SSE progress pills tick across both
    panes (agentic_dense + paper_mirror); answer panes populate when
    `all_done` fires.
12. **Click "Analyze Results"** → F1 + rationale per pane render in
    the verdict aside; methodology note labels the score
    "Illustrative F1 (n=11, see methodology)".
13. **Click "Show advanced: tool-agent"** → right pane reveals with
    Phase 5B preview content.

Manual smoke artifacts (screenshots) are pending the live demo
environment — captured separately by the operator post-merge.

## URL hash deep-link (per-graph "why" → /compare integration)

Per the plan §3.I and VP-Prod blocker #2, the deep-link consumer is
ready. Format:

```
/compare#scenario-b?template=subsidiaries_litigation_jurisdiction&ticker=AAPL&autorun=1
```

On page load:
- `template` pre-selects the Scenario B picker.
- `ticker` pre-selects the ticker picker.
- `autorun=1` clicks the Run button after a 200ms delay (so the
  operator sees the form auto-fill before the run starts).

The deep-link **producer** (per-graph "why" tab) lives in PRD-002
territory and isn't refreshed yet. When that view does refresh, just
emit a link in this format from the per-graph "Run on /compare"
button.

## Live LLM validation pass (post-merge)

```bash
KGSPIN_LIVE_LLM=1 GEMINI_API_KEY=... uv run pytest \
  tests/integration/test_phase5a_smoke.py -v
```

Expected token spend: ~$0.10 per scenario × ticker × pane (paper-mirror
fires more LLM calls than agentic_dense). Total budget ~$1 per CTO
plan §K.

— Dev team, 2026-04-30
