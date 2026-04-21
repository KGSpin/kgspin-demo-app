"""Retrieval: reciprocal-rank-fusion of corpus-first and graph-first.

Composes ``fan_out_from_corpus.retrieve`` and ``fan_out_from_graph.retrieve``
via Reciprocal Rank Fusion (k=60 per the standard default) to produce
a unified ranking. Intended as the benchmark-target composite strategy
against which the two single-strategy lists are measured.
"""

from __future__ import annotations

from typing import Any

from . import fan_out_from_corpus, fan_out_from_graph

RRF_K = 60


def retrieve(
    graph: dict[str, Any],
    question: str,
    *,
    top_k: int = 5,
    corpus_k: int = 5,
    entity_k: int = 5,
) -> list[str]:
    corpus_hits = fan_out_from_corpus.retrieve(
        graph, question, top_k=top_k, corpus_k=corpus_k,
    )
    graph_hits = fan_out_from_graph.retrieve(
        graph, question, top_k=top_k, entity_k=entity_k,
    )

    scores: dict[str, float] = {}
    for rank, text in enumerate(corpus_hits):
        scores[text] = scores.get(text, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, text in enumerate(graph_hits):
        scores[text] = scores.get(text, 0.0) + 1.0 / (RRF_K + rank + 1)

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [t for t, _ in ordered[:top_k]]
