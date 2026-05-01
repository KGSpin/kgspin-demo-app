"""Dense RAG retrieval service — PRD-004 v5 Phase 5A deliverable B.

Implements pure dense + BM25 hybrid retrieval over the per-doc chunks
corpus produced by ``scripts/build_rag_corpus.py``. This is the
"vanilla AI engineer" baseline that Scenario A (left pane) and
Scenario B (left pane, used by ``agentic_dense_rag``) compare against.

Public API:

    from kgspin_demo_app.services.dense_rag import search, serialize_chunks

    chunks = search(ticker="AAPL", query="who is the ceo", top_k=5)
    str_for_prompt = serialize_chunks(chunks)

The hybrid is BM25 top-50 ∪ cosine top-50 fused via Reciprocal Rank
Fusion with ``RRF_K=60`` (kgspin_core.constants.RRF_K). This is the
same RRF heuristic the extraction stack uses for hub-list ranking, so
the demo's retrieval semantics are consistent with the rest of kgspin.

Module-level cache: per-ticker corpus is loaded once and kept in
memory. Embeddings are mmap'd numpy; BM25Okapi is unpickled lazily.
"""
from __future__ import annotations

import logging
import os
import pickle
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Default corpus root. Tests override via :func:`set_corpus_root`.
_DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "tests" / "fixtures" / "rag-corpus"
)

_corpus_root_lock = threading.Lock()
_corpus_root: Path = _DEFAULT_CORPUS_ROOT

# Per-ticker cache: ticker → loaded corpus dict.
_corpus_cache: dict[str, "TickerCorpus"] = {}
_corpus_cache_lock = threading.Lock()

# Embedder cache (lazy-loaded, single instance per process).
_embedder = None
_embedder_lock = threading.Lock()

# RRF top-N pool size before fusion.
_RRF_POOL = 50

WHITESPACE_RE = re.compile(r"\S+")


def _bm25_tokenize(text: str) -> list[str]:
    return [t.lower() for t in WHITESPACE_RE.findall(text)]


# ---------------------------------------------------------------------------
# Public dataclasses + errors
# ---------------------------------------------------------------------------


class CorpusNotBuilt(Exception):
    """Raised when ``search(ticker, ...)`` is called for a ticker whose
    corpus directory does not exist (operator forgot to run
    ``scripts/build_rag_corpus.py``)."""


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    score: float
    source_offset: tuple[int, int]
    source_section: Optional[str]


# ---------------------------------------------------------------------------
# Corpus root + embedder accessors (test hooks)
# ---------------------------------------------------------------------------


def set_corpus_root(path: Path) -> None:
    """Override the corpus root (used by tests + unusual deployments)."""
    global _corpus_root
    with _corpus_root_lock:
        _corpus_root = Path(path)
    # Drop any cached corpora so tests get fresh data.
    with _corpus_cache_lock:
        _corpus_cache.clear()


def get_corpus_root() -> Path:
    with _corpus_root_lock:
        return _corpus_root


def set_embedder(embedder) -> None:
    """Inject a custom embedder (FakeEmbedder for tests)."""
    global _embedder
    with _embedder_lock:
        _embedder = embedder


def _get_embedder():
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer(
                "sentence-transformers/all-MiniLM-L6-v2",
            )
        return _embedder


# ---------------------------------------------------------------------------
# Per-ticker corpus loader
# ---------------------------------------------------------------------------


@dataclass
class TickerCorpus:
    ticker: str
    chunks: list[dict]               # [{id, text, char_offset_start, char_offset_end, source_section}]
    chunk_embeddings: np.ndarray      # (n_chunks, 384) float32, L2-normalized
    bm25: object                      # rank_bm25.BM25Okapi (untyped to keep import lazy)


def _resolve_corpus_dir(ticker: str) -> Path:
    """Resolve the on-disk dir for ``ticker``'s dense corpus.

    Search order (PRD-004 v5 Phase 5B / D4):
    1. Lander tree's ``_doc/`` under the latest dated subdir
       (``~/.kgspin/corpus/{domain}/{source}/{ticker}/{date}/{doc_kind}/_doc/``).
       This is the new canonical location.
    2. Legacy ``tests/fixtures/rag-corpus/{ticker}/`` fallback for
       pre-5B fixtures + unit-test pinned data.
    """
    from kgspin_demo_app.services.cache_layout import resolve_locator

    loc = resolve_locator(ticker)
    if loc is not None and loc.doc_corpus_dir.exists():
        return loc.doc_corpus_dir
    return get_corpus_root() / ticker


def _load_corpus(ticker: str) -> TickerCorpus:
    import json
    out_dir = _resolve_corpus_dir(ticker)
    chunks_path = out_dir / "chunks.json"
    emb_path = out_dir / "chunk_embeddings.npy"
    bm25_path = out_dir / "bm25_index.pkl"
    for p in (chunks_path, emb_path, bm25_path):
        if not p.exists():
            raise CorpusNotBuilt(
                f"RAG corpus for {ticker!r} missing: {p} not found. "
                f"Run `python -m scripts.warm_caches --ticker {ticker}` first."
            )
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    # mmap'd numpy load — the rank_bm25 corpus dwarfs the embeddings, so
    # mmap'ing the embeddings only is the worthwhile half.
    chunk_emb = np.load(emb_path, mmap_mode="r")
    with bm25_path.open("rb") as f:
        bm25 = pickle.load(f)
    return TickerCorpus(
        ticker=ticker, chunks=chunks, chunk_embeddings=chunk_emb, bm25=bm25,
    )


def get_corpus(ticker: str) -> TickerCorpus:
    with _corpus_cache_lock:
        if ticker not in _corpus_cache:
            _corpus_cache[ticker] = _load_corpus(ticker)
        return _corpus_cache[ticker]


# ---------------------------------------------------------------------------
# Hybrid retrieval
# ---------------------------------------------------------------------------


def _rrf_score(rank_in_list: int, rrf_k: float) -> float:
    """Reciprocal Rank Fusion: 1 / (k + rank). 0-indexed rank in."""
    return 1.0 / (rrf_k + rank_in_list + 1)


def _cosine_top_indices(query_emb: np.ndarray, doc_emb: np.ndarray, k: int) -> np.ndarray:
    """Top-k indices by cosine == dot product (rows are L2-normalized)."""
    if doc_emb.shape[0] == 0:
        return np.array([], dtype=np.int64)
    sims = doc_emb @ query_emb
    k = min(k, doc_emb.shape[0])
    # argpartition for top-k, then sort descending.
    idx = np.argpartition(-sims, k - 1)[:k]
    return idx[np.argsort(-sims[idx])]


def _rrf_fuse(
    bm25_indices: list[int], cosine_indices: list[int], rrf_k: float,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion across two ranked lists. Returns
    (chunk_idx, fused_score) tuples sorted by descending score."""
    fused: dict[int, float] = {}
    for rank, idx in enumerate(bm25_indices):
        fused[idx] = fused.get(idx, 0.0) + _rrf_score(rank, rrf_k)
    for rank, idx in enumerate(cosine_indices):
        fused[idx] = fused.get(idx, 0.0) + _rrf_score(rank, rrf_k)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec)) or 1.0
    return (vec / n).astype(np.float32)


def search(
    ticker: str,
    query: str,
    top_k: int = 5,
    *,
    rrf_k: Optional[float] = None,
) -> list[Chunk]:
    """BM25 + cosine RRF retrieval over the per-doc corpus.

    Parameters
    ----------
    ticker : str
        Ticker symbol used as the corpus directory name.
    query : str
        User query (free text).
    top_k : int
        Number of chunks to return.
    rrf_k : float, optional
        Reciprocal Rank Fusion constant. Defaults to
        ``kgspin_core.constants.RRF_K`` (60.0). Test hook.
    """
    if rrf_k is None:
        try:
            from kgspin_core.constants import RRF_K as _RRF_K
            rrf_k = float(_RRF_K)
        except ImportError:
            rrf_k = 60.0

    corpus = get_corpus(ticker)
    if not corpus.chunks:
        return []

    # Cosine.
    embedder = _get_embedder()
    q_emb = embedder.encode(query) if isinstance(query, str) else embedder.encode([query])[0]
    q_emb = _l2_normalize(np.asarray(q_emb, dtype=np.float32))
    cos_idx = _cosine_top_indices(q_emb, corpus.chunk_embeddings, _RRF_POOL).tolist()

    # BM25.
    bm25_scores = corpus.bm25.get_scores(_bm25_tokenize(query))
    pool = min(_RRF_POOL, len(bm25_scores))
    bm25_idx = np.argpartition(-bm25_scores, pool - 1)[:pool]
    bm25_idx = bm25_idx[np.argsort(-bm25_scores[bm25_idx])].tolist()

    fused = _rrf_fuse(bm25_idx, cos_idx, rrf_k)[:top_k]

    out: list[Chunk] = []
    for idx, score in fused:
        chunk = corpus.chunks[idx]
        out.append(Chunk(
            chunk_id=chunk.get("id", f"{ticker}-c{idx:05d}"),
            text=chunk.get("text", ""),
            score=float(score),
            source_offset=(
                int(chunk.get("char_offset_start", 0)),
                int(chunk.get("char_offset_end", 0)),
            ),
            source_section=chunk.get("source_section"),
        ))
    return out


def serialize_chunks(chunks: list[Chunk]) -> str:
    """Format chunks for inclusion in an LLM prompt.

    Matches the [TEXT CHUNKS] section of the bundle→string contract
    (see ``services/graph_rag.serialize_bundle_for_prompt``). When
    ``chunks`` is empty, returns an empty string so the prompt stays
    well-formed.
    """
    if not chunks:
        return ""
    lines = ["[TEXT CHUNKS]"]
    for c in chunks:
        section = f", section={c.source_section}" if c.source_section else ""
        lines.append(
            f"chunk_id={c.chunk_id} (offset {c.source_offset[0]}-{c.source_offset[1]}{section}):"
        )
        lines.append(c.text)
        lines.append("---")
    # Drop trailing separator.
    if lines[-1] == "---":
        lines.pop()
    return "\n".join(lines)


__all__ = [
    "Chunk",
    "CorpusNotBuilt",
    "search",
    "serialize_chunks",
    "set_corpus_root",
    "set_embedder",
    "get_corpus",
]
