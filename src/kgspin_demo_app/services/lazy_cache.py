"""Lazy cache builder — wires D7 + D8 into one entry point.

PRD-004 v5 Phase 5B (commits 7+8). When the modal Why-tab Run path
needs ``_doc/`` and/or ``_graph/{graph_key}/`` artifacts on disk, it
calls :func:`ensure_caches_on_disk(ticker, pipeline, bundle, kg_cache)`.

The function:
1. Resolves the lander locator for ``ticker``.
2. If ``_doc/`` is missing → builds it lazily (chunk + embed + BM25 over
   the lander's ``source.txt``). ~15-30s cold per source doc; instant warm.
3. If ``_graph/{graph_key}/`` is missing → reads the in-memory KG from
   ``kg_cache[ticker][<pipeline-specific-field>]`` and builds the graph
   index from it. ~5-10s cold; instant warm.

D7 — when the operator clicks Run on the Compare tab and a KG lands
in ``_kg_cache``, the modal Run path is what triggers the persist (not
an eager hook on the Compare-tab Run). Eager persist is deferred to
a follow-up sprint (it can run synchronously on the same Run thread).

D8 — synchronous lazy build (SSE progress UX deferred). Modal Run
blocks ~15-30s on cold first-time-per-(slot, pipeline). Subsequent
modal Runs hit warm caches and return instantly.

Cold-start LLM extraction is **not** triggered here — if the requested
pipeline's KG isn't in ``kg_cache``, raise ``KGNotInCache`` so the
modal UI can prompt the operator to "Run on Compare tab first" rather
than blocking ~5-10 min on a fresh extraction.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from kgspin_demo_app.services.cache_layout import (
    DocLocator,
    resolve_locator,
)
from kgspin_demo_app.services.doc_corpus_builder import (
    Chunk,
    build_doc_corpus,
)
from kgspin_demo_app.services.graph_corpus_builder import build_graph_index

logger = logging.getLogger(__name__)


# Maps a slot's pipeline label to the kg_cache field that holds its KG.
# Mirrors demo_compare.py's per-pipeline key convention (kgs_kg / gem_kg / mod_kg).
_PIPELINE_TO_KG_FIELD = {
    "fan_out": "kgs_kg",
    "kgenskills": "kgs_kg",          # legacy alias
    "discovery_rapid": "kgs_kg",     # uses fan_out's KG slot in current demo wiring
    "discovery_deep": "kgs_kg",      # same
    "agentic_flash": "gem_kg",
    "gemini": "gem_kg",              # legacy alias
    "agentic_analyst": "mod_kg",
    "modular": "mod_kg",             # legacy alias
}


class KGNotInCache(Exception):
    """Raised when modal Run needs a KG that hasn't been Compare-Run yet."""


class LanderNotFound(Exception):
    """Raised when the ticker has no lander tree on disk (re-fetch needed)."""


def _kg_for_pipeline(kg_cache_entry: dict, pipeline: str) -> Optional[dict]:
    """Pull the right KG dict out of the demo's kg_cache entry."""
    field = _PIPELINE_TO_KG_FIELD.get(pipeline)
    if field is None:
        return None
    kg = kg_cache_entry.get(field)
    return kg if isinstance(kg, dict) else None


def ensure_caches_on_disk(
    *,
    ticker: str,
    pipeline: str,
    bundle: str,
    bundle_version: str,
    kg_cache_entry: dict[str, Any],
    force: bool = False,
) -> tuple[Optional[DocLocator], "DocCacheStatus", "GraphCacheStatus"]:
    """Ensure ``_doc/`` and ``_graph/{graph_key}/`` exist for the slot.

    Returns ``(locator, doc_status, graph_status)`` where the statuses
    are tiny enum-like helpers for telemetry / progress UI.

    **Legacy-fallback contract**: if no D2-augmented lander tree exists
    for ``ticker`` (no ``manifest.json`` from the post-D2 lander run),
    returns ``(None, DocCacheStatus.LEGACY, GraphCacheStatus.LEGACY)``
    so the caller's downstream services (``dense_rag``, ``graph_rag``)
    can fall back to ``tests/fixtures/rag-corpus/{ticker}/`` — pre-5B
    fan_out fixtures stay readable without re-fetch.

    Raises:
        LanderNotFound — no lander tree at all (legacy fixture also missing
                         is the caller's CorpusNotBuilt to surface).
        KGNotInCache  — lander tree + D2 manifest exist, but the requested
                         pipeline's KG isn't in kg_cache for this ticker.
    """
    loc = resolve_locator(ticker)
    if loc is None:
        raise LanderNotFound(
            f"No lander tree found for {ticker!r}. "
            f"Run the lander first: `kgspin-demo-lander-sec --ticker {ticker}`"
        )

    # Legacy-fallback gate: D2 added manifest.json. Without it, we can't
    # compute the doc_key, so we let the legacy fixture path handle it.
    from kgspin_demo_app.services.cache_layout import read_lander_manifest
    if read_lander_manifest(loc) is None:
        logger.info(
            "[LAZY_CACHE] %s: no D2 manifest at %s — falling back to legacy fixtures.",
            loc.identifier, loc.manifest_path,
        )
        return None, DocCacheStatus.LEGACY, GraphCacheStatus.LEGACY

    # Step 1: ensure _doc/ on disk.
    doc_status = _ensure_doc_corpus(loc, force=force)

    # Step 2: ensure _graph/ on disk for this pipeline.
    graph_status = _ensure_graph_index(
        loc=loc,
        pipeline=pipeline,
        bundle=bundle,
        bundle_version=bundle_version,
        kg_cache_entry=kg_cache_entry,
        force=force,
    )

    return loc, doc_status, graph_status


# ---------------------------------------------------------------------------
# Status enums (lightweight)
# ---------------------------------------------------------------------------


class DocCacheStatus:
    HIT = "hit"
    BUILT = "built"
    LEGACY = "legacy"  # fell back to tests/fixtures/rag-corpus/{ticker}/


class GraphCacheStatus:
    HIT = "hit"
    BUILT = "built"
    LEGACY = "legacy"


def _ensure_doc_corpus(loc: DocLocator, *, force: bool) -> str:
    if not force and loc.doc_corpus_dir.exists() and (loc.doc_corpus_dir / "manifest.json").exists():
        return DocCacheStatus.HIT
    logger.info("[LAZY_CACHE] %s: building _doc/ corpus", loc.identifier)
    build_doc_corpus(loc, force=force)
    return DocCacheStatus.BUILT


def _ensure_graph_index(
    *,
    loc: DocLocator,
    pipeline: str,
    bundle: str,
    bundle_version: str,
    kg_cache_entry: dict[str, Any],
    force: bool,
) -> str:
    from kgspin_demo_app.services.cache_layout import kgspin_core_sha
    graph_dir = loc.graph_corpus_dir(
        pipeline=pipeline, bundle=bundle, core_sha=kgspin_core_sha(),
    )
    if not force and graph_dir.exists() and (graph_dir / "manifest.json").exists():
        return GraphCacheStatus.HIT

    kg_dict = _kg_for_pipeline(kg_cache_entry, pipeline)
    if kg_dict is None:
        raise KGNotInCache(
            f"No {pipeline!r} KG in kg_cache for {loc.identifier!r}. "
            f"Run the slot on the Compare tab first to extract the KG, "
            f"then re-open the modal."
        )

    # Load the per-doc corpus chunks (built in step 1) so the graph
    # builder can resolve evidence offsets against them.
    import json
    chunks_path = loc.doc_corpus_dir / "chunks.json"
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"_doc/chunks.json missing at {chunks_path}; doc corpus build failed silently."
        )
    raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [
        Chunk(
            chunk_id=c["id"],
            text=c["text"],
            char_offset_start=c["char_offset_start"],
            char_offset_end=c["char_offset_end"],
            source_section=c.get("source_section"),
        )
        for c in raw_chunks
    ]
    plaintext = loc.source_text_path.read_text(encoding="utf-8")

    logger.info(
        "[LAZY_CACHE] %s/%s: building _graph/ index (%d entities, %d relationships)",
        loc.identifier, pipeline,
        len(kg_dict.get("entities", [])),
        len(kg_dict.get("relationships", [])),
    )
    build_graph_index(
        loc,
        pipeline=pipeline,
        bundle=bundle,
        bundle_version=bundle_version,
        kg_dict=kg_dict,
        chunks=chunks,
        plaintext=plaintext,
        force=force,
    )
    return GraphCacheStatus.BUILT
