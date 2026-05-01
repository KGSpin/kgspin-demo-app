"""GraphRAG retrieval service — PRD-004 v5 Phase 5A deliverable C.

Three retrieval patterns the Scenario A right pane toggles between:

  * **A1 — Standard (sanity)**: pure dense (BM25 + cosine RRF) over
    chunks. Returns the same chunks as ``dense_rag.search``; empty
    graph fields. Sanity check that the graph code path hasn't broken
    the baseline.

  * **A2 — +1-hop graph**: dense_rag chunks + the 1-hop graph
    neighborhood of every entity whose ``parent_doc_offsets`` overlap
    a returned chunk's character span. Adds chunks-and-the-graph-
    around-them context to the LLM.

  * **A3 — Graph-as-corpus**: BM25 + cosine RRF over the
    ``graph_nodes`` and ``graph_edges`` corpora; resolves matched
    nodes/edges back to source spans and exposes those as
    ``evidence_spans``. Treats the extracted graph itself as the
    primary searchable index.

Public API also includes ``aquery_context`` (paper-compatible
signature, used by ``services/graphsearch_pipeline``) and
``context_filter`` (semantic / relational filters). The bundle →
string serialization contract (``serialize_bundle_for_prompt``) is
the input format every paper prompt receives; specified in PRD plan
§3.E and exercised by ``test_graph_rag.py``.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from kgspin_demo_app.services import dense_rag
from kgspin_demo_app.services.dense_rag import (
    Chunk,
    CorpusNotBuilt,
    _bm25_tokenize,
    _cosine_top_indices,
    _l2_normalize,
    _rrf_fuse,
)

logger = logging.getLogger(__name__)

WHITESPACE_RE = re.compile(r"\S+")

# Graph-side cache: ticker → loaded graph artifacts.
_graph_cache: dict[str, "GraphCorpus"] = {}
_graph_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextBundle:
    """The unit of evidence returned by ``aquery_context``.

    ``mode`` is ``'A1' | 'A2' | 'A3'``. The fields that aren't
    populated for a given mode stay empty (e.g. A1 has chunks but no
    graph_nodes). ``evidence_spans`` holds source-text spans that
    correspond to matched graph items in A3, or to chunk spans in A2
    (so the prompt can show the underlying evidence text alongside
    the graph context).
    """
    mode: str
    text_chunks: list[Chunk] = field(default_factory=list)
    graph_nodes: list[dict] = field(default_factory=list)
    graph_edges: list[dict] = field(default_factory=list)
    evidence_spans: list[tuple[int, int]] = field(default_factory=list)
    source_text: str = ""  # full plaintext, for resolving evidence_spans → text


@dataclass
class GraphCorpus:
    ticker: str
    nodes: list[dict]
    edges: list[dict]
    node_embeddings: np.ndarray
    edge_embeddings: np.ndarray
    node_bm25_corpus: list[list[str]]
    edge_bm25_corpus: list[list[str]]
    source_text: str  # plaintext (cached for evidence-span resolution)


# ---------------------------------------------------------------------------
# Loader (separate from dense_rag's chunks loader)
# ---------------------------------------------------------------------------


def _node_text_for_index(node: dict) -> str:
    return "{} [{}] {}".format(
        node.get("text", ""),
        node.get("type", "UNKNOWN"),
        node.get("semantic_definition", "") or "",
    ).strip()


def _edge_text_for_index(edge: dict) -> str:
    return "{} {}".format(
        edge.get("predicate", "") or "",
        edge.get("evidence_text", "") or "",
    ).strip()


def _resolve_graph_dir(
    ticker: str,
    *,
    pipeline: str = "fan_out",
    bundle: str = "financial-default",
) -> Path:
    """Resolve the on-disk dir for ``(ticker, pipeline, bundle)``'s graph index.

    Search order (PRD-004 v5 Phase 5B / D5):
    1. Lander tree's ``_graph/{pipeline}__{bundle}__core-{sha[:7]}/`` under
       the latest dated subdir.
    2. Legacy ``tests/fixtures/rag-corpus/{ticker}/`` fallback (back-compat
       for pre-5B fan_out fixtures only — non-fan_out pipelines need a
       lander-tree _graph/ dir or build will fail).
    """
    from kgspin_demo_app.services.cache_layout import (
        kgspin_core_sha,
        resolve_locator,
    )

    loc = resolve_locator(ticker)
    if loc is not None:
        candidate = loc.graph_corpus_dir(
            pipeline=pipeline, bundle=bundle, core_sha=kgspin_core_sha(),
        )
        if candidate.exists():
            return candidate
    # Legacy fallback (pre-5B fan_out only).
    return dense_rag.get_corpus_root() / ticker


def _load_graph_corpus(
    ticker: str,
    *,
    pipeline: str = "fan_out",
    bundle: str = "financial-default",
) -> GraphCorpus:
    out_dir = _resolve_graph_dir(ticker, pipeline=pipeline, bundle=bundle)
    nodes_path = out_dir / "graph_nodes.json"
    edges_path = out_dir / "graph_edges.json"
    nemb_path = out_dir / "graph_node_embeddings.npy"
    eemb_path = out_dir / "graph_edge_embeddings.npy"
    # source.txt sits alongside the lander artifact (not in _graph/), so
    # walk up to the dated subdir to find it.
    src_path = (out_dir.parent.parent / "source.txt") if out_dir.name != ticker else (out_dir / "source.txt")
    for p in (nodes_path, edges_path, nemb_path, eemb_path):
        if not p.exists():
            raise CorpusNotBuilt(
                f"Graph corpus for {ticker!r}/{pipeline} missing: {p} not found. "
                f"Run `python -m scripts.warm_caches --ticker {ticker} --pipeline {pipeline}` first."
            )
    nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
    edges = json.loads(edges_path.read_text(encoding="utf-8"))
    nemb = np.load(nemb_path, mmap_mode="r")
    eemb = np.load(eemb_path, mmap_mode="r")
    source_text = src_path.read_text(encoding="utf-8") if src_path.exists() else ""

    node_bm25 = [_bm25_tokenize(_node_text_for_index(n)) for n in nodes]
    edge_bm25 = [_bm25_tokenize(_edge_text_for_index(e)) for e in edges]
    return GraphCorpus(
        ticker=ticker, nodes=nodes, edges=edges,
        node_embeddings=nemb, edge_embeddings=eemb,
        node_bm25_corpus=node_bm25, edge_bm25_corpus=edge_bm25,
        source_text=source_text,
    )


def get_graph_corpus(
    ticker: str,
    *,
    pipeline: str = "fan_out",
    bundle: str = "financial-default",
) -> GraphCorpus:
    """Return the graph corpus for ``(ticker, pipeline, bundle)``.

    Cache key includes pipeline + bundle so different slot types share
    nothing. Defaults preserve pre-5B fan_out behavior for back-compat
    callers that don't yet pass pipeline.
    """
    cache_key = f"{ticker}|{pipeline}|{bundle}"
    with _graph_cache_lock:
        if cache_key not in _graph_cache:
            _graph_cache[cache_key] = _load_graph_corpus(
                ticker, pipeline=pipeline, bundle=bundle,
            )
        return _graph_cache[cache_key]


def _clear_graph_cache() -> None:
    """Test hook — drop cached graph corpora so set_corpus_root takes."""
    with _graph_cache_lock:
        _graph_cache.clear()


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _bm25_top_indices(query_tokens: list[str], corpus_tokens: list[list[str]], pool: int) -> list[int]:
    if not corpus_tokens:
        return []
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi(corpus_tokens)
    scores = bm25.get_scores(query_tokens)
    pool = min(pool, len(scores))
    if pool == 0:
        return []
    idx = np.argpartition(-scores, pool - 1)[:pool]
    return idx[np.argsort(-scores[idx])].tolist()


def _retrieve_graph_items(
    query: str, corpus: GraphCorpus, top_k: int = 5,
    *, rrf_k: float = 60.0,
) -> tuple[list[dict], list[dict]]:
    """A3 retrieval: BM25 + cosine RRF over (nodes, edges) separately."""
    embedder = dense_rag._get_embedder()
    q_emb_raw = embedder.encode(query) if isinstance(query, str) else embedder.encode([query])[0]
    q_emb = _l2_normalize(np.asarray(q_emb_raw, dtype=np.float32))

    pool = max(50, top_k * 5)

    # Nodes.
    cos_node_idx = _cosine_top_indices(q_emb, np.asarray(corpus.node_embeddings), pool).tolist()
    bm25_node_idx = _bm25_top_indices(_bm25_tokenize(query), corpus.node_bm25_corpus, pool)
    node_fused = _rrf_fuse(bm25_node_idx, cos_node_idx, rrf_k)[:top_k]
    matched_nodes = [
        {**corpus.nodes[idx], "_score": float(score)}
        for idx, score in node_fused
    ]

    # Edges.
    cos_edge_idx = _cosine_top_indices(q_emb, np.asarray(corpus.edge_embeddings), pool).tolist()
    bm25_edge_idx = _bm25_top_indices(_bm25_tokenize(query), corpus.edge_bm25_corpus, pool)
    edge_fused = _rrf_fuse(bm25_edge_idx, cos_edge_idx, rrf_k)[:top_k]
    matched_edges = [
        {**corpus.edges[idx], "_score": float(score)}
        for idx, score in edge_fused
    ]
    return matched_nodes, matched_edges


def _expand_one_hop(
    seed_nodes: list[dict], all_nodes: list[dict], all_edges: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Given a seed set of nodes, return (nodes_with_neighbors, edges_among_them)."""
    seed_ids = {n.get("id") for n in seed_nodes if n.get("id")}
    if not seed_ids:
        return list(seed_nodes), []
    relevant_edges = [
        e for e in all_edges
        if (e.get("src") in seed_ids) or (e.get("tgt") in seed_ids)
    ]
    neighbor_ids: set[str] = set()
    for e in relevant_edges:
        if e.get("src"): neighbor_ids.add(e["src"])
        if e.get("tgt"): neighbor_ids.add(e["tgt"])
    by_id = {n.get("id"): n for n in all_nodes if n.get("id")}
    expanded_nodes = [by_id[i] for i in (seed_ids | neighbor_ids) if i in by_id]
    return expanded_nodes, relevant_edges


def _entities_in_chunk_span(
    nodes: list[dict], chunk_start: int, chunk_end: int,
) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        offsets = n.get("parent_doc_offsets") or [0, 0]
        if len(offsets) != 2:
            continue
        n_start, n_end = int(offsets[0]), int(offsets[1])
        # Overlap test (intervals).
        if n_end >= chunk_start and n_start <= chunk_end:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def aquery_context(
    ticker: str,
    question: str,
    mode: str = "A2",
    top_k: int = 5,
    *,
    pipeline: str = "fan_out",
    bundle: str = "financial-default",
) -> ContextBundle:
    """Retrieve a context bundle in the requested GraphRAG mode.

    ``async`` to mirror the GraphSearch paper's
    ``GraphRAG.aquery_context`` signature so the paper-mirror pipeline
    (deliverable E) can swap implementations cleanly.

    PRD-004 v5 Phase 5B (D5): ``pipeline`` + ``bundle`` route the
    graph-side load to the right ``_graph/{graph_key}/`` index. Pre-5B
    silently used fan_out for every slot regardless of pipeline.
    """
    if mode not in ("A1", "A2", "A3"):
        raise ValueError(f"mode must be 'A1', 'A2', or 'A3'; got {mode!r}")

    if mode == "A1":
        chunks = dense_rag.search(ticker, question, top_k=top_k)
        return ContextBundle(mode="A1", text_chunks=chunks)

    if mode == "A2":
        chunks = dense_rag.search(ticker, question, top_k=top_k)
        graph = get_graph_corpus(ticker, pipeline=pipeline, bundle=bundle)
        seed_nodes: list[dict] = []
        for c in chunks:
            seed_nodes.extend(_entities_in_chunk_span(
                graph.nodes, c.source_offset[0], c.source_offset[1],
            ))
        # Dedup by id while preserving order.
        seen_ids: set[str] = set()
        deduped: list[dict] = []
        for n in seed_nodes:
            nid = n.get("id") or ""
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                deduped.append(n)
        nodes_expanded, edges_in_hood = _expand_one_hop(
            deduped, graph.nodes, graph.edges,
        )
        evidence_spans = [
            (c.source_offset[0], c.source_offset[1]) for c in chunks
        ]
        return ContextBundle(
            mode="A2",
            text_chunks=chunks,
            graph_nodes=nodes_expanded,
            graph_edges=edges_in_hood,
            evidence_spans=evidence_spans,
            source_text=graph.source_text,
        )

    # A3 — graph-as-corpus.
    graph = get_graph_corpus(ticker, pipeline=pipeline, bundle=bundle)
    matched_nodes, matched_edges = _retrieve_graph_items(
        question, graph, top_k=top_k,
    )
    spans: list[tuple[int, int]] = []
    for n in matched_nodes:
        offsets = n.get("parent_doc_offsets") or [0, 0]
        if len(offsets) == 2 and (offsets[1] > offsets[0]):
            spans.append((int(offsets[0]), int(offsets[1])))
    for e in matched_edges:
        offsets = e.get("evidence_char_span") or [0, 0]
        if len(offsets) == 2 and (offsets[1] > offsets[0]):
            spans.append((int(offsets[0]), int(offsets[1])))
    # Dedup spans.
    spans = sorted(set(spans))
    return ContextBundle(
        mode="A3",
        text_chunks=[],
        graph_nodes=matched_nodes,
        graph_edges=matched_edges,
        evidence_spans=spans,
        source_text=graph.source_text,
    )


def context_filter(
    bundle: ContextBundle,
    filter_type: str,
    query: Optional[str] = None,
) -> ContextBundle:
    """Two filter modes mirroring the paper:

    * ``'semantic'`` — re-embed and rank items by similarity to ``query``
      (the typical "filter by semantic similarity to a sub-query").
    * ``'relational'`` — restrict to items connected via 1-hop edges
      (the typical "filter by graph adjacency to current frontier").
    """
    if filter_type not in ("semantic", "relational"):
        raise ValueError(
            f"filter_type must be 'semantic' or 'relational'; got {filter_type!r}"
        )

    if filter_type == "semantic":
        if not query:
            return bundle
        embedder = dense_rag._get_embedder()
        q_emb = _l2_normalize(
            np.asarray(embedder.encode(query), dtype=np.float32),
        )

        # Score each item by its embedded text against the query.
        def _score_text(t: str) -> float:
            v = _l2_normalize(np.asarray(embedder.encode(t), dtype=np.float32))
            return float(np.dot(q_emb, v))

        scored_chunks = sorted(
            bundle.text_chunks,
            key=lambda c: _score_text(c.text),
            reverse=True,
        )
        scored_nodes = sorted(
            bundle.graph_nodes,
            key=lambda n: _score_text(_node_text_for_index(n)),
            reverse=True,
        )
        scored_edges = sorted(
            bundle.graph_edges,
            key=lambda e: _score_text(_edge_text_for_index(e)),
            reverse=True,
        )
        return ContextBundle(
            mode=bundle.mode,
            text_chunks=scored_chunks,
            graph_nodes=scored_nodes,
            graph_edges=scored_edges,
            evidence_spans=bundle.evidence_spans,
            source_text=bundle.source_text,
        )

    # 'relational' — restrict to items connected to current node-set
    # via 1-hop edges. The "current frontier" is the seed nodes; we
    # keep nodes referenced by edges and edges that touch a seed node.
    seed_ids = {n.get("id") for n in bundle.graph_nodes if n.get("id")}
    if not seed_ids:
        return bundle
    kept_edges = [
        e for e in bundle.graph_edges
        if (e.get("src") in seed_ids) or (e.get("tgt") in seed_ids)
    ]
    neighbor_ids = set()
    for e in kept_edges:
        if e.get("src"): neighbor_ids.add(e["src"])
        if e.get("tgt"): neighbor_ids.add(e["tgt"])
    kept_node_ids = seed_ids | neighbor_ids
    kept_nodes = [n for n in bundle.graph_nodes if n.get("id") in kept_node_ids]
    return ContextBundle(
        mode=bundle.mode,
        text_chunks=bundle.text_chunks,
        graph_nodes=kept_nodes,
        graph_edges=kept_edges,
        evidence_spans=bundle.evidence_spans,
        source_text=bundle.source_text,
    )


def serialize_bundle_for_prompt(bundle: ContextBundle) -> str:
    """Bundle → string contract per PRD plan §3.E.

    Produces a deterministic, machine-parseable string with explicit
    section markers. Every paper-mirror prompt receives strings in
    this format; the dual-channel (text vs KG) distinction is
    preserved by the section headers.
    """
    sections: list[str] = []

    if bundle.text_chunks:
        sections.append(dense_rag.serialize_chunks(bundle.text_chunks))

    if bundle.graph_nodes:
        sections.append("[GRAPH NODES]")
        for n in bundle.graph_nodes:
            nid = n.get("id", "")
            text = n.get("text", "")
            ntype = n.get("type", "UNKNOWN")
            semdef = n.get("semantic_definition") or ""
            line = f"- {nid}: {text} [{ntype}]"
            if semdef:
                line += f" — {semdef}"
            sections[-1] = sections[-1] if sections[-1] != "[GRAPH NODES]" else "[GRAPH NODES]"
            # Append node line to the GRAPH NODES section. We'll join later.
            sections.append(line)

    if bundle.graph_edges:
        sections.append("[GRAPH EDGES]")
        for e in bundle.graph_edges:
            src = e.get("src", "?")
            tgt = e.get("tgt", "?")
            pred = e.get("predicate", "?")
            evidence = e.get("evidence_text", "") or ""
            span = e.get("evidence_char_span") or [0, 0]
            sections.append(
                f"- ({src}) --{pred}--> ({tgt})"
            )
            if evidence:
                sections.append(
                    f"  evidence: \"{evidence}\" [span {int(span[0])}-{int(span[1])}]"
                )

    if bundle.evidence_spans and bundle.source_text:
        sections.append("[EVIDENCE SPANS]")
        seen: set[tuple[int, int]] = set()
        for start, end in bundle.evidence_spans:
            if (start, end) in seen:
                continue
            seen.add((start, end))
            span_text = bundle.source_text[start:end]
            sections.append(f"(span {start}-{end}): \"{span_text}\"")

    return "\n".join(sections).strip()


__all__ = [
    "ContextBundle",
    "GraphCorpus",
    "aquery_context",
    "context_filter",
    "get_graph_corpus",
    "serialize_bundle_for_prompt",
]
