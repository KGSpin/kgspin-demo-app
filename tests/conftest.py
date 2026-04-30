"""Shared pytest fixtures for the kgspin-demo test suite."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _configured_demo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Seed a minimal ``config.yaml`` per test + point ``KGSPIN_DEMO_CONFIG`` at it.

    ADR-001 wires ``kgspin_demo_app.config.bootstrap_cli()`` into every CLI ``main()``.
    Without this fixture each test that enters ``main()`` would trip the
    first-run bootstrap and ``SystemExit(1)``. Seeding a tempdir config per test
    also isolates tests from any real ``config.yaml`` the developer may have
    created in the repo root.

    Also unsets any legacy env vars that ``apply_settings_to_env`` may have
    leaked into ``os.environ`` from a previous test — those writes bypass
    monkeypatch and persist across the suite without this reset.
    """
    for env_name in (
        "KGSPIN_CORPUS_ROOT",
        "KGEN_BUNDLES_DIR",
        "KGEN_DEFAULT_BUNDLE",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    # Reset the module-global bridge/warning state so cross-test bleed doesn't
    # hide re-layer bugs or double-warnings.
    from kgspin_demo_app import config as _cfg
    _cfg._bridge_applied.clear()
    _cfg._warned_envs.clear()

    # Sprint 0.5.4: reset the lazy LLM resolver + cached settings so a test
    # that injects a fake resolver doesn't leak into the next test.
    from kgspin_demo_app import llm_backend as _llm
    _llm.reset_resolver_for_tests(None)
    _llm.reset_settings_for_tests(None)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "storage:\n"
        "  corpus_root: \"\"\n"
        "  bundles_dir: \".bundles\"\n"
        "security:\n"
        "  cors_origins:\n"
        "    - \"*\"\n"
        "features:\n"
        "  default_bundle: \"\"\n"
    )
    monkeypatch.setenv("KGSPIN_DEMO_CONFIG", str(config_path))
    return config_path


# ---------------------------------------------------------------------------
# FakeEmbedder — PRD-004 v5 Phase 5A
#
# Unit tests for the RAG corpus builder + dense_rag + graph_rag services
# need an embedder, but loading the real `sentence-transformers`
# `all-MiniLM-L6-v2` model (~80MB) on every `pytest .` is unacceptable.
# `FakeEmbedder` returns deterministic 384-dim float32 vectors derived
# from a SHA-256 hash of the input text. Same input → same vector;
# distinct inputs → distinct vectors. Sufficient for round-trip /
# ranking / filter tests.
#
# The integration smoke (`tests/integration/test_phase5a_smoke.py`,
# gated on `KGSPIN_LIVE_LLM=1`) and the actual corpus build
# (`scripts/build_rag_corpus.py`) use the real model.
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic 384-dim embedder for unit tests.

    Mirrors ``sentence_transformers.SentenceTransformer.encode``'s
    output shape (``np.ndarray`` of float32, ``(n, dim)`` for a list
    input, ``(dim,)`` for a single-string input). The vectors are
    L2-normalized so cosine similarity == dot product, matching the
    real model's behavior.
    """

    EMBED_DIM = 384

    def __init__(self, *_args, **_kwargs):
        # Accept the same positional/keyword args as
        # ``SentenceTransformer(model_name, device=...)`` so call sites
        # don't branch on test vs prod construction.
        pass

    def encode(self, texts, batch_size=64, show_progress_bar=False, **_kwargs):
        if isinstance(texts, str):
            return self._embed_one(texts)
        out = np.zeros((len(texts), self.EMBED_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i] = self._embed_one(t)
        return out

    def _embed_one(self, text: str) -> np.ndarray:
        # Stretch a SHA-256 digest to 384 dims via repeated hashes.
        seed = text.encode("utf-8") if isinstance(text, str) else b""
        chunks: list[bytes] = []
        i = 0
        while sum(len(c) for c in chunks) < self.EMBED_DIM * 4:  # 4 bytes per float32
            chunks.append(hashlib.sha256(seed + i.to_bytes(4, "big")).digest())
            i += 1
        raw = b"".join(chunks)[: self.EMBED_DIM * 4]
        # Bytes → uint32 → centered float in [-1, 1] → L2-normalized.
        as_uint = np.frombuffer(raw, dtype=np.uint32)
        vec = as_uint.astype(np.float32) / np.float32(2 ** 32) * 2.0 - 1.0
        norm = float(np.linalg.norm(vec)) or 1.0
        return (vec / norm).astype(np.float32)


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    """Pytest fixture wrapper around :class:`FakeEmbedder`."""
    return FakeEmbedder()


@pytest.fixture
def patch_sentence_transformer(monkeypatch: pytest.MonkeyPatch):
    """Patch ``sentence_transformers.SentenceTransformer`` to ``FakeEmbedder``.

    Use in unit tests that exercise code which constructs the embedder
    by name (the corpus builder + lazy module-level loaders).
    """
    import sentence_transformers
    monkeypatch.setattr(
        sentence_transformers, "SentenceTransformer", FakeEmbedder,
    )
    return FakeEmbedder
