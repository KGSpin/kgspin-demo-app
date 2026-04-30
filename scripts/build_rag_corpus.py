"""PRD-004 v5 Phase 5A — RAG corpus builder.

Builds the per-doc retrieval corpus that the dense_rag, graph_rag, and
graphsearch_pipeline services consume. One ticker per invocation;
``--ticker phase5a`` builds the full Phase-5A target list (AAPL + JNJ +
JNJ-Stelara clinical hedge).

Output (per spec §4 Data Layer):

    tests/fixtures/rag-corpus/
    ├── {ticker}/
    │   ├── source.txt                # plaintext (build artifact)
    │   ├── graph.json                # fan_out kg_dict (build artifact)
    │   ├── chunks.json               # tracked
    │   ├── chunk_embeddings.npy      # build artifact
    │   ├── bm25_index.pkl            # build artifact
    │   ├── graph_nodes.json          # tracked
    │   ├── graph_edges.json          # tracked
    │   ├── graph_node_embeddings.npy # build artifact
    │   ├── graph_edge_embeddings.npy # build artifact
    │   └── manifest.json             # tracked

Idempotent: if the existing manifest matches the incoming fingerprint
(source_sha + embedding_model + chunk_config + pipeline + kgspin_core_sha),
the build is a no-op. ``--force`` regenerates regardless.

CLI:
    python -m scripts.build_rag_corpus --ticker AAPL [--force]
    python -m scripts.build_rag_corpus --ticker phase5a   # AAPL + JNJ + JNJ-Stelara
    python -m scripts.build_rag_corpus --ticker all       # all 7 fin tickers
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_EXTRACTION_DIR = PROJECT_ROOT / "demos" / "extraction"
sys.path.insert(0, str(DEMO_EXTRACTION_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("build_rag_corpus")

CORPUS_OUT_DIR = PROJECT_ROOT / "tests" / "fixtures" / "rag-corpus"

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
CHUNK_WINDOW_TOKENS = 256
CHUNK_OVERLAP_TOKENS = 32

# Approximate-token chunking via whitespace split. Document chunking
# granularity is approximate by design — retrieval ranking dominates the
# end-to-end signal. Avoiding a tiktoken dep keeps the demo deps minimal.
WHITESPACE_RE = re.compile(r"\S+")

PHASE5A_TICKERS = ("AAPL", "JNJ", "JNJ-Stelara")
ALL_FIN_TICKERS = ("AAPL", "AMD", "GOOGL", "JNJ", "MSFT", "NVDA", "UNH")

# Clinical hedge ticker → NCT id mapping (PRD-004 v5 §3 Phase 5A).
CLINICAL_TICKER_NCT = {
    "JNJ-Stelara": "NCT00174785",
}

KGSPIN_CORPUS_ROOT = Path(os.environ.get("KGSPIN_CORPUS_ROOT") or Path.home() / ".kgspin" / "corpus")


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSource:
    ticker: str
    domain: str  # 'financial' | 'clinical'
    raw_path: Path
    raw_kind: str  # 'html' | 'json'
    raw_bytes: bytes
    plaintext: str
    company_name: str


def _strip_html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _strip_clinical_to_text(payload_json: str) -> tuple[str, str]:
    """Convert a ClinicalTrials.gov JSON record into searchable plaintext.
    Returns (plaintext, sponsor_name). Sponsor name is used as the H-module
    main_entity for clinical extraction."""
    data = json.loads(payload_json)
    proto = data.get("protocolSection") or {}
    id_module = proto.get("identificationModule") or {}
    desc_module = proto.get("descriptionModule") or {}
    cond_module = proto.get("conditionsModule") or {}
    arms_module = proto.get("armsInterventionsModule") or {}
    out_module = proto.get("outcomesModule") or {}
    elig_module = proto.get("eligibilityModule") or {}
    spons_module = proto.get("sponsorCollaboratorsModule") or {}

    sponsor = (
        ((spons_module.get("leadSponsor") or {}).get("name"))
        or "Unknown Sponsor"
    )

    parts: list[str] = []
    if (title := id_module.get("officialTitle") or id_module.get("briefTitle")):
        parts.append(f"Title: {title}")
    if (nct_id := id_module.get("nctId")):
        parts.append(f"NCT: {nct_id}")
    parts.append(f"Sponsor: {sponsor}")
    if (summary := desc_module.get("briefSummary")):
        parts.append("Summary:\n" + summary)
    if (detail := desc_module.get("detailedDescription")):
        parts.append("Detailed description:\n" + detail)
    for cond in (cond_module.get("conditions") or []):
        parts.append(f"Condition: {cond}")
    for kw in (cond_module.get("keywords") or []):
        parts.append(f"Keyword: {kw}")
    for arm in (arms_module.get("armGroups") or []):
        parts.append(
            "Arm: "
            + arm.get("label", "")
            + " — "
            + arm.get("description", "")
        )
    for itv in (arms_module.get("interventions") or []):
        parts.append(
            "Intervention: "
            + itv.get("name", "")
            + " ("
            + itv.get("type", "")
            + ") — "
            + itv.get("description", "")
        )
    for outcome in (out_module.get("primaryOutcomes") or []):
        parts.append("Primary outcome: " + outcome.get("measure", ""))
    for outcome in (out_module.get("secondaryOutcomes") or []):
        parts.append("Secondary outcome: " + outcome.get("measure", ""))
    for outcome in (out_module.get("otherOutcomes") or []):
        parts.append("Other outcome: " + outcome.get("measure", ""))
    if (crit := elig_module.get("eligibilityCriteria")):
        parts.append("Eligibility:\n" + crit)
    return "\n\n".join(p for p in parts if p), sponsor


def _resolve_source(ticker: str) -> ResolvedSource:
    """Locate the newest 10-K HTML or NCT JSON for ``ticker``."""
    if ticker in CLINICAL_TICKER_NCT:
        nct = CLINICAL_TICKER_NCT[ticker]
        clinical_root = KGSPIN_CORPUS_ROOT / "clinical" / "clinicaltrials_gov" / nct
        if not clinical_root.exists():
            raise FileNotFoundError(
                f"Clinical corpus for {ticker!r} ({nct}) not found at {clinical_root}. "
                f"Run `kgspin-demo-lander-clinical --nct {nct}` to populate."
            )
        dated_dirs = sorted(
            (d for d in clinical_root.iterdir() if d.is_dir()), reverse=True,
        )
        if not dated_dirs:
            raise FileNotFoundError(f"No dated subdirs under {clinical_root}")
        json_path = dated_dirs[0] / "trial" / "raw.json"
        raw_bytes = json_path.read_bytes()
        plaintext, sponsor = _strip_clinical_to_text(raw_bytes.decode("utf-8"))
        return ResolvedSource(
            ticker=ticker, domain="clinical",
            raw_path=json_path, raw_kind="json",
            raw_bytes=raw_bytes, plaintext=plaintext,
            company_name=sponsor,
        )

    fin_root = KGSPIN_CORPUS_ROOT / "financial" / "sec_edgar" / ticker
    if not fin_root.exists():
        raise FileNotFoundError(
            f"SEC corpus for {ticker!r} not found at {fin_root}. "
            f"Run `kgspin-demo-lander-sec --ticker {ticker}` to populate."
        )
    dated_dirs = sorted(
        (d for d in fin_root.iterdir() if d.is_dir()), reverse=True,
    )
    if not dated_dirs:
        raise FileNotFoundError(f"No dated subdirs under {fin_root}")
    html_path = dated_dirs[0] / "10-K" / "raw.html"
    if not html_path.exists():
        raise FileNotFoundError(f"Expected {html_path} to exist")
    raw_bytes = html_path.read_bytes()
    plaintext = _strip_html_to_text(raw_bytes.decode("utf-8", errors="ignore"))

    # Best-effort canonical-name resolution (KNOWN_TICKERS keeps these
    # for the 7 demo tickers; falls through to ticker echo otherwise).
    try:
        from pipeline_common import KNOWN_TICKERS
        info = KNOWN_TICKERS.get(ticker, {})
        company_name = info.get("name") or ticker
    except Exception:
        company_name = ticker

    return ResolvedSource(
        ticker=ticker, domain="financial",
        raw_path=html_path, raw_kind="html",
        raw_bytes=raw_bytes, plaintext=plaintext,
        company_name=company_name,
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    char_offset_start: int
    char_offset_end: int
    source_section: Optional[str]


def _chunk_text(plaintext: str, ticker: str) -> list[Chunk]:
    """Sliding-window chunker. Token = whitespace span; window = 256 tokens
    with 32-token overlap. Char offsets back-resolve to ``plaintext`` for
    citation rendering."""
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
        text = plaintext[start:end]
        chunks.append(
            Chunk(
                chunk_id=f"{ticker}-c{chunk_idx:05d}",
                text=text,
                char_offset_start=start,
                char_offset_end=end,
                source_section=None,
            )
        )
        chunk_idx += 1
        i += step
    return chunks


# ---------------------------------------------------------------------------
# Manifest fingerprint
# ---------------------------------------------------------------------------


def _kgspin_core_sha() -> str:
    """Best-effort kgspin-core revision hash for manifest fingerprint."""
    try:
        import kgspin_core
        version = getattr(kgspin_core, "__version__", "")
        if version:
            return f"version:{version}"
    except Exception:
        pass
    try:
        import subprocess
        import kgspin_core
        core_root = Path(kgspin_core.__file__).resolve().parent
        out = subprocess.check_output(
            ["git", "-C", str(core_root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=2,
        )
        return f"sha:{out.decode().strip()}"
    except Exception:
        return "unknown"


def _manifest_fingerprint(source_sha: str, pipeline: str) -> dict:
    return {
        "source_sha": source_sha,
        "embedding_model": EMBED_MODEL_NAME,
        "chunk_config": {
            "window_tokens": CHUNK_WINDOW_TOKENS,
            "overlap_tokens": CHUNK_OVERLAP_TOKENS,
        },
        "pipeline": pipeline,
        "kgspin_core_sha": _kgspin_core_sha(),
    }


def _is_idempotent(out_dir: Path, fingerprint: dict) -> bool:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return all(existing.get(k) == v for k, v in fingerprint.items())


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------


def _resolve_evidence_span(
    plaintext: str,
    chunk_lookup: dict[str, Chunk],
    chunk_id: str,
    sentence_text: str,
) -> tuple[int, int]:
    """Find ``sentence_text`` inside the chunk's slice of ``plaintext``.
    Falls back to the full chunk span when sentence_text isn't found."""
    chunk = chunk_lookup.get(chunk_id)
    if chunk is None:
        return (0, 0)
    if not sentence_text:
        return (chunk.char_offset_start, chunk.char_offset_end)
    haystack = plaintext[chunk.char_offset_start:chunk.char_offset_end]
    idx = haystack.find(sentence_text)
    if idx < 0:
        return (chunk.char_offset_start, chunk.char_offset_end)
    abs_start = chunk.char_offset_start + idx
    return (abs_start, abs_start + len(sentence_text))


def _embed(texts: list[str], embedder) -> np.ndarray:
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    out = embedder.encode(texts, batch_size=64, show_progress_bar=False)
    if isinstance(out, list):
        out = np.asarray(out, dtype=np.float32)
    return np.asarray(out, dtype=np.float32)


def _bm25_tokenize(text: str) -> list[str]:
    return [t.lower() for t in WHITESPACE_RE.findall(text)]


def build_corpus_for_ticker(
    ticker: str, *,
    pipeline: str = "fan_out",
    force: bool = False,
    embedder=None,
    skip_extraction: bool = False,
) -> dict:
    """Build (or no-op) the RAG corpus for one ticker.

    Returns a dict {ticker, status, fingerprint, n_chunks, n_nodes, n_edges,
    elapsed_seconds}. ``skip_extraction=True`` reuses any existing
    ``graph.json`` (used by tests; production rebuilds always run extraction).
    """
    started = time.time()
    out_dir = CORPUS_OUT_DIR / ticker
    out_dir.mkdir(parents=True, exist_ok=True)

    src = _resolve_source(ticker)
    source_sha = sha256(src.raw_bytes).hexdigest()
    fingerprint = _manifest_fingerprint(source_sha=source_sha, pipeline=pipeline)

    if not force and _is_idempotent(out_dir, fingerprint):
        logger.info(
            "[%s] manifest fingerprint matches → no-op (use --force to rebuild)",
            ticker,
        )
        return {
            "ticker": ticker, "status": "noop",
            "fingerprint": fingerprint,
            "elapsed_seconds": round(time.time() - started, 2),
        }

    # Plaintext.
    (out_dir / "source.txt").write_text(src.plaintext, encoding="utf-8")

    # Chunks.
    chunks = _chunk_text(src.plaintext, ticker)
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
        json.dumps(chunks_payload, indent=2), encoding="utf-8",
    )
    chunk_lookup = {c.chunk_id: c for c in chunks}

    # Embedder lazy-load.
    if embedder is None:
        from sentence_transformers import SentenceTransformer
        embedder = SentenceTransformer(EMBED_MODEL_NAME)

    # Chunk embeddings.
    chunk_emb = _embed([c.text for c in chunks], embedder)
    np.save(out_dir / "chunk_embeddings.npy", chunk_emb)

    # BM25 index (lowercase whitespace tokens).
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi([_bm25_tokenize(c.text) for c in chunks])
    with (out_dir / "bm25_index.pkl").open("wb") as f:
        pickle.dump(bm25, f)

    # Extraction.
    graph_path = out_dir / "graph.json"
    if skip_extraction and graph_path.exists():
        logger.info("[%s] reusing existing graph.json (skip_extraction=True)", ticker)
        kg_dict = json.loads(graph_path.read_text(encoding="utf-8"))
    else:
        from extraction.public_api import run_fan_out_extraction
        logger.info("[%s] running %s extraction (this may take 5-10 min)…", ticker, pipeline)
        kg_dict = run_fan_out_extraction(
            text=src.plaintext,
            company_name=src.company_name,
            ticker=ticker,
            raw_html=src.raw_bytes.decode("utf-8", errors="ignore") if src.raw_kind == "html" else None,
            document_metadata={"company_name": src.company_name, "doc_id": ticker},
            pipeline=pipeline,
        )
        graph_path.write_text(json.dumps(kg_dict, indent=2, default=str), encoding="utf-8")

    # Walk extraction → graph_nodes / graph_edges with embeddings.
    raw_entities = kg_dict.get("entities", []) or []
    raw_relationships = kg_dict.get("relationships", []) or []

    node_payloads: list[dict] = []
    node_texts_for_embed: list[str] = []
    for ent in raw_entities:
        evidence = ent.get("evidence") or {}
        chunk_id = evidence.get("chunk_id") or ""
        sentence_text = evidence.get("sentence_text") or ""
        span = _resolve_evidence_span(
            src.plaintext, chunk_lookup, chunk_id, sentence_text,
        )
        node_payloads.append({
            "id": ent.get("id") or "",
            "text": ent.get("text") or "",
            "type": ent.get("entity_type") or "UNKNOWN",
            "semantic_definition": (ent.get("metadata") or {}).get("semantic_definition", "") or "",
            "parent_doc_offsets": list(span),
            "embedding_index": len(node_texts_for_embed),
        })
        embed_text = "{} [{}] {}".format(
            ent.get("text", ""),
            ent.get("entity_type", "UNKNOWN"),
            (ent.get("metadata") or {}).get("semantic_definition", "") or "",
        ).strip()
        node_texts_for_embed.append(embed_text)

    edge_payloads: list[dict] = []
    edge_texts_for_embed: list[str] = []
    for rel in raw_relationships:
        evidence = rel.get("evidence") or {}
        chunk_id = evidence.get("chunk_id") or ""
        sentence_text = evidence.get("sentence_text") or ""
        evidence_span = _resolve_evidence_span(
            src.plaintext, chunk_lookup, chunk_id, sentence_text,
        )
        subj = rel.get("subject") or {}
        obj = rel.get("object") or {}
        edge_payloads.append({
            "id": rel.get("id") or "",
            "src": subj.get("id") or "",
            "tgt": obj.get("id") or "",
            "predicate": rel.get("predicate") or "",
            "evidence_text": sentence_text,
            "evidence_char_span": list(evidence_span),
            "embedding_index": len(edge_texts_for_embed),
        })
        embed_text = "{} {}".format(
            rel.get("predicate", "") or "",
            sentence_text,
        ).strip()
        edge_texts_for_embed.append(embed_text)

    (out_dir / "graph_nodes.json").write_text(
        json.dumps(node_payloads, indent=2), encoding="utf-8",
    )
    (out_dir / "graph_edges.json").write_text(
        json.dumps(edge_payloads, indent=2), encoding="utf-8",
    )

    node_emb = _embed(node_texts_for_embed, embedder)
    edge_emb = _embed(edge_texts_for_embed, embedder)
    np.save(out_dir / "graph_node_embeddings.npy", node_emb)
    np.save(out_dir / "graph_edge_embeddings.npy", edge_emb)

    # Manifest.
    manifest = {
        **fingerprint,
        "ticker": ticker,
        "domain": src.domain,
        "company_name": src.company_name,
        "raw_source_path": str(src.raw_path),
        "n_chunks": len(chunks),
        "n_graph_nodes": len(node_payloads),
        "n_graph_edges": len(edge_payloads),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )

    return {
        "ticker": ticker, "status": "built",
        "fingerprint": fingerprint,
        "n_chunks": len(chunks),
        "n_nodes": len(node_payloads),
        "n_edges": len(edge_payloads),
        "elapsed_seconds": round(time.time() - started, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _expand_ticker_list(arg: str) -> tuple[str, ...]:
    if arg == "phase5a":
        return PHASE5A_TICKERS
    if arg == "all":
        return ALL_FIN_TICKERS
    return (arg,)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ticker", required=True,
        help="Ticker symbol, 'phase5a' (AAPL+JNJ+JNJ-Stelara), or 'all' (7 fin tickers).",
    )
    ap.add_argument(
        "--pipeline", default="fan_out",
        help="Extraction pipeline (default: fan_out).",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Rebuild even when manifest fingerprint matches.",
    )
    ap.add_argument(
        "--log-level", default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    tickers = _expand_ticker_list(args.ticker)
    rc = 0
    for t in tickers:
        try:
            result = build_corpus_for_ticker(
                t, pipeline=args.pipeline, force=args.force,
            )
            logger.info(
                "[%s] %s — chunks=%d nodes=%d edges=%d elapsed=%.1fs",
                t, result.get("status"),
                result.get("n_chunks", 0),
                result.get("n_nodes", 0),
                result.get("n_edges", 0),
                result.get("elapsed_seconds", 0.0),
            )
        except Exception as exc:
            logger.exception("[%s] build FAILED: %s", t, exc)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
