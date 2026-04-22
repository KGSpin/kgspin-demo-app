"""Clinical-domain LLM extractor dispatch.

Wave B: ``_run_clinical_modular`` now returns a 5-tuple
``(kg, h_tokens, l_tokens, elapsed, error_count)``, matching
:func:`extraction.agentic._run_agentic_analyst` and resolving the
Liskov-arity drift the audit flagged. Callers that unpacked the old
4-tuple were updated in the same commit.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from bundle_resolve import _get_bundle

logger = logging.getLogger(__name__)


def _pipeline_ref(strategy: str):
    from demo_compare import _pipeline_ref_from_strategy
    return _pipeline_ref_from_strategy(strategy)


def _registry_client():
    from demo_compare import _get_registry_client
    return _get_registry_client()


def _run_clinical_gemini_full_shot(
    text: str,
    trial_name: str,
    source_id: str,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run ``agentic-flash`` against the clinical bundle (single-prompt LLM).

    Wave 3: same dispatch path as :func:`extraction.agentic._run_agentic_flash`;
    the clinical bundle carries the clinical entity types. No bundle mutation.
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

    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=trial_name,
            source_document=source_id,
            backend=backend,
            pipeline_config_ref=_pipeline_ref("agentic_flash"),
            registry_client=registry_client,
        )
        elapsed = time.time() - t0
        return result.to_dict(), 0, elapsed, 0, False
    except Exception:
        logger.exception("Clinical LLM Full Shot run_pipeline failed")
        raise


def _run_clinical_modular(
    text: str,
    trial_name: str,
    source_id: str,
    chunk_size: int | None = None,
    model: str | None = None,
    bundle_path: Path = None,
    patterns_path: Path = None,
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple:
    """Run ``agentic-analyst`` against the clinical bundle (multi-stage LLM).

    Wave 3: same dispatch path as :func:`extraction.agentic._run_agentic_analyst`.

    Wave B: returns a 5-tuple (kg, h_tokens, l_tokens, elapsed, error_count)
    to match the agentic-analyst shape. h_tokens / l_tokens are both 0
    until ExtractionResult surfaces LLM token counts for the clinical
    pipeline.
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

    t0 = time.time()
    try:
        result = extractor.run_pipeline(
            text=text,
            main_entity=trial_name,
            source_document=source_id,
            backend=backend,
            pipeline_config_ref=_pipeline_ref("agentic_analyst"),
            registry_client=registry_client,
        )
        elapsed = time.time() - t0
        return result.to_dict(), 0, 0, elapsed, 0
    except Exception:
        logger.exception("Clinical LLM Multi-Stage run_pipeline failed")
        raise
