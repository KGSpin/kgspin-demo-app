# Arm B — LLM-extracted graph

Per-chunk Gemini triple extraction → canonicalized graph-v0 JSON. This
is the side of the A/B the harness scores against Arm A (KGSpin-tuned,
ships Wave 2).

## Prompt design

Source: `EXTRACTION_SYSTEM` + `EXTRACTION_USER_TEMPLATE` in
[`extract.py`](./extract.py). Summary:

- **System**: fixed ontology of 8 entity types (ORGANIZATION, PERSON,
  LOCATION, METRIC, SEGMENT, PRODUCT, RISK, EVENT) + snake_case
  predicates. JSON-only output with a verbatim `evidence_text` span.
- **User**: chunk body inside a code fence, doc-id prefix for
  provenance grounding.
- **Temperature**: 0.0 (deterministic) for reproducibility under
  like-for-like comparison.
- **Max tokens**: 1024.

**Held-out Arm-B blind (20%)** — `benchmarks/questions/*.heldout.jsonl`
is gitignored (`benchmarks/questions/.gitignore`). Anyone iterating on
the prompt must run against the training split only; the held-out split
is reserved for final eval.

## Cross-chunk entity resolution

Naive-but-reasonable, budget-floor posture:

1. Normalize surface form → lowercase + whitespace-collapse.
2. Bucket by `(node_type, normalized_surface)`. Exact match merges.
3. For near-matches of the same type, compute token Jaccard; merge
   above `JACCARD_MERGE_THRESHOLD = 0.85`.
4. Retain every surface-form variant in `aliases`.

**Known tradeoff**: embedding-based merge is the documented next step.
It's intentionally deferred — under the 3–5 day Arm B floor, token
Jaccard already captures the common "Apple Inc." ↔ "Apple" class of
merges without bringing in an embedding backend. When Arm A ships and
the full-corpus benchmark runs, compare Arm B precision/recall at two
resolver settings (Jaccard-only vs. embedding-merge) to decide whether
the added dep is worth it.

## Budget controls

- `--max-docs N` caps the run at the first `N` documents in the
  manifest. Use `--max-docs 2` for cost-check runs before scaling to
  the full corpus.
- Per-chunk failures log a warning and skip that chunk (one bad
  extraction doesn't kill the graph build).
- `gemini_hard_limit` alias (per ADR-002) enforces a vendor-side token
  ceiling; the extractor caps `max_tokens=1024` on top.

## Mock mode

`--mock-llm` swaps the Gemini round-trip for a canned 2-triple-per-chunk
stub. Use this for plumbing smoke tests (the harness's thin-slice
runs it automatically unless `--no-mock-llm` is passed). Mock output
is deterministic and does NOT exercise prompt quality — reserve it for
end-to-end wiring verification only.

## Scale plan

Sprint 20 (this): plumbing only. No full-corpus run.

Wave 2 order:

1. Populate `manifest.yaml`'s `sha256: null` entries via
   `benchmarks/corpus/fetch.py`.
2. `--max-docs 2` dry run; log token count + cost.
3. If cost/doc extrapolates under the Wave 2 budget cap, scale to
   full 18 docs.
4. Compare Arm A vs. Arm B across all three retrieval strategies using
   the committed FinanceBench training split. Run the held-out split
   LAST, exactly once.
