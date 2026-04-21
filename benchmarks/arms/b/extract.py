"""Arm B — LLM-extracted graph (Gemini, per-chunk triple extraction).

Pipeline:

1. Chunk each corpus document into ~1,500 character windows (honouring
   paragraph boundaries). Chunks land in ``graph.chunks`` so downstream
   retrieval strategies can operate without re-tokenizing the corpus.
2. For each chunk, prompt the configured LLM (``gemini_hard_limit``
   alias by default, per ADR-002) to emit a JSON list of
   ``(subject, subject_type, predicate, object, object_type, evidence)``
   records. Failures degrade gracefully: one bad chunk does not kill
   the run.
3. Canonicalize entity surface forms across chunks. Naive-but-reasonable
   resolution: lowercase + whitespace-strip for exact matches, Jaccard
   on token sets for near-matches above a threshold. Embedding-based
   merge is the documented next step but kept out of sprint 20 to stay
   inside the 3–5 day budget floor.
4. Emit ``graph-v0`` JSON.

ADR-002 §7 contract: every public entry point accepts
``llm_alias`` / ``llm_provider`` / ``llm_model`` per-call kwargs.

Mock-mode (``mock_llm=True`` or ``--mock-llm``) swaps the LLM call for a
deterministic stub so the thin-slice smoke can exercise end-to-end
plumbing without an API round-trip.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Paragraph-aware chunking target. ~1500 chars ≈ 300 tokens which
# keeps Gemini extraction prompts well under the per-call ceiling.
CHUNK_TARGET_CHARS = 1500
CHUNK_OVERLAP_CHARS = 150

JACCARD_MERGE_THRESHOLD = 0.85


# --- Data types -------------------------------------------------------------


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    span_start: int
    span_end: int

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class RawTriple:
    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    evidence_text: str
    chunk_id: str


@dataclass
class Node:
    node_id: str
    surface_form: str
    node_type: str
    aliases: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.9

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Edge:
    edge_id: str
    subject: str
    predicate: str
    object: str
    provenance: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.9

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# --- Chunking ---------------------------------------------------------------


_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def chunk_document(doc_id: str, text: str) -> list[Chunk]:
    """Chunk ``text`` into overlapping windows with paragraph alignment."""
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    chunks: list[Chunk] = []

    buf: list[str] = []
    buf_len = 0
    cursor = 0
    for para in paragraphs:
        if buf and buf_len + len(para) + 2 > CHUNK_TARGET_CHARS:
            body = "\n\n".join(buf)
            start = cursor
            end = cursor + len(body)
            chunks.append(Chunk(
                chunk_id=_chunk_id(doc_id, start, body),
                doc_id=doc_id,
                text=body,
                span_start=start,
                span_end=end,
            ))
            # overlap: keep tail of last paragraph
            overlap = body[-CHUNK_OVERLAP_CHARS:] if len(body) > CHUNK_OVERLAP_CHARS else body
            cursor = end - len(overlap)
            buf = [overlap] if overlap else []
            buf_len = len(overlap)
        buf.append(para)
        buf_len += len(para) + 2

    if buf:
        body = "\n\n".join(buf)
        start = cursor
        end = cursor + len(body)
        chunks.append(Chunk(
            chunk_id=_chunk_id(doc_id, start, body),
            doc_id=doc_id,
            text=body,
            span_start=start,
            span_end=end,
        ))
    return chunks


def _chunk_id(doc_id: str, start: int, body: str) -> str:
    short = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
    return f"{doc_id}__{start:07d}__{short}"


# --- LLM triple extraction --------------------------------------------------


EXTRACTION_SYSTEM = """You extract knowledge-graph triples from 10-K filings.
Return a JSON array. Each element must have:
  subject, subject_type, predicate, object, object_type, evidence_text.
Types: ORGANIZATION, PERSON, LOCATION, METRIC, SEGMENT, PRODUCT, RISK, EVENT.
Predicates use snake_case verbs (e.g. operates_in, acquired, reports_revenue).
evidence_text must be a verbatim span from the provided chunk.
Reply with only the JSON array, no prose."""

EXTRACTION_USER_TEMPLATE = """Chunk ({doc_id}):
```
{text}
```

Return the JSON array now."""


class LLMTripleExtractor:
    """Per-chunk extraction wrapper. Thin so tests can stub ``_complete``."""

    def __init__(
        self,
        *,
        llm_alias: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        mock_llm: bool = False,
    ) -> None:
        self.llm_alias = llm_alias
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.mock_llm = mock_llm
        self._backend = None

    def _get_backend(self):
        if self._backend is None:
            # Lazy import so extract.py stays importable in CI without
            # the demo's full dependency stack installed.
            from kgspin_demo_app.llm_backend import resolve_llm_backend
            alias = self.llm_alias or "gemini_hard_limit"
            self._backend = resolve_llm_backend(
                llm_alias=None if (self.llm_provider or self.llm_model) else alias,
                llm_provider=self.llm_provider,
                llm_model=self.llm_model,
            )
        return self._backend

    def extract(self, chunk: Chunk) -> list[RawTriple]:
        if self.mock_llm:
            return _mock_triples(chunk)
        prompt = EXTRACTION_USER_TEMPLATE.format(
            doc_id=chunk.doc_id, text=chunk.text[:CHUNK_TARGET_CHARS * 2]
        )
        try:
            result = self._get_backend().complete(
                prompt=prompt,
                system_prompt=EXTRACTION_SYSTEM,
                max_tokens=1024,
                temperature=0.0,
            )
            payload = _strip_codefence(result.text)
            raw = json.loads(payload)
        except Exception as e:  # noqa: BLE001 — one bad chunk shouldn't kill the run
            logger.warning(
                "[ARM-B] extraction failed for %s: %s: %s",
                chunk.chunk_id, type(e).__name__, str(e)[:120],
            )
            return []
        return list(_parse_llm_triples(raw, chunk))


def _strip_codefence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _parse_llm_triples(raw: Any, chunk: Chunk) -> Iterable[RawTriple]:
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            yield RawTriple(
                subject=str(item["subject"]).strip(),
                subject_type=str(item.get("subject_type", "ENTITY")).strip().upper(),
                predicate=str(item["predicate"]).strip().lower().replace(" ", "_"),
                object=str(item["object"]).strip(),
                object_type=str(item.get("object_type", "ENTITY")).strip().upper(),
                evidence_text=str(item.get("evidence_text", ""))[:500],
                chunk_id=chunk.chunk_id,
            )
        except (KeyError, TypeError):
            continue


def _mock_triples(chunk: Chunk) -> list[RawTriple]:
    """Deterministic stub for thin-slice smoke tests.

    Emits two canned triples per chunk so retrieval + scoring paths
    exercise end-to-end plumbing without a real LLM round-trip.
    """
    return [
        RawTriple(
            subject=f"mock-entity-{chunk.doc_id}",
            subject_type="ORGANIZATION",
            predicate="appears_in",
            object=chunk.doc_id,
            object_type="DOCUMENT",
            evidence_text=chunk.text[:120],
            chunk_id=chunk.chunk_id,
        ),
        RawTriple(
            subject=chunk.doc_id,
            subject_type="DOCUMENT",
            predicate="has_chunk",
            object=chunk.chunk_id,
            object_type="CHUNK",
            evidence_text=chunk.text[:120],
            chunk_id=chunk.chunk_id,
        ),
    ]


# --- Entity resolution ------------------------------------------------------


def _normalize(surface: str) -> str:
    return re.sub(r"\s+", " ", surface.strip().lower())


def _jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def canonicalize(triples: list[RawTriple]) -> tuple[list[Node], list[Edge]]:
    """Collapse surface-form variants into shared node_ids.

    Strategy: one bucket per (node_type, normalized-surface). For each
    incoming mention, check existing buckets of the same type for a
    Jaccard-on-tokens match above ``JACCARD_MERGE_THRESHOLD``; if any,
    merge into that bucket. Otherwise open a new bucket.
    """
    buckets: dict[tuple[str, str], Node] = {}
    surface_index: dict[str, list[tuple[str, str]]] = {}

    def resolve(surface: str, node_type: str, chunk_id: str) -> str:
        norm = _normalize(surface)
        key = (node_type, norm)
        if key in buckets:
            node = buckets[key]
            if surface not in node.aliases and surface != node.surface_form:
                node.aliases.append(surface)
        else:
            # Try jaccard against existing buckets of same type.
            candidates = surface_index.get(node_type, [])
            best_key = None
            best_score = JACCARD_MERGE_THRESHOLD
            for k in candidates:
                score = _jaccard(k[1], norm)
                if score > best_score:
                    best_score = score
                    best_key = k
            if best_key:
                node = buckets[best_key]
                if surface not in node.aliases and surface != node.surface_form:
                    node.aliases.append(surface)
                key = best_key
            else:
                nid = f"n::{node_type.lower()}::{hashlib.sha1(f'{node_type}:{norm}'.encode()).hexdigest()[:12]}"
                node = Node(node_id=nid, surface_form=surface, node_type=node_type)
                buckets[key] = node
                surface_index.setdefault(node_type, []).append(key)
        node.provenance.append({
            "chunk_id": chunk_id,
            "surface_form": surface,
        })
        return buckets[key].node_id

    edges: list[Edge] = []
    for t in triples:
        s_id = resolve(t.subject, t.subject_type, t.chunk_id)
        o_id = resolve(t.object, t.object_type, t.chunk_id)
        eid = f"e::{hashlib.sha1(f'{s_id}|{t.predicate}|{o_id}|{t.chunk_id}'.encode()).hexdigest()[:14]}"
        edges.append(Edge(
            edge_id=eid,
            subject=s_id,
            predicate=t.predicate,
            object=o_id,
            provenance=[{"chunk_id": t.chunk_id, "evidence_text": t.evidence_text}],
        ))

    return list(buckets.values()), edges


# --- Orchestration ----------------------------------------------------------


def build_graph(
    corpus: dict[str, str],
    *,
    corpus_id: str,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    mock_llm: bool = False,
) -> dict[str, Any]:
    """Run chunking + per-chunk extraction + resolution → graph-v0 dict.

    Args:
        corpus: Mapping of ``doc_id -> full document text``.
        corpus_id: Matches ``benchmarks/corpus/manifest.yaml :: corpus_id``.
        llm_alias: ADR-002 alias for the extractor; defaults to ``gemini_hard_limit``.
        llm_provider: Direct-mode escape hatch (requires ``llm_model``).
        llm_model: Direct-mode model id.
        mock_llm: If True, use the deterministic stub (thin-slice smoke).
    """
    extractor = LLMTripleExtractor(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        mock_llm=mock_llm,
    )
    all_chunks: list[Chunk] = []
    all_triples: list[RawTriple] = []
    started = time.time()
    for doc_id, text in corpus.items():
        doc_chunks = chunk_document(doc_id, text)
        all_chunks.extend(doc_chunks)
        for ch in doc_chunks:
            all_triples.extend(extractor.extract(ch))
    nodes, edges = canonicalize(all_triples)
    elapsed = time.time() - started

    logger.info(
        "[ARM-B] extracted %d triples from %d chunks across %d docs in %.1fs",
        len(all_triples), len(all_chunks), len(corpus), elapsed,
    )

    return {
        "schema_version": "graph-v0",
        "corpus_id": corpus_id,
        "arm": "b",
        "producer": {
            "name": "benchmarks.arms.b.extract",
            "version": "v0",
            "llm_alias": (llm_alias or ("mock" if mock_llm else "gemini_hard_limit")),
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "config_hash": None,
        },
        "chunks": [c.to_dict() for c in all_chunks],
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
    }


# --- CLI --------------------------------------------------------------------


def _load_corpus_from_manifest(manifest_path: Path, max_docs: int | None = None) -> dict[str, str]:
    """Return ``{doc_id: text}`` from a fixtures dir. Manifest currently
    documents upstream URLs + SHA-256; the fetch script caches text to
    ``benchmarks/corpus/pdfs/<doc_id>.txt`` (post-conversion). Tests use
    small ``benchmarks/corpus/fixtures/`` instead.
    """
    import yaml
    with manifest_path.open() as f:
        manifest = yaml.safe_load(f)
    corpus: dict[str, str] = {}
    base = manifest_path.parent
    pdfs = base / "pdfs"
    fixtures = base / "fixtures" / "thin_slice"
    for idx, doc in enumerate(manifest.get("documents", []) or []):
        if max_docs is not None and idx >= max_docs:
            break
        doc_id = doc["doc_name"]
        # Prefer fetched + text-extracted, fall back to the tiny fixture.
        txt = pdfs / f"{doc_id}.txt"
        if txt.is_file():
            corpus[doc_id] = txt.read_text(encoding="utf-8", errors="ignore")
            continue
        fx = fixtures / f"{doc_id}.txt"
        if fx.is_file():
            corpus[doc_id] = fx.read_text(encoding="utf-8", errors="ignore")
    return corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arm B — per-chunk LLM triple extraction")
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/corpus/manifest.yaml"))
    parser.add_argument("--output", type=Path, required=True, help="Where to write graph-v0 JSON.")
    parser.add_argument("--max-docs", type=int, default=None, help="Cap for budget-controlled scale tests.")
    parser.add_argument("--llm-alias", default=None)
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--mock-llm", action="store_true", help="Deterministic stub for plumbing smoke tests.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    corpus = _load_corpus_from_manifest(args.manifest, max_docs=args.max_docs)
    if not corpus:
        logger.error("No corpus docs resolvable from manifest %s. Run benchmarks/corpus/fetch.py first.", args.manifest)
        return 3

    import yaml
    manifest = yaml.safe_load(args.manifest.read_text())

    graph = build_graph(
        corpus=corpus,
        corpus_id=manifest.get("corpus_id", "unknown"),
        llm_alias=args.llm_alias,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        mock_llm=args.mock_llm,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(graph, indent=2))
    logger.info("[ARM-B] wrote %s (%d nodes, %d edges)", args.output, len(graph["nodes"]), len(graph["edges"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
