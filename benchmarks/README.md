# KGSpin benchmark harness

Track 3 of the alpha MVP. Two-arm like-for-like comparison:

- **Arm A** — KGSpin-tuned graph (tuner's ``alpha_runner`` output).
  Wave 2 — wiring deferred until the tuner stabilizes.
- **Arm B** — LLM-extracted graph (Gemini per-chunk triple extraction).
  Implemented in [`arms/b/extract.py`](./arms/b/extract.py).

Both arms emit the same ``graph-v0`` schema
([`schemas/graph-v0.json`](./schemas/graph-v0.json)), so the three
retrieval strategies in [`retrieval/`](./retrieval/) score against
whichever arm's graph you hand the runner.

## Question sets

- **FinanceBench (primary)** — 150-question open-source set from
  [patronus-ai/financebench](https://github.com/patronus-ai/financebench).
  Filtered to the 23 multi-hop-requiring records (≥2 evidence spans)
  whose ``doc_name`` overlaps our corpus manifest. Split
  deterministically by SHA-1 bucket (20% held-out, gitignored).
  - `questions/financebench-subset.jsonl` — 20 training records.
  - `questions/financebench-subset.heldout.jsonl` — 3 held-out
    records. Gitignored. NEVER prompt-engineer against.
- **MultiHop-RAG (cross-domain control)** — full 2,556-query set from
  [yixuantt/MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG).
  Land at `questions/multihop-rag.jsonl`. Ships its own news corpus
  (see MultiHop-RAG repo). Not our Amazon/NewsAPI news corpus.

The full 10,231-question FinanceBench set requires Patronus access.
Request-for-access tracked in `docs/sprints/track-3-benchmark-and-clinical-gold/dev-report.md`.

## Corpus

`corpus/manifest.yaml` freezes 18 10-Ks from 15 companies across 7 GICS
sectors. ``sha256`` values were measured during freeze on 2026-04-20;
entries with ``sha256: null`` are sources that required browser sessions
during the unattended freeze and populate on first ``fetch.py`` run.

```
python benchmarks/corpus/fetch.py  # download PDFs into corpus/pdfs/
```

## Retrieval strategies

- **`fan_out_from_corpus`** — semantic-rank chunks → expand via graph
  1-hop.
- **`fan_out_from_graph`** — rank entities → collect chunks via
  provenance.
- **`semantic_composed`** — reciprocal-rank fusion of the two above.

Each exposes ``retrieve(graph, question, top_k=5) -> list[str]``.

## Metrics

**Primary:** [RAGAS](https://docs.ragas.io) —
`faithfulness`, `answer_relevancy`, `context_precision`,
`context_recall`. RAGAS is imported lazily; install with
`pip install ragas` before running.

**Fallback:** deterministic token-level EM + F1 + context-recall, used
when RAGAS is unavailable or the caller passes `--metrics simple`. No
API keys required. DeepEval is a documented secondary option; add only
if RAGAS under-performs on a future question set.

## Harness runner

```
python benchmarks/harness/run.py \
    --arm b \
    --retrieval fan_out_from_corpus \
    --graph benchmarks/reports/<ts>/arm-b/graph.json \
    --questions benchmarks/questions/financebench-subset.jsonl \
    --llm-alias gemini_flash \
    --output benchmarks/reports/<ts>/results.json
```

ADR-002 §7: `--llm-alias`, `--llm-provider`, `--llm-model` are all
wired as per-call kwargs. `gemini_hard_limit` is the extractor default
(for Arm B); the runner's *answer-generation* step picks up whatever
alias the caller passes.

### Thin-slice smoke (plumbing verification)

```
python benchmarks/harness/run.py \
    --arm b --retrieval fan_out_from_corpus \
    --graph benchmarks/reports/smoke/arm-b/graph.json \
    --questions benchmarks/questions/financebench-subset.jsonl \
    --mock-llm --limit 5 \
    --output benchmarks/reports/smoke/results.json
```

`--mock-llm` swaps Gemini for deterministic stubs — exercises
extraction + retrieval + scoring end-to-end without an API round-trip.

## Not in sprint 20 (this)

- Full-corpus Arm B extraction run. Arm A isn't ready; there's no
  comparator to score against.
- Token-cost budget report. Gate behind the first scale test.
- Embedding-based entity merge. Shipped with Wave 2 if Jaccard-only
  under-performs on the full benchmark.
