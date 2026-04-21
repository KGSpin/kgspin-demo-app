"""Retrieval: semantic-rank corpus chunks, then fan out through graph.

Algorithm:

1. Score every chunk by word-overlap Jaccard against the question.
2. Pick the top-``corpus_k`` chunks as seeds.
3. Collect every node whose ``provenance`` lists a seed chunk.
4. For each collected node, pull its neighbours (1-hop expansion).
5. Return the union of seed chunks + neighbour chunks, deduplicated,
   capped at ``top_k``.

This mirrors the "corpus-first, graph-expand" retrieval pattern: the
graph is used to broaden context around chunks already surfaced by
semantic similarity.
"""

from __future__ import annotations

import re
from typing import Any

WORD_RE = re.compile(r"\w+")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(text)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def retrieve(
    graph: dict[str, Any],
    question: str,
    *,
    top_k: int = 5,
    corpus_k: int = 3,
) -> list[str]:
    """Return up to ``top_k`` chunk texts relevant to ``question``."""
    q_tokens = _tokens(question)
    chunks = graph.get("chunks") or []
    if not chunks or not q_tokens:
        return []

    scored = sorted(
        chunks,
        key=lambda c: _jaccard(q_tokens, _tokens(c.get("text", ""))),
        reverse=True,
    )
    seed_ids = [c["chunk_id"] for c in scored[:corpus_k]]
    seed_set = set(seed_ids)

    # Find nodes provenance-rooted in seed chunks.
    seed_nodes: set[str] = set()
    for node in graph.get("nodes") or []:
        for p in node.get("provenance") or []:
            if p.get("chunk_id") in seed_set:
                seed_nodes.add(node["node_id"])
                break

    # Collect chunks from seed nodes + their 1-hop neighbours.
    neighbour_nodes: set[str] = set()
    for edge in graph.get("edges") or []:
        if edge.get("subject") in seed_nodes:
            neighbour_nodes.add(edge.get("object"))
        if edge.get("object") in seed_nodes:
            neighbour_nodes.add(edge.get("subject"))

    expanded_nodes = seed_nodes | neighbour_nodes
    expanded_chunk_ids: list[str] = list(seed_ids)
    node_index = {n["node_id"]: n for n in graph.get("nodes") or []}
    for nid in expanded_nodes:
        node = node_index.get(nid)
        if not node:
            continue
        for p in node.get("provenance") or []:
            cid = p.get("chunk_id")
            if cid and cid not in expanded_chunk_ids:
                expanded_chunk_ids.append(cid)

    chunk_index = {c["chunk_id"]: c for c in chunks}
    texts: list[str] = []
    for cid in expanded_chunk_ids:
        ch = chunk_index.get(cid)
        if ch is not None:
            texts.append(ch.get("text", ""))
        if len(texts) >= top_k:
            break
    return texts
