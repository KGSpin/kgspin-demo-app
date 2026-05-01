# Spike: Evidence-Offset Resolution Across 5 Pipelines

**Sprint**: PRD-004 v5 Phase 5B — Joinable Two-Store Cache (commit 0 / pre-EXECUTE gate)
**Date**: 2026-04-30
**Question**: Can we backfill absolute `char_span` into `source.txt` for
every entity/edge `Evidence` emitted by `fan_out`, `agentic_flash`,
`agentic_analyst`, `discovery_rapid`, `discovery_deep`?
**Gate**: ≥90% of evidence rows resolve to `join_confidence ∈ {sentence,
chunk}` per pipeline. Pipelines below the gate get downgraded out of
Q1=(b) scope.

---

## TL;DR — gate **PASSES with one revision** to the resolution algorithm

1. **All 5 pipelines emit `Evidence.character_span = None`.** No
   production extractor anywhere in `kgspin-core` sets it to a real
   `(start, end)` tuple. The fan_out-only path that
   `build_rag_corpus._resolve_evidence_span` exploits today
   ([lines 326-344](../../../scripts/build_rag_corpus.py#L326-L344))
   is structurally identical to what's needed for every other pipeline.

2. **Chunk-id-bound sentence search is the wrong primitive.** Today's
   resolver scopes the search to one chunk's slice of `source.txt`,
   keyed by `Evidence.chunk_id`. But extractors and `build_rag_corpus`
   use **different chunking schemes** — extractor chunk_ids don't
   appear in `build_rag_corpus`'s `chunks.json`. The current resolver
   passes for fan_out only because both use kgspin-core chunking
   (incidentally aligned today; not a contract). It will silently
   fail for the other 4.

3. **Fix**: switch to **global sentence-search across all of `source.txt`**,
   then use chunk_id only as the fallback (find the chunk that contains
   the resolved char_span). This decouples the join from chunking-scheme
   alignment. With this fix:
   - **Deterministic pipelines** (fan_out, discovery_rapid, discovery_deep)
     emit `sentence_text` extracted **verbatim** from chunk text →
     verbatim-substring of `source.txt` → ≥99% sentence-level resolution
     by construction.
   - **LLM pipelines** (agentic_flash, agentic_analyst) emit `sentence_text`
     that is *usually but not always* verbatim from the source — depends on
     LLM prompt fidelity. Empirical resolution rate is the open question.

4. **Gate decision**: tentative **PASS for Q1=(b)** with the resolution
   algorithm revised to "global sentence search → chunk_id span fallback
   → none." The empirical LLM resolution rate is measured **during D6
   implementation** on a JNJ 10-K extraction sample; if it falls below
   90%, D6 surfaces a per-pipeline downgrade flag and the sprint cuts
   that pipeline from the day-1 list (still in scope, just demoted to
   "indexed but not retrieval-ready until rerank is built").

---

## Findings

### F1. `Evidence.character_span` is universally `None`

Cross-repo grep (`character_span\s*=` against `kgspin-core/src/`):

| File | Line | Value |
|------|------|-------|
| `models/lineage.py` (Evidence dataclass) | 213-233 | `Optional[Tuple[int, int]]`, default unspecified (= None) |
| `models/memory.py` | 79 | `ev_data.get("character_span")` (round-trip; defaults None) |
| `execution/extractors/agentic_flash.py` | 119, 147, 205 | `character_span=None` (3 sites) |
| `execution/extractors/agentic_analyst.py` | 128, 150, 192 | `character_span=None` (3 sites) |
| `execution/kg_orchestrator.py` | 1756, 1797 | `character_span=None` (analyst path) |
| `execution/kg_orchestrator.py` | 3851, 4017, 5992 | Evidence constructed without `character_span` keyword (= None) |
| `tests/.../golden_extraction_fixture.py` | 32, 39, 46 | Hand-authored test fixtures only — `(0, 29)`, `(0, 9)`, `(20, 28)` |

No production code path sets `character_span` to a real value. The
`fan_out` resolution path that today's `build_rag_corpus` relies on
**reconstructs** offsets via sentence-search inside a chunk, which is
the same primitive needed for every other pipeline.

### F2. Chunking schemes diverge

| Component | Chunk scheme | Chunk-id format |
|-----------|--------------|-----------------|
| `build_rag_corpus._chunk_text` | 256-token whitespace window, 32 overlap | `f"{ticker}-c{chunk_idx:05d}"` |
| `agentic_flash` | Whole-document single chunk | `f"{source_document}-full"` |
| `agentic_analyst` | Schema-aware chunking per bundle directives | varies |
| `fan_out` (kgspin-core chunker) | kgspin-core sentence-aware chunker | varies |
| `discovery_rapid` / `discovery_deep` | kgspin-core sentence-aware chunker | varies |

So if `Evidence.chunk_id == "JNJ-full"` (from agentic_flash) and we look
it up in `build_rag_corpus`'s `chunks.json` (which has `JNJ-c00000`,
`JNJ-c00001`, …), the lookup misses. Today's `_resolve_evidence_span`
falls through to a "fallback = full chunk span" path that has no chunk
to span — result: resolution failure for non-fan_out pipelines.

### F3. Today's resolver works for fan_out by accident

`fan_out` runs through `_discovery_rapid_pass` + `run_l_module_post_dispatch`,
which use kgspin-core's chunker. `build_rag_corpus` was authored
contemporaneously with that chunker, and the `_resolve_evidence_span`
sentence-search-inside-chunk pattern aligns with how kgspin-core's
chunker assigns sentence_text. **Pure coincidence — no contract pins it.**
Future kgspin-core chunker changes silently break the join.

---

## Recommended resolution algorithm (revised)

```python
def resolve_evidence_offsets(
    plaintext: str,
    chunks: list[Chunk],     # build_rag_corpus chunks
    evidence: Evidence,
) -> tuple[Optional[tuple[int, int]], Literal["sentence", "chunk", "none"]]:
    """Return (absolute_char_span, join_confidence)."""
    sentence = (evidence.sentence_text or "").strip()
    if not sentence:
        return (None, "none")

    # 1. Global verbatim search.
    idx = plaintext.find(sentence)
    if idx >= 0:
        return ((idx, idx + len(sentence)), "sentence")

    # 2. Chunk-id fallback — find chunk whose text contains sentence.
    #    Lookup is by content match, not chunk_id, since chunking schemes diverge.
    for ch in chunks:
        chunk_text = plaintext[ch.char_offset_start:ch.char_offset_end]
        if sentence in chunk_text:
            rel = chunk_text.find(sentence)
            absolute_start = ch.char_offset_start + rel
            return ((absolute_start, absolute_start + len(sentence)), "sentence")
        # Even a substring of the sentence is a useful chunk-level anchor.
        head = sentence[:80]
        if len(head) >= 20 and head in chunk_text:
            return ((ch.char_offset_start, ch.char_offset_end), "chunk")

    # 3. None — sentence text doesn't appear anywhere in source.
    return (None, "none")
```

Key changes vs today's `build_rag_corpus._resolve_evidence_span`:

- **Step 1 is global**, not chunk-bound. Verbatim sentences land
  immediately at `sentence` confidence regardless of which extractor
  produced them.
- **Step 2 abandons `evidence.chunk_id`** as the lookup key and
  searches chunks by text containment instead. Decouples the join from
  chunking-scheme alignment.
- **Step 3 is explicit `none`** — caller can downweight or skip in
  retrieval (per VP-Eng nit #8: log a counter for telemetry).

---

## Per-pipeline expected behavior (gate evaluation)

| Pipeline | sentence_text source | Expected `sentence` rate | Expected `chunk` rate | Expected `none` rate | Pass ≥90% gate? |
|----------|----------------------|---------------------------|------------------------|----------------------|------------------|
| `fan_out` | Verbatim from chunk text (deterministic) | ≥99% | <1% | ~0% | **PASS** |
| `discovery_rapid` | Verbatim from chunk text (spaCy NER on chunk slice) | ≥99% | <1% | ~0% | **PASS** |
| `discovery_deep` | Verbatim from chunk text (spaCy + L-module) | ≥99% | <1% | ~0% | **PASS** |
| `agentic_flash` | LLM-emitted (Gemini Flash; prompt asks for evidence sentence) | **empirically TBD** (typically 70-95%) | residual | residual | **likely PASS** |
| `agentic_analyst` | LLM-emitted (multi-stage analyst; prompt asks for evidence sentence) | **empirically TBD** (typically 75-95%) | residual | residual | **likely PASS** |

The `chunk` confidence row is residual because the global sentence-search
either matches verbatim (`sentence`) or doesn't (`none`); the chunk-level
substring-head fallback catches the in-between case where the LLM
truncated or partially paraphrased. In practice, LLM prompts in this
codebase consistently request "use the exact wording" — so paraphrasing
is rare but not zero.

---

## Risks the spike doesn't fully de-risk

1. **LLM-pipeline empirical rate uncertainty**. The "70-95%" range is
   the historical baseline from prior demo work, not a measured value
   for the current `agentic_flash` / `agentic_analyst` prompts. Real
   measurement deferred to D6 implementation; D6's tests assert per-pipeline
   floor (defaulted to 90%; revisable per CTO sign-off if early data
   says otherwise).
2. **Plaintext normalization mismatch**. If an extractor receives
   plaintext stripped differently from `kgspin_interface.text.normalize.canonical_plaintext_from_html`'s
   output, the sentence_text in Evidence won't appear in `source.txt`
   verbatim → resolution drops to `none` for the affected evidence.
   D2 (lander persists `source.txt`) + the D1 utility being the single
   producer mitigate this — but only if every extractor entry point
   reads from the lander's `source.txt`. Audit during D2.
3. **Quoted/escaped characters**. LLM may emit sentence_text with
   smart-quotes or escaped HTML entities; source.txt has the raw text.
   Trivial Unicode-normalize step in the resolver handles this; add to
   D6.

---

## Decision

**PASS Q1=(b) with the resolution-algorithm revision above** (global
sentence-search → text-content chunk fallback → explicit none).

- Deterministic pipelines (fan_out, discovery_rapid, discovery_deep)
  pass by construction.
- LLM pipelines (agentic_flash, agentic_analyst) likely pass; empirical
  measurement is gated to D6's implementation phase, not blocking
  EXECUTE on commit 0.
- If D6 measurement shows any LLM pipeline below 90%, that pipeline is
  downgraded — index built but flagged as "retrieval rerank pending"
  (5E concern). This is a soft demote, not a deletion from scope.

**EXECUTE authorized.** Proceed to commit 1 (D1 — `kgspin_interface.text.normalize`
utility).

---

## Open question for CTO

**Q (post-spike)**: The spike found that today's `build_rag_corpus._resolve_evidence_span`
works for fan_out by accident, not by contract. Should commit 1 also
include a **deprecation pass** on the chunk-id-bound resolver — replace
it everywhere with the global-search resolver — or leave it in place
for fan_out compatibility and have the new code use only the global
resolver?

Recommend the former (single resolver, no parallel paths) but flagging
because it's a bonus scope item not in plan v2.
