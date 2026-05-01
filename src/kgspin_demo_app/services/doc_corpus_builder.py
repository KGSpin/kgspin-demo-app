"""Per-source-doc dense corpus builder (PRD-004 v5 Phase 5B / D4).

Produces ``_doc/`` next to the lander's ``source.txt``:

    chunks.json, chunk_embeddings.npy, bm25_index.pkl, manifest.json

Chunking + embedding + BM25 are LLM-independent — same chunks /
embeddings / BM25 index serve every pipeline that touches the same
source doc. Idempotent via the per-doc manifest fingerprint
(``doc_key``).

The implementation deliberately mirrors ``scripts/build_rag_corpus.py``'s
chunking / embedding logic so the byte layout is identical to what
existing ``dense_rag`` consumers expect — the difference is WHERE on
disk the artifacts land, and that the cache key is content-fingerprinted
rather than ticker-keyed.
"""
from __future__ import annotations

import json
import logging
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from kgspin_demo_app.services.cache_layout import (
    DocLocator,
    LanderManifest,
    compute_doc_key,
    read_lander_manifest,
)

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
CHUNK_WINDOW_TOKENS = 256
CHUNK_OVERLAP_TOKENS = 32

WHITESPACE_RE = re.compile(r"\S+")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    char_offset_start: int
    char_offset_end: int
    source_section: Optional[str] = None


def chunk_text(plaintext: str, identifier: str) -> list[Chunk]:
    """Sliding-window chunker — 256-token whitespace window, 32-token overlap."""
    matches = list(WHITESPACE_RE.finditer(plaintext))
    if not matches:
        return []
    chunks: list[Chunk] = []
    step = CHUNK_WINDOW_TOKENS - CHUNK_OVERLAP_TOKENS
    i = 0
    chunk_idx = 0
    while i < len(matches):
        window = matches[i : i + CHUNK_WINDOW_TOKENS]
        if not window:
            break
        start = window[0].start()
        end = window[-1].end()
        chunks.append(
            Chunk(
                chunk_id=f"{identifier}-c{chunk_idx:05d}",
                text=plaintext[start:end],
                char_offset_start=start,
                char_offset_end=end,
            )
        )
        chunk_idx += 1
        i += step
    return chunks


# ---------------------------------------------------------------------------
# Embedder + BM25 (process-wide singletons via thin wrappers)
# ---------------------------------------------------------------------------


def _get_embedder():
    """Delegate to ``dense_rag``'s embedder singleton.

    Tests inject via ``dense_rag.set_embedder(fake)``; production
    lazy-loads ``all-MiniLM-L6-v2`` once. Sharing avoids dual model
    loads + lets test fixtures inject through one entry point.
    """
    from kgspin_demo_app.services import dense_rag
    return dense_rag._get_embedder()


def set_embedder(embedder) -> None:
    """Inject an embedder (FakeEmbedder for tests).

    Routes through ``dense_rag.set_embedder`` so both modules share.
    """
    from kgspin_demo_app.services import dense_rag
    dense_rag.set_embedder(embedder)


def _embed_texts(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    embedder = _get_embedder()
    arr = np.asarray(embedder.encode(texts, convert_to_numpy=True), dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] != len(texts):
        raise ValueError(f"Embedder returned unexpected shape {arr.shape}")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def _bm25_tokenize(text: str) -> list[str]:
    return [t.lower() for t in WHITESPACE_RE.findall(text)]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocCorpusManifest:
    doc_key: str
    chunks_count: int
    embedding_model: str
    chunk_window_tokens: int
    chunk_overlap_tokens: int
    plaintext_sha: str
    normalization_version: str


def build_doc_corpus(
    loc: DocLocator,
    *,
    force: bool = False,
    progress: Optional[Callable[[str, Optional[int], Optional[int]], None]] = None,
) -> DocCorpusManifest:
    """Build (or load-from-cache) the ``_doc/`` artifacts for ``loc``.

    Idempotent: if the existing manifest's ``doc_key`` matches the
    fingerprint computed from the lander's manifest, returns the
    existing artifacts without rebuilding. ``force=True`` rebuilds
    regardless.

    ``progress`` is a callback ``(stage, current, total)`` invoked at
    chunking / embedding / indexing milestones. Demo's lazy-build path
    streams these as SSE events to the modal Why-tab UI; tests pass
    ``None``.
    """
    lander_manifest = read_lander_manifest(loc)
    if lander_manifest is None:
        raise FileNotFoundError(
            f"Lander manifest missing at {loc.manifest_path}. "
            f"Re-run the lander to populate source.txt + manifest.json."
        )

    doc_key = compute_doc_key(
        domain=lander_manifest.domain,
        source=lander_manifest.source,
        identifier=loc.identifier,
        source_sha=lander_manifest.raw_sha,
        normalization_version=lander_manifest.normalization_version,
    )
    out_dir = loc.doc_corpus_dir
    manifest_path = out_dir / "manifest.json"

    # Idempotency check.
    if not force and manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                existing.get("doc_key") == doc_key
                and (out_dir / "chunks.json").exists()
                and (out_dir / "chunk_embeddings.npy").exists()
                and (out_dir / "bm25_index.pkl").exists()
            ):
                logger.info("[DOC_CORPUS] %s: cache hit (%s)", loc.identifier, doc_key[:12])
                return _manifest_from_dict(existing)
        except (OSError, json.JSONDecodeError):
            pass  # rebuild

    out_dir.mkdir(parents=True, exist_ok=True)
    plaintext = loc.source_text_path.read_text(encoding="utf-8")

    if progress:
        progress("chunking", None, None)
    chunks = chunk_text(plaintext, loc.identifier)
    logger.info("[DOC_CORPUS] %s: chunked into %d chunks", loc.identifier, len(chunks))

    if progress:
        progress("embedding", 0, len(chunks))
    chunk_texts = [c.text for c in chunks]
    chunk_embeddings = _embed_texts(chunk_texts)
    if progress:
        progress("embedding", len(chunks), len(chunks))

    if progress:
        progress("indexing", None, None)
    from rank_bm25 import BM25Okapi
    bm25_corpus = [_bm25_tokenize(c.text) for c in chunks]
    bm25 = BM25Okapi(bm25_corpus)

    # Persist artifacts.
    chunks_payload = [
        {
            "id": c.chunk_id,
            "text": c.text,
            "char_offset_start": c.char_offset_start,
            "char_offset_end": c.char_offset_end,
            "source_section": c.source_section,
        }
        for c in chunks
    ]
    (out_dir / "chunks.json").write_text(
        json.dumps(chunks_payload, indent=2), encoding="utf-8"
    )
    np.save(out_dir / "chunk_embeddings.npy", chunk_embeddings)
    with (out_dir / "bm25_index.pkl").open("wb") as f:
        pickle.dump(bm25, f)

    manifest = {
        "doc_key": doc_key,
        "chunks_count": len(chunks),
        "embedding_model": EMBED_MODEL_NAME,
        "chunk_window_tokens": CHUNK_WINDOW_TOKENS,
        "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
        "plaintext_sha": lander_manifest.plaintext_sha,
        "normalization_version": lander_manifest.normalization_version,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    if progress:
        progress("done", len(chunks), len(chunks))

    return _manifest_from_dict(manifest)


def _manifest_from_dict(d: dict) -> DocCorpusManifest:
    return DocCorpusManifest(
        doc_key=d["doc_key"],
        chunks_count=d["chunks_count"],
        embedding_model=d["embedding_model"],
        chunk_window_tokens=d["chunk_window_tokens"],
        chunk_overlap_tokens=d["chunk_overlap_tokens"],
        plaintext_sha=d["plaintext_sha"],
        normalization_version=d["normalization_version"],
    )
