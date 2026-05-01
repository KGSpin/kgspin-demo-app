"""Paper-mirror GraphSearch pipeline — PRD-004 v5 Phase 5A deliverable E.

Faithful translation of:

    RAGSearch/GraphSearch/pipeline.py:graph_search_reasoning
    arXiv:2509.22009 (GraphSearch: Decomposed and Verifiable Graph
    Reasoning for Question Answering on Knowledge Graphs)

Dual-channel iterative-decomposition pipeline:

  1. Seed retrieval via ``graph_rag.aquery_context(mode='A2')`` →
     dual summary (text_summary + kg_summary).
  2. Dual decomposition: text-channel sub-queries (``#`` placeholders)
     + KG-channel sub-queries (subject-predicate-object triples).
  3. **Text channel iterative loop**:
     a. ``query_completer`` if a ``#`` placeholder is present.
     b. ``aquery_context(sub_query, mode='A2')`` →
        ``context_filter('semantic', query=sub_query)`` → serialize.
     c. ``text_summary`` → ``answer_generation`` over history.
     d. Append ``(sub_query, summary, answer)`` to text history.
  4. Text draft via ``answer_generation_deep``.
  5. **Self-reflection (gated)**: ``evidence_verification`` →
     ``query_expansion`` if verification == "yes" → loop expansion
     queries through retrieval + summary; append.
  6. **KG channel iterative loop**: mirror of (3) with
     ``kg_query_completer``, ``context_filter('relational')``,
     ``kg_summary``, ``answer_generation``.
  7. KG draft + verification + optional expansion (mirror of 4-5).
  8. Merge: ``answer_generation_deep`` over the combined dual history
     → final answer.

LLM client is dependency-injected (``LLMClient`` Protocol). Tests
pass a deterministic ``MockLLMClient``; production binds to
``gemini_flash`` via deliverable H.

Self-reflection default: ``True`` (mirrors paper). Operator can flip
via the ``enable_self_reflection`` flag for faster-but-less-faithful
runs. The SSE progress stream (deliverable H) makes the 30-60s wait
observable.

Total per-pipeline timeout: 240s. Per-call timeout: 60s. These are
soft caps — the pipeline does not enforce them itself; deliverable H
wraps ``run`` in ``asyncio.wait_for``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from kgspin_demo_app.services import dense_rag, graph_rag
from kgspin_demo_app.services._graphsearch_components import (
    LLMClient,
    answer_generation,
    answer_generation_deep,
    evidence_verification,
    extract_words_str,
    format_history_context,
    kg_query_completer,
    kg_summary,
    normalize,
    parse_expanded_queries,
    parse_subquery_kg,
    parse_subquery_text,
    query_completer,
    query_expansion,
    question_decomposition_deep,
    question_decomposition_deep_kg,
    text_summary,
)

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, dict], None]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphSearchResult:
    final_answer: str
    text_channel_history: list[dict] = field(default_factory=list)
    kg_channel_history: list[dict] = field(default_factory=list)
    text_draft_answer: str = ""
    kg_draft_answer: str = ""
    text_evidence_verification: str = ""
    kg_evidence_verification: str = ""
    expansion_used: bool = False
    retrieval_count: int = 0
    stage_timings_ms: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _emit(progress_cb: Optional[ProgressCb], stage: str, payload: dict) -> None:
    if progress_cb is not None:
        try:
            progress_cb(stage, payload)
        except Exception:
            logger.debug("progress_cb raised on stage=%s", stage, exc_info=True)


async def _retrieve_and_serialize(
    ticker: str, query: str, *, mode: str = "A2",
    filter_type: Optional[str] = None,
    pipeline: str = "fan_out",
    bundle_name: str = "financial-default",
    n_hops: Optional[int] = None,
) -> str:
    """Retrieve + optional filter + serialize → string for prompt.

    PRD-004 v5 Phase 5B+: ``pipeline`` + ``bundle_name`` route the
    graph-side load to the right ``_graph/{graph_key}/`` index. Pre-5B
    silently used fan_out for every multi-hop run regardless of the
    slot the operator opened the modal from.
    """
    bundle = await graph_rag.aquery_context(
        ticker, query, mode=mode,
        pipeline=pipeline, bundle=bundle_name, n_hops=n_hops,
    )
    if filter_type is not None:
        bundle = graph_rag.context_filter(bundle, filter_type, query=query)
    return graph_rag.serialize_bundle_for_prompt(bundle)


async def run(
    ticker: str,
    question: str,
    *,
    llm: LLMClient,
    enable_self_reflection: bool = True,
    progress_cb: Optional[ProgressCb] = None,
    pipeline: str = "fan_out",
    bundle: str = "financial-default",
    n_hops: Optional[int] = None,
) -> GraphSearchResult:
    """Run the full dual-channel paper-mirror pipeline.

    ``pipeline`` + ``bundle`` route the graph-side retrieval to the
    slot's pipeline-specific ``_graph/{graph_key}/`` index. Default
    fan_out preserves pre-5B behavior for callers that don't pass
    slot context.
    """
    timings: dict[str, int] = {}
    retrieval_count = 0
    expansion_used = False

    # Stage 1 — Seed retrieval.
    t0 = time.time()
    grag_seed_str = await _retrieve_and_serialize(
        ticker, question, mode="A2",
        pipeline=pipeline, bundle_name=bundle, n_hops=n_hops,
    )
    retrieval_count += 1
    timings["seed_retrieval_ms"] = int((time.time() - t0) * 1000)
    _emit(progress_cb, "seed_retrieval_done", {})

    # Stage 2 — Dual summary.
    t0 = time.time()
    grag_text_summary = await text_summary(llm, question, grag_seed_str)
    grag_kg_summary = await kg_summary(llm, question, grag_seed_str)
    timings["dual_summary_ms"] = int((time.time() - t0) * 1000)
    _emit(progress_cb, "dual_summary_done", {})

    # Stage 3 — Dual decomposition.
    t0 = time.time()
    text_decomp_raw = await question_decomposition_deep(llm, question)
    kg_decomp_raw = await question_decomposition_deep_kg(llm, question)
    sub_queries_text = parse_subquery_text(text_decomp_raw)
    sub_queries_kg = parse_subquery_kg(kg_decomp_raw)
    timings["decomposition_ms"] = int((time.time() - t0) * 1000)
    _emit(progress_cb, "decomposition_done", {
        "n_text_sub_queries": len(sub_queries_text),
        "n_kg_sub_queries": len(sub_queries_kg),
    })

    # Stage 4 — Text channel iterative loop.
    text_history: list[tuple[str, str, str]] = []
    t0_text_loop = time.time()
    for i, sub_query_raw in enumerate(sub_queries_text):
        text_history_str = format_history_context(text_history)
        sub_query = sub_query_raw
        if "#" in sub_query:
            sub_query = await query_completer(
                llm, sub_query, text_decomp_raw + "\n\n" + text_history_str,
            ) or sub_query

        _emit(progress_cb, "text_subquery_start", {"index": i, "sub_query": sub_query})
        t0_sub = time.time()
        sub_ctx = await _retrieve_and_serialize(
            ticker, sub_query, mode="A2", filter_type="semantic",
            pipeline=pipeline, bundle_name=bundle, n_hops=n_hops,
        )
        retrieval_count += 1
        sub_ctx_summary = await text_summary(llm, sub_query, sub_ctx)
        sub_query_context_data = (text_history_str + "\n\n" + sub_ctx_summary).strip()
        sub_answer = await answer_generation(llm, sub_query, sub_query_context_data)
        text_history.append((sub_query, sub_ctx_summary, sub_answer))
        _emit(progress_cb, "text_subquery_done", {
            "index": i,
            "ms": int((time.time() - t0_sub) * 1000),
        })
    timings["text_loop_ms"] = int((time.time() - t0_text_loop) * 1000)

    text_history_str = format_history_context(text_history)
    t0 = time.time()
    text_draft = await answer_generation_deep(llm, question, text_history_str)
    timings["text_draft_ms"] = int((time.time() - t0) * 1000)
    _emit(progress_cb, "text_draft_done", {})

    # Stage 5 — Text channel self-reflection (gated).
    text_verif = ""
    if enable_self_reflection:
        t0 = time.time()
        text_verif = await evidence_verification(
            llm, question, text_history_str, text_draft,
        )
        timings["text_verification_ms"] = int((time.time() - t0) * 1000)
        _emit(progress_cb, "text_verification_done", {"verdict": text_verif})

        if "yes" in normalize(text_verif):
            t0 = time.time()
            expanded_raw = await query_expansion(
                llm, question, text_history_str, text_draft, text_verif,
            )
            expanded_queries = parse_expanded_queries(expanded_raw)
            for expanded in expanded_queries:
                expanded_ctx = await _retrieve_and_serialize(
                    ticker, expanded, mode="A2", filter_type="semantic",
                    pipeline=pipeline, bundle_name=bundle, n_hops=n_hops,
                )
                retrieval_count += 1
                expanded_summary = await text_summary(llm, expanded, expanded_ctx)
                text_history.append((expanded, expanded_summary, ""))
            text_history_str = format_history_context(text_history)
            timings["text_expansion_ms"] = int((time.time() - t0) * 1000)
            expansion_used = True
            _emit(progress_cb, "text_expansion_done", {
                "n_expanded": len(expanded_queries),
            })

    # Stage 6 — KG channel iterative loop.
    kg_history: list[tuple[str, str, str]] = []
    t0_kg_loop = time.time()
    for i, sub_kg_raw in enumerate(sub_queries_kg):
        kg_history_str = format_history_context(kg_history)
        sub_kg_query = sub_kg_raw
        if i > 0:
            sub_kg_query = await kg_query_completer(
                llm, sub_kg_query, kg_decomp_raw + "\n\n" + kg_history_str,
            ) or sub_kg_query

        _emit(progress_cb, "kg_subquery_start", {"index": i, "sub_query": sub_kg_query})
        t0_sub = time.time()
        sub_kg_clean = extract_words_str(sub_kg_query)
        sub_kg_ctx = await _retrieve_and_serialize(
            ticker, sub_kg_clean, mode="A2", filter_type="relational",
            pipeline=pipeline, bundle_name=bundle, n_hops=n_hops,
        )
        retrieval_count += 1
        sub_kg_summary = await kg_summary(llm, sub_kg_query, sub_kg_ctx)
        sub_kg_context_data = (kg_history_str + "\n\n" + sub_kg_summary).strip()
        sub_kg_answer = await answer_generation(llm, sub_kg_query, sub_kg_context_data)
        kg_history.append((sub_kg_query, sub_kg_summary, sub_kg_answer))
        _emit(progress_cb, "kg_subquery_done", {
            "index": i, "ms": int((time.time() - t0_sub) * 1000),
        })
    timings["kg_loop_ms"] = int((time.time() - t0_kg_loop) * 1000)

    kg_history_str = format_history_context(kg_history)
    t0 = time.time()
    kg_draft = await answer_generation_deep(llm, question, kg_history_str)
    timings["kg_draft_ms"] = int((time.time() - t0) * 1000)
    _emit(progress_cb, "kg_draft_done", {})

    # Stage 7 — KG channel self-reflection (gated).
    kg_verif = ""
    if enable_self_reflection:
        t0 = time.time()
        kg_verif = await evidence_verification(
            llm, question, kg_history_str, kg_draft,
        )
        timings["kg_verification_ms"] = int((time.time() - t0) * 1000)
        _emit(progress_cb, "kg_verification_done", {"verdict": kg_verif})

        if "yes" in normalize(kg_verif):
            t0 = time.time()
            expanded_raw = await query_expansion(
                llm, question, kg_history_str, kg_draft, kg_verif,
            )
            expanded_queries = parse_expanded_queries(expanded_raw)
            for expanded in expanded_queries:
                expanded_ctx = await _retrieve_and_serialize(
                    ticker, expanded, mode="A2", filter_type="relational",
                    pipeline=pipeline, bundle_name=bundle, n_hops=n_hops,
                )
                retrieval_count += 1
                expanded_summary = await kg_summary(llm, expanded, expanded_ctx)
                kg_history.append((expanded, expanded_summary, ""))
            kg_history_str = format_history_context(kg_history)
            timings["kg_expansion_ms"] = int((time.time() - t0) * 1000)
            expansion_used = True
            _emit(progress_cb, "kg_expansion_done", {
                "n_expanded": len(expanded_queries),
            })

    # Stage 8 — Merge dual channels.
    t0 = time.time()
    combined = (
        "Background information:\n"
        + grag_text_summary + "\n"
        + grag_kg_summary + "\n\n"
        + text_history_str + "\n\n"
        + kg_history_str
    )
    final_answer = await answer_generation_deep(llm, question, combined)
    timings["merge_ms"] = int((time.time() - t0) * 1000)
    _emit(progress_cb, "merge_done", {"final_answer_chars": len(final_answer)})

    text_history_payload = [
        {"sub_query": q, "summary": s, "answer": a}
        for (q, s, a) in text_history
    ]
    kg_history_payload = [
        {"sub_query": q, "summary": s, "answer": a}
        for (q, s, a) in kg_history
    ]

    return GraphSearchResult(
        final_answer=final_answer,
        text_channel_history=text_history_payload,
        kg_channel_history=kg_history_payload,
        text_draft_answer=text_draft,
        kg_draft_answer=kg_draft,
        text_evidence_verification=text_verif,
        kg_evidence_verification=kg_verif,
        expansion_used=expansion_used,
        retrieval_count=retrieval_count,
        stage_timings_ms=timings,
    )


__all__ = [
    "GraphSearchResult",
    "ProgressCb",
    "run",
]
