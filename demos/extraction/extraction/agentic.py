"""Financial (agentic-flash / agentic-analyst) LLM extractor dispatch."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from bundle_resolve import _get_bundle

logger = logging.getLogger(__name__)

# Kept in sync with ``demo_compare.DEFAULT_CHUNK_SIZE``. Read at call time
# via lazy import inside each function to avoid import-order coupling.


def _pipeline_ref(strategy: str):
    # Lazy import to avoid a module-load cycle with demo_compare during
    # initial carve.
    from demo_compare import _pipeline_ref_from_strategy
    return _pipeline_ref_from_strategy(strategy)


def _registry_client():
    from demo_compare import _get_registry_client
    return _get_registry_client()


def _run_agentic_flash(
    text: str,
    company_name: str,
    source_id: str,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run the ``agentic-flash`` pipeline — single-prompt LLM extraction.

    Wave 3: dispatches through ``run_pipeline(pipeline_config_ref=...,
    registry_client=...)``; core loads the YAML from admin and invokes
    the ``AgenticFlashExtractor`` subclass. Returns
    (kg_dict, tokens, elapsed, error_count, truncated) to match the SSE
    event builder contract.
    """
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor
    from kgspin_demo_app.llm_backend import resolve_llm_backend

    bundle = _get_bundle(
        bundle_name=Path(bundle_path).name if bundle_path else None,
    )
    registry_client = _registry_client()

    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        legacy_model=model,
    )
    extractor = KnowledgeGraphExtractor(bundle)

    def log_cb(msg):
        logger.info(msg)

    logger.info(f"[AGENTIC_FLASH] starting model={model} text_chars={len(text)}")
    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=company_name,
            source_document=source_id,
            backend=backend,
            log_callback=log_cb,
            pipeline_config_ref=_pipeline_ref("agentic_flash"),
            registry_client=registry_client,
        )
        elapsed = time.time() - t0
        tokens = getattr(result.provenance, "tokens_used", 0) or 0
        logger.info(
            f"[AGENTIC_FLASH] complete elapsed={elapsed:.2f}s tokens={tokens}"
        )
        kg = result.to_dict()
        return kg, tokens, elapsed, 0, False
    except Exception:
        logger.exception("Agentic Flash run_pipeline failed")
        raise


def _run_agentic_analyst(
    text: str,
    company_name: str,
    source_id: str,
    on_chunk_complete=None,
    cancel_event=None,
    chunk_size: int | None = None,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run the ``agentic-analyst`` pipeline — chunked schema-aware LLM
    extraction. Same dispatch shape as :func:`_run_agentic_flash`.

    Returns (kg, h_tokens, l_tokens, elapsed, error_count); h/l_tokens
    stay 0 until ExtractionResult surfaces LLM token counts.
    """
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor
    from kgspin_demo_app.llm_backend import resolve_llm_backend

    bundle = _get_bundle(
        bundle_name=Path(bundle_path).name if bundle_path else None,
    )
    registry_client = _registry_client()

    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
        legacy_model=model,
    )
    extractor = KnowledgeGraphExtractor(bundle)

    def log_cb(msg):
        logger.info(msg)

    logger.info(f"[AGENTIC_ANALYST] starting model={model} chunk_size={chunk_size} text_chars={len(text)}")
    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=company_name,
            source_document=source_id,
            backend=backend,
            on_chunk_complete=on_chunk_complete,
            log_callback=log_cb,
            pipeline_config_ref=_pipeline_ref("agentic_analyst"),
            registry_client=registry_client,
        )
        elapsed = time.time() - t0
        tokens = getattr(result.provenance, "tokens_used", 0) or 0
        logger.info(
            f"[AGENTIC_ANALYST] complete elapsed={elapsed:.2f}s tokens={tokens}"
        )
        kg = result.to_dict()
        # (h_tokens, l_tokens) split isn't surfaced by the extractor yet;
        # report the aggregate as h_tokens, 0 as l_tokens, matching the
        # existing SSE event builder contract.
        return kg, tokens, 0, elapsed, 0
    except Exception:
        logger.exception("Agentic Analyst run_pipeline failed")
        raise
