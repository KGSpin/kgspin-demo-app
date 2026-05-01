"""Per-(doc, pipeline, bundle) graph index builder (PRD-004 v5 Phase 5B / D5).

Produces ``_graph/{graph_key}/`` next to the lander's ``source.txt``:

    graph_nodes.json, graph_edges.json,
    graph_node_embeddings.npy, graph_edge_embeddings.npy, manifest.json

Provenance is preserved as build-time metadata: each row carries the
pipeline that emitted it, the resolved char_span (via
``kgspin_interface.text.normalize.resolve_evidence_offsets``), and the
``join_confidence ∈ {sentence, chunk, none}`` flag (D6 — internal only;
not surfaced in modal Why-tab UI per CTO scope cut).

Bridge edges (PRD-056 v2) flow through with ``kind`` preserved on disk
but with no special retrieval behavior in 5B (deferred to a future
sprint when the unified-graph data layer lands).
"""
from __future__ import annotations

import json
import logging
import pickle  # noqa: F401  — reserved for any future BM25 index persistence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from kgspin_demo_app.services.cache_layout import (
    DocLocator,
    compute_doc_key,
    compute_graph_key,
    kgspin_core_sha,
    read_lander_manifest,
)
from kgspin_demo_app.services.doc_corpus_builder import (
    Chunk,
    EMBED_MODEL_NAME,
    _embed_texts,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provenance row shape (graph_edges.json / graph_nodes.json)
# ---------------------------------------------------------------------------


def _node_to_row(
    node_dict: dict,
    *,
    pipeline: str,
    char_span: tuple[int, int] | None,
    join_confidence: str,
) -> dict:
    return {
        "id": node_dict.get("id") or node_dict.get("text") or "",
        "text": node_dict.get("text", ""),
        "type": node_dict.get("entity_type", "UNKNOWN"),
        "semantic_definition": node_dict.get("semantic_definition") or "",
        "parent_doc_offsets": [char_span[0], char_span[1]] if char_span else None,
        "provenance": [
            {
                "evidence_kind": node_dict.get("kind", "intra"),
                "pipeline": pipeline,
                "sentence_text": node_dict.get("sentence_text", ""),
                "char_span": [char_span[0], char_span[1]] if char_span else None,
                "join_confidence": join_confidence,
            }
        ],
    }


def _edge_to_row(
    edge_dict: dict,
    *,
    pipeline: str,
    char_span: tuple[int, int] | None,
    join_confidence: str,
) -> dict:
    return {
        "id": edge_dict.get("id", ""),
        "src": edge_dict.get("src") or edge_dict.get("subject_id") or "",
        "tgt": edge_dict.get("tgt") or edge_dict.get("object_id") or "",
        "predicate": edge_dict.get("predicate", ""),
        "evidence_text": edge_dict.get("sentence_text", ""),
        "evidence_char_span": [char_span[0], char_span[1]] if char_span else None,
        "kind": edge_dict.get("kind", "intra"),
        "provenance": [
            {
                "evidence_kind": edge_dict.get("kind", "intra"),
                "pipeline": pipeline,
                "sentence_text": edge_dict.get("sentence_text", ""),
                "char_span": [char_span[0], char_span[1]] if char_span else None,
                "join_confidence": join_confidence,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Builder
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


@dataclass(frozen=True)
class GraphCorpusManifest:
    graph_key: str
    doc_key: str
    pipeline: str
    bundle: str
    bundle_version: str
    nodes_count: int
    edges_count: int
    embedding_model: str
    join_confidence_breakdown: dict[str, int]


def build_graph_index(
    loc: DocLocator,
    *,
    pipeline: str,
    bundle: str,
    bundle_version: str,
    kg_dict: dict[str, Any],
    chunks: list[Chunk],
    plaintext: str,
    force: bool = False,
    progress: Optional[Callable[[str, Optional[int], Optional[int]], None]] = None,
) -> GraphCorpusManifest:
    """Build (or load-from-cache) the ``_graph/{graph_key}/`` artifacts.

    ``kg_dict`` is the in-memory KG (typically from demo_compare's
    ``_kg_cache``); shape: ``{"entities": [...], "relationships": [...]}``.
    ``chunks`` come from the per-doc ``_doc/`` artifact (caller threads
    them in to avoid double-loading).

    Resolves every entity / edge evidence's ``sentence_text`` to an
    absolute ``char_span`` via the global resolver in
    ``kgspin_interface.text.normalize`` (D6 — chunking-scheme-independent).
    """
    from kgspin_interface.text.normalize import (
        ChunkSpan,
        resolve_evidence_offsets,
    )

    lander_manifest = read_lander_manifest(loc)
    if lander_manifest is None:
        raise FileNotFoundError(
            f"Lander manifest missing at {loc.manifest_path}."
        )

    doc_key = compute_doc_key(
        domain=lander_manifest.domain,
        source=lander_manifest.source,
        identifier=loc.identifier,
        source_sha=lander_manifest.raw_sha,
        normalization_version=lander_manifest.normalization_version,
    )
    core_sha = kgspin_core_sha()
    graph_key = compute_graph_key(
        doc_key=doc_key,
        pipeline=pipeline,
        bundle=bundle,
        bundle_version=bundle_version,
        kgspin_core_sha=core_sha,
    )
    out_dir = loc.graph_corpus_dir(pipeline=pipeline, bundle=bundle, core_sha=core_sha)
    manifest_path = out_dir / "manifest.json"

    if not force and manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("graph_key") == graph_key:
                logger.info(
                    "[GRAPH_INDEX] %s/%s: cache hit (%s)",
                    loc.identifier, pipeline, graph_key[:12],
                )
                return _gmanifest_from_dict(existing)
        except (OSError, json.JSONDecodeError):
            pass

    out_dir.mkdir(parents=True, exist_ok=True)

    # Convert chunks to ChunkSpan for the resolver (only fields it needs).
    chunk_spans = [
        ChunkSpan(
            text=c.text,
            char_offset_start=c.char_offset_start,
            char_offset_end=c.char_offset_end,
        )
        for c in chunks
    ]

    # Walk entities + relationships, resolving offsets.
    if progress:
        progress("resolving_offsets", 0, len(kg_dict.get("entities", [])) + len(kg_dict.get("relationships", [])))

    join_breakdown = {"sentence": 0, "chunk": 0, "none": 0}

    raw_nodes = kg_dict.get("entities", []) or []
    nodes_payload = []
    for n in raw_nodes:
        ev = n.get("evidence") or {}
        # Evidence may be a dict (from to_dict()) or a dataclass — handle both.
        sentence = (
            ev.get("sentence_text")
            if isinstance(ev, dict)
            else getattr(ev, "sentence_text", "")
        ) or ""
        span, conf = resolve_evidence_offsets(plaintext, chunk_spans, sentence)
        join_breakdown[conf] = join_breakdown.get(conf, 0) + 1
        nodes_payload.append(
            _node_to_row(
                {
                    "id": n.get("id", ""),
                    "text": n.get("text", ""),
                    "entity_type": n.get("entity_type", "UNKNOWN"),
                    "semantic_definition": n.get("semantic_definition") or "",
                    "kind": n.get("kind", "intra"),
                    "sentence_text": sentence,
                },
                pipeline=pipeline,
                char_span=span,
                join_confidence=conf,
            )
        )

    raw_edges = kg_dict.get("relationships", []) or []
    edges_payload = []
    for e in raw_edges:
        ev = e.get("evidence") or {}
        sentence = (
            ev.get("sentence_text")
            if isinstance(ev, dict)
            else getattr(ev, "sentence_text", "")
        ) or ""
        span, conf = resolve_evidence_offsets(plaintext, chunk_spans, sentence)
        join_breakdown[conf] = join_breakdown.get(conf, 0) + 1
        subj = e.get("subject") or {}
        obj = e.get("object") or {}
        edges_payload.append(
            _edge_to_row(
                {
                    "id": e.get("id", ""),
                    "src": subj.get("id", "") if isinstance(subj, dict) else "",
                    "tgt": obj.get("id", "") if isinstance(obj, dict) else "",
                    "predicate": e.get("predicate", ""),
                    "kind": e.get("kind", "intra"),
                    "sentence_text": sentence,
                },
                pipeline=pipeline,
                char_span=span,
                join_confidence=conf,
            )
        )

    if progress:
        progress("embedding_nodes", 0, len(nodes_payload))
    node_texts = [_node_text_for_index(n) for n in nodes_payload]
    node_embeddings = (
        _embed_texts(node_texts)
        if node_texts
        else np.zeros((0, 384), dtype=np.float32)
    )
    if progress:
        progress("embedding_edges", 0, len(edges_payload))
    edge_texts = [_edge_text_for_index(e) for e in edges_payload]
    edge_embeddings = (
        _embed_texts(edge_texts)
        if edge_texts
        else np.zeros((0, 384), dtype=np.float32)
    )

    (out_dir / "graph_nodes.json").write_text(
        json.dumps(nodes_payload, indent=2), encoding="utf-8"
    )
    (out_dir / "graph_edges.json").write_text(
        json.dumps(edges_payload, indent=2), encoding="utf-8"
    )
    np.save(out_dir / "graph_node_embeddings.npy", node_embeddings)
    np.save(out_dir / "graph_edge_embeddings.npy", edge_embeddings)

    manifest = {
        "graph_key": graph_key,
        "doc_key": doc_key,
        "pipeline": pipeline,
        "bundle": bundle,
        "bundle_version": bundle_version,
        "nodes_count": len(nodes_payload),
        "edges_count": len(edges_payload),
        "embedding_model": EMBED_MODEL_NAME,
        "join_confidence_breakdown": join_breakdown,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if progress:
        progress("done", len(nodes_payload) + len(edges_payload),
                 len(nodes_payload) + len(edges_payload))

    # D6 telemetry counter (followup #8).
    logger.info(
        "graphrag.build.join_confidence_breakdown",
        extra={
            "doc_key": doc_key,
            "graph_key": graph_key,
            "pipeline": pipeline,
            **{f"n_{k}": v for k, v in join_breakdown.items()},
        },
    )
    return _gmanifest_from_dict(manifest)


def _gmanifest_from_dict(d: dict) -> GraphCorpusManifest:
    return GraphCorpusManifest(
        graph_key=d["graph_key"],
        doc_key=d["doc_key"],
        pipeline=d["pipeline"],
        bundle=d["bundle"],
        bundle_version=d["bundle_version"],
        nodes_count=d["nodes_count"],
        edges_count=d["edges_count"],
        embedding_model=d["embedding_model"],
        join_confidence_breakdown=d.get("join_confidence_breakdown", {}),
    )
