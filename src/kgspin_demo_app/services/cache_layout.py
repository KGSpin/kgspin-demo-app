"""Joinable two-store cache layout (PRD-004 v5 Phase 5B).

Resolves on-disk paths for the per-doc dense corpus (``_doc/``) and the
per-(doc, pipeline, bundle) graph index (``_graph/{graph_key}/``) under
the lander's existing per-(ticker, date, doc_kind) subdir tree.

Layout (canonical example, financial domain):

    ~/.kgspin/corpus/financial/sec_edgar/JNJ/2025-02-13/10-K/
    ├── raw.html              # lander
    ├── source.txt            # lander (D2)
    ├── manifest.json         # lander (D2)
    ├── _doc/                 # this module
    │   ├── chunks.json
    │   ├── chunk_embeddings.npy
    │   ├── bm25_index.pkl
    │   └── manifest.json
    └── _graph/
        ├── fan_out__financial-default__core-abc/
        │   ├── graph_nodes.json
        │   ├── graph_edges.json
        │   ├── graph_node_embeddings.npy
        │   ├── graph_edge_embeddings.npy
        │   └── manifest.json
        └── ...

Clinical mirrors under ``~/.kgspin/corpus/clinical/clinicaltrials_gov/{NCT}/{date}/trial/``.

Path resolution falls back to the legacy ``tests/fixtures/rag-corpus/{ticker}/``
fixture layout when the lander tree doesn't exist (so unit tests can pin
small fixtures and existing fan_out fixtures stay readable).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_DEFAULT_KGSPIN_CORPUS_ROOT = Path.home() / ".kgspin" / "corpus"


def _kgspin_corpus_root() -> Path:
    return Path(os.environ.get("KGSPIN_CORPUS_ROOT") or _DEFAULT_KGSPIN_CORPUS_ROOT)


# ---------------------------------------------------------------------------
# Domain / source / doc_kind resolution from a ticker
# ---------------------------------------------------------------------------


# Tickers that map to clinical trials (lookup mirrors build_rag_corpus).
_CLINICAL_TICKER_NCT = {"JNJ-Stelara": "NCT00174785"}


@dataclass(frozen=True)
class DocLocator:
    """Where on disk a (domain, source, identifier, doc_kind) lives."""

    domain: str            # 'financial' | 'clinical'
    source: str            # 'sec_edgar' | 'clinicaltrials_gov'
    identifier: str        # ticker or NCT id (the identifier the lander uses)
    doc_kind: str          # '10-K' | '10-Q' | '8-K' | 'trial'
    dated_dir: Path        # ~/.kgspin/corpus/.../{ticker}/{date}/{doc_kind}/

    @property
    def raw_path(self) -> Path:
        if self.domain == "clinical":
            return self.dated_dir / "raw.json"
        return self.dated_dir / "raw.html"

    @property
    def source_text_path(self) -> Path:
        return self.dated_dir / "source.txt"

    @property
    def manifest_path(self) -> Path:
        return self.dated_dir / "manifest.json"

    @property
    def doc_corpus_dir(self) -> Path:
        return self.dated_dir / "_doc"

    def graph_corpus_dir(self, *, pipeline: str, bundle: str, core_sha: str) -> Path:
        subdir_name = f"{pipeline}__{bundle}__core-{core_sha[:7]}"
        return self.dated_dir / "_graph" / subdir_name


def resolve_locator(
    identifier: str,
    *,
    domain: Optional[str] = None,
) -> Optional[DocLocator]:
    """Find the latest dated subdir for ``identifier`` in the lander tree.

    Returns ``None`` if no lander directory exists (caller should fall
    back to legacy fixture lookup).

    Resolution rules:
    - If ``identifier`` matches a clinical-ticker mapping (e.g.
      ``JNJ-Stelara`` → ``NCT00174785``), domain is forced to clinical.
    - If ``identifier`` starts with ``NCT``, treated as clinical NCT id directly.
    - Else: financial / sec_edgar / 10-K (default doc_kind for SEC).
    """
    nct = _CLINICAL_TICKER_NCT.get(identifier)
    if nct is not None or identifier.startswith("NCT"):
        nct_id = nct or identifier
        clinical_root = _kgspin_corpus_root() / "clinical" / "clinicaltrials_gov" / nct_id
        if not clinical_root.exists():
            return None
        dated_dirs = sorted(
            (d for d in clinical_root.iterdir() if d.is_dir()), reverse=True,
        )
        if not dated_dirs:
            return None
        dated = dated_dirs[0] / "trial"
        if not dated.exists():
            return None
        return DocLocator(
            domain="clinical",
            source="clinicaltrials_gov",
            identifier=nct_id,
            doc_kind="trial",
            dated_dir=dated,
        )

    # Financial path.
    fin_root = _kgspin_corpus_root() / "financial" / "sec_edgar" / identifier
    if not fin_root.exists():
        return None
    dated_dirs = sorted(
        (d for d in fin_root.iterdir() if d.is_dir()), reverse=True,
    )
    if not dated_dirs:
        return None
    dated = dated_dirs[0] / "10-K"
    if not dated.exists():
        return None
    return DocLocator(
        domain="financial",
        source="sec_edgar",
        identifier=identifier,
        doc_kind="10-K",
        dated_dir=dated,
    )


# ---------------------------------------------------------------------------
# Cache key fingerprints
# ---------------------------------------------------------------------------


def compute_doc_key(
    *,
    domain: str,
    source: str,
    identifier: str,
    source_sha: str,
    normalization_version: str,
) -> str:
    """SHA-256 of (domain, source, identifier, source_sha, normalization_version)."""
    parts = [domain, source, identifier, source_sha, normalization_version]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def compute_graph_key(
    *,
    doc_key: str,
    pipeline: str,
    bundle: str,
    bundle_version: str,
    kgspin_core_sha: str,
) -> str:
    """SHA-256 of (doc_key, pipeline, bundle, bundle_version, kgspin_core_sha)."""
    parts = [doc_key, pipeline, bundle, bundle_version, kgspin_core_sha]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def kgspin_core_sha() -> str:
    """Best-effort kgspin-core revision hash for graph_key composition."""
    try:
        import kgspin_core  # noqa: F401
        version = getattr(__import__("kgspin_core"), "__version__", "")
        if version:
            return hashlib.sha256(version.encode("utf-8")).hexdigest()
    except Exception:
        pass
    return "unknown" * 8  # 56 chars; satisfies [:7] slice for path naming


# ---------------------------------------------------------------------------
# Lander-manifest reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LanderManifest:
    """Parsed view of the lander's manifest.json (D2)."""

    raw_sha: str
    plaintext_sha: str
    plaintext_bytes: int
    normalization_version: str
    domain: str
    source: str
    sponsor: Optional[str] = None


def read_lander_manifest(loc: DocLocator) -> Optional[LanderManifest]:
    """Read the lander's manifest.json; None if missing or malformed."""
    if not loc.manifest_path.exists():
        return None
    try:
        data = json.loads(loc.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return LanderManifest(
        raw_sha=data["raw"]["sha256"],
        plaintext_sha=data["source_text"]["sha256"],
        plaintext_bytes=data["source_text"]["bytes"],
        normalization_version=data["source_text"]["normalization_version"],
        domain=data["domain"],
        source=data["source"],
        sponsor=(data.get("clinical") or {}).get("sponsor"),
    )
