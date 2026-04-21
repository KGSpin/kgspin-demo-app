"""Retrieval: match entities to the question, then fan out to documents.

Algorithm:

1. Score every node by token-Jaccard of its ``surface_form`` + aliases
   against the question.
2. Pick the top-``entity_k`` nodes.
3. Walk 1-hop neighbours via ``edges``.
4. Gather every chunk listed in the nodes' or edges' ``provenance``.
5. Return up to ``top_k`` chunk texts, ordered by number of node hits
   per chunk (multi-hit chunks first — they are the densest
   multi-hop-relevant spans).

This mirrors the "graph-first, document-expand" pattern: the graph
chooses relevant entities, chunks flow in as evidence carriers.
"""

from __future__ import annotations

import re
from collections import Counter
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
    entity_k: int = 5,
) -> list[str]:
    q_tokens = _tokens(question)
    nodes = graph.get("nodes") or []
    chunks = graph.get("chunks") or []
    if not nodes or not chunks or not q_tokens:
        return []

    def node_score(n: dict[str, Any]) -> float:
        surface = " ".join([n.get("surface_form", ""), *n.get("aliases", [])])
        return _jaccard(q_tokens, _tokens(surface))

    ranked = sorted(nodes, key=node_score, reverse=True)[:entity_k]
    seed_nodes = {n["node_id"] for n in ranked}

    # 1-hop expansion via edges
    neighbours: set[str] = set()
    edges = graph.get("edges") or []
    for e in edges:
        if e.get("subject") in seed_nodes:
            neighbours.add(e.get("object"))
        if e.get("object") in seed_nodes:
            neighbours.add(e.get("subject"))

    expanded = seed_nodes | neighbours

    # Count chunk hits from expanded nodes.
    node_index = {n["node_id"]: n for n in nodes}
    chunk_hits: Counter[str] = Counter()
    for nid in expanded:
        node = node_index.get(nid)
        if not node:
            continue
        for p in node.get("provenance") or []:
            cid = p.get("chunk_id")
            if cid:
                chunk_hits[cid] += 1
    # Also boost chunks that carry edge evidence across seed nodes.
    for e in edges:
        if e.get("subject") in expanded and e.get("object") in expanded:
            for p in e.get("provenance") or []:
                cid = p.get("chunk_id")
                if cid:
                    chunk_hits[cid] += 2

    chunk_index = {c["chunk_id"]: c for c in chunks}
    ordered = [cid for cid, _ in chunk_hits.most_common(top_k)]
    return [chunk_index[cid].get("text", "") for cid in ordered if cid in chunk_index]
