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

    PRD-004 v5 Phase 5B+ — three retrieval modes, each with a tailored
    nested structure:

    - ``chunk_first``: chunks anchor the retrieval; each chunk row
      carries its in-chunk subgraph (entities + edges whose evidence
      falls inside the chunk's char span). Populates ``chunk_rows``.
    - ``graph_first``: graph items (nodes/edges) anchor the retrieval;
      each match row carries its source chunk(s). Populates ``graph_match_rows``.
    - ``parallel``: dense and graph search run independently; both
      result lists are presented side-by-side in the prompt with no
      joining at retrieval time. Populates ``text_chunks`` AND
      ``graph_nodes``/``graph_edges`` as flat lists.

    Legacy flat fields (``text_chunks``, ``graph_nodes``, ``graph_edges``,
    ``evidence_spans``) are retained for back-compat with downstream
    consumers (serialize_bundle_for_prompt fallback, A2/A3 callers).
    Modes that emit nested rows ALSO populate the legacy flat fields
    with the same items unioned, so legacy renderers don't break.

    ``n_hops`` records how deep the graph traversal went (configurable
    via API param + ``graph_rag.n_hops_default`` config; default 3).
    """
    mode: str
    text_chunks: list[Chunk] = field(default_factory=list)
    graph_nodes: list[dict] = field(default_factory=list)
    graph_edges: list[dict] = field(default_factory=list)
    evidence_spans: list[tuple[int, int]] = field(default_factory=list)
    source_text: str = ""  # full plaintext, for resolving evidence_spans → text
    # New nested per-row structures (populated by chunk_first / graph_first):
    chunk_rows: list[dict] = field(default_factory=list)
    graph_match_rows: list[dict] = field(default_factory=list)
    n_hops: int = 0


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


def _expand_n_hops(
    seed_nodes: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
    *,
    n_hops: int = 3,
) -> tuple[list[dict], list[dict]]:
    """BFS to depth ``n_hops`` from ``seed_nodes``.

    Returns ``(nodes_within_n_hops, edges_within_n_hops)``. ``n_hops=1``
    is the original 1-hop neighborhood. Higher N pulls in transitive
    neighbors — useful for multi-hop questions where the answer entity
    is several relationships removed from any chunk-anchored entity.

    The traversal is undirected (an edge counts whether the seed sits
    on its src or tgt side) and stops as soon as no new ids are added
    in a layer (so ``n_hops=10`` on a small graph won't loop forever).
    """
    seed_ids = {n.get("id") for n in seed_nodes if n.get("id")}
    if not seed_ids:
        return list(seed_nodes), []
    by_id = {n.get("id"): n for n in all_nodes if n.get("id")}
    # Edge index — for each node id, which edges touch it.
    edges_by_node: dict[str, list[dict]] = {}
    for e in all_edges:
        for endpoint in (e.get("src"), e.get("tgt")):
            if endpoint:
                edges_by_node.setdefault(endpoint, []).append(e)

    visited_ids: set[str] = set(seed_ids)
    visited_edges_keyed_by_id: dict[str, dict] = {}  # rel_id -> edge
    frontier: set[str] = set(seed_ids)
    for _ in range(max(0, int(n_hops))):
        next_frontier: set[str] = set()
        for nid in frontier:
            for e in edges_by_node.get(nid, ()):
                eid = e.get("id") or f"{e.get('src','')}|{e.get('predicate','')}|{e.get('tgt','')}"
                visited_edges_keyed_by_id[eid] = e
                for endpoint in (e.get("src"), e.get("tgt")):
                    if endpoint and endpoint not in visited_ids:
                        visited_ids.add(endpoint)
                        next_frontier.add(endpoint)
        if not next_frontier:
            break
        frontier = next_frontier

    expanded_nodes = [by_id[i] for i in visited_ids if i in by_id]
    expanded_edges = list(visited_edges_keyed_by_id.values())
    return expanded_nodes, expanded_edges


def _entities_within_n_hops_of_chunk(
    seed_nodes: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
    *,
    n_hops: int,
) -> tuple[list[dict], list[dict]]:
    """Wrapper for ``_expand_n_hops`` that returns the subgraph anchored
    to one chunk's seed entities. Same signature as ``_expand_one_hop``
    used to be — easy drop-in replacement."""
    return _expand_n_hops(seed_nodes, all_nodes, all_edges, n_hops=n_hops)


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


_LEGACY_MODE_ALIAS = {
    "A2": "chunk_first",   # +1-hop graph (chunk-anchored)
    "A3": "graph_first",   # graph-as-corpus
    # A1 deliberately not aliased — drops in 5B+ since it duplicated dense.
}

_VALID_MODES = ("chunk_first", "graph_first", "parallel")


_DEFAULT_N_HOPS_FALLBACK = 3


def _default_n_hops() -> int:
    """Read ``graph_rag.n_hops_default`` from AppSettings; default 3.

    Best-effort: lazy-imports kgspin_demo_app.config and calls
    ``load_settings()``. Test paths that don't have a config fall back
    to the hardcoded default. Env override via
    ``KGSPIN_GRAPH_RAG_N_HOPS_DEFAULT`` for ad-hoc tuning.
    """
    import os
    env_override = os.environ.get("KGSPIN_GRAPH_RAG_N_HOPS_DEFAULT")
    if env_override:
        try:
            v = int(env_override)
            if v >= 0:
                return v
        except ValueError:
            pass
    try:
        from kgspin_demo_app.config import load_settings
        s = load_settings()
        v = getattr(getattr(s, "graph_rag", None), "n_hops_default", None)
        if isinstance(v, int) and v >= 0:
            return v
    except Exception:
        pass
    return _DEFAULT_N_HOPS_FALLBACK


async def aquery_context(
    ticker: str,
    question: str,
    mode: str = "chunk_first",
    top_k: int = 5,
    *,
    pipeline: str = "fan_out",
    bundle: str = "financial-default",
    n_hops: Optional[int] = None,
) -> ContextBundle:
    """Retrieve a context bundle in the requested GraphRAG mode.

    Modes (PRD-004 v5 Phase 5B+ redesign):

    - ``chunk_first`` — chunks anchor retrieval; each chunk row carries
      its in-chunk subgraph (entities + N-hop edges within the chunk).
    - ``graph_first`` — graph items anchor retrieval; each match row
      carries the source chunk(s) containing the evidence.
    - ``parallel`` — independent dense + graph searches; both result
      lists land side-by-side in the prompt with no joining.

    Legacy ``A2`` / ``A3`` codes are accepted as aliases. ``A1`` is
    rejected with a migration note (it was a useless duplicate of the
    dense pane).

    ``n_hops`` controls graph-traversal depth in ``chunk_first``. Default
    reads from ``graph_rag.n_hops_default`` config (default 3). The
    knob threads through to multi-hop's retrieval steps too since
    scenario_b's services share this entry point.
    """
    # Normalize mode: legacy aliases → new names; reject A1.
    if mode == "A1":
        raise ValueError(
            "mode 'A1' (Standard) was dropped in 5B+. It duplicated the "
            "dense-RAG pane. Use 'chunk_first', 'graph_first', or 'parallel'."
        )
    mode = _LEGACY_MODE_ALIAS.get(mode, mode)
    if mode not in _VALID_MODES:
        raise ValueError(
            f"mode must be one of {_VALID_MODES}; got {mode!r}"
        )

    hops = _default_n_hops() if n_hops is None else int(n_hops)

    if mode == "chunk_first":
        chunks = dense_rag.search(ticker, question, top_k=top_k)
        graph = get_graph_corpus(ticker, pipeline=pipeline, bundle=bundle)
        chunk_rows: list[dict] = []
        union_node_ids: set[str] = set()
        union_edge_ids: set[str] = set()
        flat_nodes: list[dict] = []
        flat_edges: list[dict] = []
        for c in chunks:
            seeds = _entities_in_chunk_span(
                graph.nodes, c.source_offset[0], c.source_offset[1],
            )
            sub_nodes, sub_edges = _expand_n_hops(
                seeds, graph.nodes, graph.edges, n_hops=hops,
            )
            chunk_rows.append({
                "chunk": {
                    "id": c.chunk_id,
                    "text": c.text,
                    "score": float(getattr(c, "score", 0.0)),
                    "source_offset": list(c.source_offset),
                },
                "subgraph_nodes": sub_nodes,
                "subgraph_edges": sub_edges,
            })
            for n in sub_nodes:
                nid = n.get("id") or ""
                if nid and nid not in union_node_ids:
                    union_node_ids.add(nid)
                    flat_nodes.append(n)
            for e in sub_edges:
                eid = e.get("id") or f"{e.get('src','')}|{e.get('predicate','')}|{e.get('tgt','')}"
                if eid not in union_edge_ids:
                    union_edge_ids.add(eid)
                    flat_edges.append(e)
        evidence_spans = [
            (c.source_offset[0], c.source_offset[1]) for c in chunks
        ]
        return ContextBundle(
            mode="chunk_first",
            text_chunks=chunks,
            graph_nodes=flat_nodes,
            graph_edges=flat_edges,
            evidence_spans=evidence_spans,
            source_text=graph.source_text,
            chunk_rows=chunk_rows,
            n_hops=hops,
        )

    if mode == "graph_first":
        graph = get_graph_corpus(ticker, pipeline=pipeline, bundle=bundle)
        matched_nodes, matched_edges = _retrieve_graph_items(
            question, graph, top_k=top_k,
        )

        def _chunks_containing_span(start: int, end: int) -> list[dict]:
            """Best-effort: pull the chunk(s) whose offsets contain or
            overlap [start, end]. Defers full chunk indexing to the
            ``_doc/`` corpus loader; returns empty if dense_rag's chunks
            aren't loadable for this ticker."""
            try:
                corpus = dense_rag.get_corpus(ticker)
            except Exception:
                return []
            out: list[dict] = []
            for raw in corpus.chunks:
                cs = int(raw.get("char_offset_start", 0))
                ce = int(raw.get("char_offset_end", 0))
                if ce >= start and cs <= end:
                    out.append({
                        "id": raw.get("id"),
                        "text": raw.get("text", ""),
                        "char_offset_start": cs,
                        "char_offset_end": ce,
                    })
            return out

        match_rows: list[dict] = []
        spans: list[tuple[int, int]] = []
        for n in matched_nodes:
            offs = n.get("parent_doc_offsets") or [0, 0]
            if len(offs) == 2 and offs[1] > offs[0]:
                spans.append((int(offs[0]), int(offs[1])))
                source_chunks = _chunks_containing_span(int(offs[0]), int(offs[1]))
            else:
                source_chunks = []
            match_rows.append({
                "kind": "node",
                "node": n,
                "source_chunks": source_chunks,
            })
        for e in matched_edges:
            offs = e.get("evidence_char_span") or [0, 0]
            if len(offs) == 2 and offs[1] > offs[0]:
                spans.append((int(offs[0]), int(offs[1])))
                source_chunks = _chunks_containing_span(int(offs[0]), int(offs[1]))
            else:
                source_chunks = []
            match_rows.append({
                "kind": "edge",
                "edge": e,
                "source_chunks": source_chunks,
            })
        spans = sorted(set(spans))
        return ContextBundle(
            mode="graph_first",
            text_chunks=[],
            graph_nodes=matched_nodes,
            graph_edges=matched_edges,
            evidence_spans=spans,
            source_text=graph.source_text,
            graph_match_rows=match_rows,
            n_hops=hops,
        )

    # parallel — dense + graph in parallel; no joining at retrieval time.
    chunks = dense_rag.search(ticker, question, top_k=top_k)
    graph = get_graph_corpus(ticker, pipeline=pipeline, bundle=bundle)
    matched_nodes, matched_edges = _retrieve_graph_items(
        question, graph, top_k=top_k,
    )
    chunk_spans = [(c.source_offset[0], c.source_offset[1]) for c in chunks]
    graph_spans: list[tuple[int, int]] = []
    for n in matched_nodes:
        offs = n.get("parent_doc_offsets") or [0, 0]
        if len(offs) == 2 and offs[1] > offs[0]:
            graph_spans.append((int(offs[0]), int(offs[1])))
    for e in matched_edges:
        offs = e.get("evidence_char_span") or [0, 0]
        if len(offs) == 2 and offs[1] > offs[0]:
            graph_spans.append((int(offs[0]), int(offs[1])))
    return ContextBundle(
        mode="parallel",
        text_chunks=chunks,
        graph_nodes=matched_nodes,
        graph_edges=matched_edges,
        evidence_spans=sorted(set(chunk_spans + graph_spans)),
        source_text=graph.source_text,
        n_hops=hops,
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


def _serialize_chunk_first(bundle: ContextBundle) -> str:
    """Per-row format for chunk_first mode: each chunk inline with its
    in-chunk subgraph. Easier for the LLM to ground text in structure
    than separate flat sections."""
    parts: list[str] = []
    parts.append(f"[CHUNK-FIRST RETRIEVAL — {bundle.n_hops}-hop subgraphs]")
    for i, row in enumerate(bundle.chunk_rows or [], start=1):
        ch = row.get("chunk") or {}
        parts.append(f"\n## Chunk {i} ({ch.get('id', '?')})")
        parts.append(f"Text: {ch.get('text', '')}")
        sub_nodes = row.get("subgraph_nodes") or []
        sub_edges = row.get("subgraph_edges") or []
        if sub_nodes:
            parts.append("Subgraph nodes:")
            for n in sub_nodes:
                parts.append(
                    f"  - {n.get('id','')}: {n.get('text','')} [{n.get('type','UNKNOWN')}]"
                )
        if sub_edges:
            parts.append("Subgraph edges:")
            for e in sub_edges:
                parts.append(
                    f"  - ({e.get('src','?')}) --{e.get('predicate','?')}--> ({e.get('tgt','?')})"
                )
    return "\n".join(parts).strip()


def _serialize_graph_first(bundle: ContextBundle) -> str:
    """Per-row format for graph_first mode: each matched node/edge
    followed by its source chunk(s)."""
    parts: list[str] = []
    parts.append("[GRAPH-FIRST RETRIEVAL — graph items + source chunks]")
    for i, row in enumerate(bundle.graph_match_rows or [], start=1):
        kind = row.get("kind", "")
        if kind == "node":
            n = row.get("node") or {}
            parts.append(
                f"\n## Match {i} (node) {n.get('id','')}: "
                f"{n.get('text','')} [{n.get('type','UNKNOWN')}]"
            )
        elif kind == "edge":
            e = row.get("edge") or {}
            parts.append(
                f"\n## Match {i} (edge) "
                f"({e.get('src','?')}) --{e.get('predicate','?')}--> ({e.get('tgt','?')})"
            )
            ev = e.get("evidence_text") or ""
            if ev:
                parts.append(f"Evidence: {ev}")
        for ch in row.get("source_chunks") or []:
            parts.append(f"Source chunk ({ch.get('id','?')}): {ch.get('text','')}")
    return "\n".join(parts).strip()


def _serialize_parallel(bundle: ContextBundle) -> str:
    """Two flat sections side-by-side: dense chunks + graph items.
    No joining — the LLM sees the two retrieval streams as independent
    columns of context."""
    parts: list[str] = []
    if bundle.text_chunks:
        parts.append("[DENSE RETRIEVAL]")
        parts.append(dense_rag.serialize_chunks(bundle.text_chunks))
    if bundle.graph_nodes or bundle.graph_edges:
        parts.append("\n[GRAPH RETRIEVAL]")
        if bundle.graph_nodes:
            parts.append("Matched nodes:")
            for n in bundle.graph_nodes:
                parts.append(
                    f"  - {n.get('id','')}: {n.get('text','')} [{n.get('type','UNKNOWN')}]"
                )
        if bundle.graph_edges:
            parts.append("Matched edges:")
            for e in bundle.graph_edges:
                parts.append(
                    f"  - ({e.get('src','?')}) --{e.get('predicate','?')}--> ({e.get('tgt','?')})"
                )
    return "\n".join(parts).strip()


def serialize_bundle_for_prompt(bundle: ContextBundle) -> str:
    """Bundle → string contract per PRD plan §3.E.

    PRD-004 v5 Phase 5B+ — three modes, three serializers. Legacy modes
    (A2/A3 with no nested rows) fall through to the original flat
    section format.
    """
    if bundle.mode == "chunk_first" and bundle.chunk_rows:
        return _serialize_chunk_first(bundle)
    if bundle.mode == "graph_first" and bundle.graph_match_rows:
        return _serialize_graph_first(bundle)
    if bundle.mode == "parallel":
        return _serialize_parallel(bundle)

    # Legacy fallback (flat sections; A2/A3 from old callers, or empty bundles).
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
