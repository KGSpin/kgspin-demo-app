"""Unit tests for ``services/dense_rag``.

PRD-004 v5 Phase 5A — deliverable B. Builds a tiny synthetic corpus
on the fly using the corpus-builder helpers + FakeEmbedder, then
exercises the BM25+cosine RRF retrieval path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_rag_corpus import build_corpus_for_ticker  # noqa: E402

from kgspin_demo_app.services import dense_rag  # noqa: E402


@pytest.fixture
def synthetic_aapl(tmp_path, monkeypatch, fake_embedder):
    """Stand up a tiny AAPL corpus and point dense_rag at it."""
    # Source HTML.
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    html = """
    <html><body>
    <p>Apple Inc. designs and markets smartphones, personal computers, tablets, wearables.
       Tim Cook is the Chief Executive Officer of Apple Inc.
       Revenue for fiscal 2025 totaled $400 billion.</p>
    <p>The Company faces ongoing antitrust litigation in the European Union over the
       App Store. App Store fees have been a regulatory focus.
       Subsidiaries listed in Exhibit 21 include Apple Operations International (Ireland)
       and Apple Distribution International (Ireland).</p>
    <p>Apple Inc. announced a $90 billion share repurchase program. The Company plans to
       continue investing in research and development. R&D spending grew 8% year over year.</p>
    """ * 3  # repeat so we get multiple chunks even with 256-token windows
    (fin_dir / "raw.html").write_text(html, encoding="utf-8")
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(tmp_path))
    import build_rag_corpus
    monkeypatch.setattr(build_rag_corpus, "KGSPIN_CORPUS_ROOT", tmp_path)

    out_root = tmp_path / "rag-corpus"
    out_root.mkdir()
    monkeypatch.setattr(build_rag_corpus, "CORPUS_OUT_DIR", out_root)

    # Pre-seed graph.json so build_corpus_for_ticker(skip_extraction=True)
    # works (we don't need a real KG for dense_rag tests).
    aapl_out = out_root / "AAPL"
    aapl_out.mkdir()
    (aapl_out / "graph.json").write_text(
        json.dumps({"entities": [], "relationships": []}),
        encoding="utf-8",
    )

    build_corpus_for_ticker("AAPL", embedder=fake_embedder, skip_extraction=True)

    # Point dense_rag at the synthetic root + use FakeEmbedder.
    dense_rag.set_corpus_root(out_root)
    dense_rag.set_embedder(fake_embedder)

    yield "AAPL"

    dense_rag.set_corpus_root(dense_rag._DEFAULT_CORPUS_ROOT)
    dense_rag.set_embedder(None)


def test_search_returns_top_k_chunks(synthetic_aapl):
    chunks = dense_rag.search("AAPL", "who is the CEO of Apple", top_k=3)
    assert 1 <= len(chunks) <= 3
    assert all(isinstance(c, dense_rag.Chunk) for c in chunks)
    # Returned in descending score order.
    scores = [c.score for c in chunks]
    assert scores == sorted(scores, reverse=True)


def test_search_top_k_clamps_to_corpus_size(synthetic_aapl):
    # With a tiny corpus, top_k=999 should not crash; returns up to chunk count.
    chunks = dense_rag.search("AAPL", "Apple", top_k=999)
    assert len(chunks) >= 1
    assert all(isinstance(c, dense_rag.Chunk) for c in chunks)


def test_corpus_not_built_raises():
    dense_rag.set_corpus_root(Path("/nonexistent/path/that/does/not/exist"))
    try:
        with pytest.raises(dense_rag.CorpusNotBuilt):
            dense_rag.search("FOO", "any query", top_k=3)
    finally:
        dense_rag.set_corpus_root(dense_rag._DEFAULT_CORPUS_ROOT)


def test_serialize_chunks_format(synthetic_aapl):
    chunks = dense_rag.search("AAPL", "App Store litigation", top_k=2)
    text = dense_rag.serialize_chunks(chunks)
    assert "[TEXT CHUNKS]" in text
    assert "chunk_id=" in text
    assert "offset " in text


def test_serialize_chunks_empty_returns_empty_string():
    assert dense_rag.serialize_chunks([]) == ""


def test_rrf_fuse_combines_bm25_and_cosine_winners():
    # When BM25 ranks A first and cosine ranks B first, both should appear
    # in the top-3 fused result.
    fused = dense_rag._rrf_fuse(
        bm25_indices=[10, 11, 12],
        cosine_indices=[20, 11, 22],
        rrf_k=60.0,
    )
    fused_idx = [idx for idx, _ in fused]
    # Index 11 appears in both → should rank highest.
    assert fused_idx[0] == 11
    # Indices 10 and 20 should both appear.
    assert 10 in fused_idx[:5]
    assert 20 in fused_idx[:5]


def test_cosine_top_indices_returns_sorted_descending():
    # Build a tiny doc embedding with a clear winner.
    doc_emb = np.array([
        [1.0, 0.0, 0.0],
        [0.5, 0.5, 0.0],
        [0.0, 1.0, 0.0],
    ], dtype=np.float32)
    # L2 normalize rows.
    doc_emb = doc_emb / np.linalg.norm(doc_emb, axis=1, keepdims=True)
    query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    out = dense_rag._cosine_top_indices(query, doc_emb, k=3)
    assert out[0] == 0  # exact match
    assert out[-1] == 2  # orthogonal


def test_search_uses_module_level_corpus_cache(synthetic_aapl, monkeypatch):
    """Two calls hit the cache; only one disk load."""
    call_count = {"n": 0}
    real_load = dense_rag._load_corpus

    def counting_load(ticker):
        call_count["n"] += 1
        return real_load(ticker)

    monkeypatch.setattr(dense_rag, "_load_corpus", counting_load)
    # Clear cache explicitly for isolation.
    with dense_rag._corpus_cache_lock:
        dense_rag._corpus_cache.pop("AAPL", None)
    dense_rag.search("AAPL", "Apple", top_k=2)
    dense_rag.search("AAPL", "Tim Cook", top_k=2)
    assert call_count["n"] == 1
