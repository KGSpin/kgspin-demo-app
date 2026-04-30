"""Unit tests for ``scripts/build_rag_corpus.py``.

PRD-004 v5 Phase 5A — corpus builder smoke + idempotency. Uses
``FakeEmbedder`` (from conftest) so ``pytest .`` doesn't require the
real ``sentence-transformers`` model. Extraction is faked via
``skip_extraction=True`` plus a pre-seeded ``graph.json``; the live
extraction path is exercised by the integration smoke (gated on
``KGSPIN_LIVE_LLM=1``).
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from pathlib import Path
from textwrap import dedent

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_rag_corpus import (  # noqa: E402
    build_corpus_for_ticker,
    _chunk_text,
    _bm25_tokenize,
    _manifest_fingerprint,
    _is_idempotent,
    CORPUS_OUT_DIR,
)


@pytest.fixture
def synthetic_corpus_root(tmp_path, monkeypatch):
    """Stand up a fake ``KGSPIN_CORPUS_ROOT`` with an AAPL 10-K HTML."""
    fin_dir = tmp_path / "financial" / "sec_edgar" / "AAPL" / "2026-04-25" / "10-K"
    fin_dir.mkdir(parents=True)
    (fin_dir / "raw.html").write_text(
        dedent("""
        <html><body>
        <h1>Apple Inc. — Form 10-K (synthetic for tests)</h1>
        <p>Apple Inc. is a technology company. Tim Cook serves as Chief Executive Officer.</p>
        <p>The Company designs, manufactures, and markets smartphones, personal computers,
           tablets, wearables, and accessories. Revenue for fiscal year 2025 was $400 billion.</p>
        <p>The Company faces ongoing litigation in California and the European Union related
           to App Store antitrust complaints.</p>
        <p>Apple Inc. has a wholly-owned subsidiary, Apple Operations International,
           organized in Ireland. Another subsidiary, Apple Distribution International,
           also operates from Ireland.</p>
        <p>Item 11 (Executive Compensation): Tim Cook total compensation was $99 million,
           with stock awards comprising the largest single component at $82 million.</p>
        </body></html>
        """).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(tmp_path))
    # Reload the module to pick up the new env var.
    import build_rag_corpus
    build_rag_corpus.KGSPIN_CORPUS_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def out_root(tmp_path, monkeypatch):
    """Redirect corpus output to a temp dir so we don't pollute tests/fixtures."""
    out_dir = tmp_path / "rag-corpus"
    out_dir.mkdir()
    import build_rag_corpus
    monkeypatch.setattr(build_rag_corpus, "CORPUS_OUT_DIR", out_dir)
    return out_dir


@pytest.fixture
def seed_graph_for_aapl(out_root):
    """Pre-seed a fake fan_out kg_dict so we don't need the real extractor."""
    aapl_dir = out_root / "AAPL"
    aapl_dir.mkdir(parents=True)
    kg = {
        "entities": [
            {
                "id": "ent-aapl",
                "text": "Apple Inc.",
                "entity_type": "ORGANIZATION",
                "confidence": 0.99,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Apple Inc. is a technology company.",
                    "sentence_index": 0,
                    "source_document": "AAPL_10K",
                    "character_span": [0, 30],
                },
                "metadata": {"semantic_definition": "Cupertino-based technology firm"},
            },
            {
                "id": "ent-tim",
                "text": "Tim Cook",
                "entity_type": "PERSON",
                "confidence": 0.95,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Tim Cook serves as Chief Executive Officer.",
                    "sentence_index": 1,
                    "source_document": "AAPL_10K",
                    "character_span": [40, 80],
                },
                "metadata": {},
            },
        ],
        "relationships": [
            {
                "id": "rel-1",
                "subject": {"id": "ent-tim", "text": "Tim Cook", "entity_type": "PERSON"},
                "predicate": "ceo_of",
                "object": {"id": "ent-aapl", "text": "Apple Inc.", "entity_type": "ORGANIZATION"},
                "confidence": 0.95,
                "evidence": {
                    "chunk_id": "AAPL-c00000",
                    "sentence_text": "Tim Cook serves as Chief Executive Officer.",
                    "sentence_index": 1,
                    "source_document": "AAPL_10K",
                    "character_span": [40, 80],
                },
            },
        ],
        "derived_facts": [], "provenance": {}, "rejected_relationships": [],
    }
    (aapl_dir / "graph.json").write_text(json.dumps(kg), encoding="utf-8")
    return aapl_dir


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_chunk_text_smoke():
    text = " ".join(f"word{i}" for i in range(800))
    chunks = _chunk_text(text, "TEST")
    assert len(chunks) >= 3
    assert chunks[0].chunk_id == "TEST-c00000"
    assert all(c.text == text[c.char_offset_start : c.char_offset_end] for c in chunks)
    # Overlap: consecutive chunks share at least overlap_tokens-many words.
    if len(chunks) >= 2:
        assert chunks[0].char_offset_end > chunks[1].char_offset_start


def test_chunk_text_empty():
    assert _chunk_text("", "TEST") == []


def test_bm25_tokenize_lowercases_and_splits_whitespace():
    assert _bm25_tokenize("The Company FACES Litigation.") == [
        "the", "company", "faces", "litigation.",
    ]


# ---------------------------------------------------------------------------
# Build smoke + idempotency (uses FakeEmbedder + pre-seeded graph)
# ---------------------------------------------------------------------------


def test_build_smoke_writes_all_artifacts(
    synthetic_corpus_root, out_root, seed_graph_for_aapl, fake_embedder,
):
    result = build_corpus_for_ticker(
        "AAPL", embedder=fake_embedder, skip_extraction=True,
    )
    assert result["status"] == "built"
    assert result["n_chunks"] >= 1
    assert result["n_nodes"] == 2
    assert result["n_edges"] == 1

    aapl = out_root / "AAPL"
    for fname in (
        "source.txt", "chunks.json", "chunk_embeddings.npy", "bm25_index.pkl",
        "graph.json", "graph_nodes.json", "graph_edges.json",
        "graph_node_embeddings.npy", "graph_edge_embeddings.npy",
        "manifest.json",
    ):
        assert (aapl / fname).exists(), f"missing {fname}"

    # Embedding shapes.
    chunk_emb = np.load(aapl / "chunk_embeddings.npy")
    assert chunk_emb.shape[1] == 384
    assert chunk_emb.dtype == np.float32

    node_emb = np.load(aapl / "graph_node_embeddings.npy")
    assert node_emb.shape == (2, 384)

    edge_emb = np.load(aapl / "graph_edge_embeddings.npy")
    assert edge_emb.shape == (1, 384)

    # BM25 index round-trip.
    with (aapl / "bm25_index.pkl").open("rb") as f:
        bm25 = pickle.load(f)
    assert hasattr(bm25, "get_scores")

    # Graph nodes carry parent_doc_offsets back to source.txt.
    nodes = json.loads((aapl / "graph_nodes.json").read_text())
    apple_node = next(n for n in nodes if "Apple" in n["text"])
    start, end = apple_node["parent_doc_offsets"]
    source = (aapl / "source.txt").read_text()
    assert source[start:end].startswith("Apple Inc.")

    manifest = json.loads((aapl / "manifest.json").read_text())
    assert manifest["pipeline"] == "fan_out"
    assert manifest["embedding_model"].endswith("all-MiniLM-L6-v2")


def test_build_idempotent_no_op(
    synthetic_corpus_root, out_root, seed_graph_for_aapl, fake_embedder,
):
    first = build_corpus_for_ticker(
        "AAPL", embedder=fake_embedder, skip_extraction=True,
    )
    assert first["status"] == "built"
    second = build_corpus_for_ticker(
        "AAPL", embedder=fake_embedder, skip_extraction=True,
    )
    assert second["status"] == "noop"


def test_build_force_rebuilds(
    synthetic_corpus_root, out_root, seed_graph_for_aapl, fake_embedder,
):
    build_corpus_for_ticker("AAPL", embedder=fake_embedder, skip_extraction=True)
    forced = build_corpus_for_ticker(
        "AAPL", embedder=fake_embedder, skip_extraction=True, force=True,
    )
    assert forced["status"] == "built"


def test_manifest_fingerprint_is_deterministic():
    fp1 = _manifest_fingerprint("abc123", "fan_out")
    fp2 = _manifest_fingerprint("abc123", "fan_out")
    assert fp1 == fp2
    fp3 = _manifest_fingerprint("def456", "fan_out")
    assert fp1 != fp3
    fp4 = _manifest_fingerprint("abc123", "discovery_rapid")
    assert fp1 != fp4


def test_is_idempotent_false_when_pipeline_changes(tmp_path):
    fp = {"source_sha": "x", "embedding_model": "y", "chunk_config": {}, "pipeline": "fan_out", "kgspin_core_sha": "z"}
    (tmp_path / "manifest.json").write_text(json.dumps(fp), encoding="utf-8")
    assert _is_idempotent(tmp_path, fp)
    fp_changed = {**fp, "pipeline": "discovery_rapid"}
    assert not _is_idempotent(tmp_path, fp_changed)
