"""KGSpin (zero-LLM) extractor dispatch."""

from __future__ import annotations

import logging

from bundle_resolve import _get_gliner_backend

logger = logging.getLogger(__name__)


def _run_kgenskills(
    text: str, company_name: str, ticker: str, bundle,
    pipeline_config_ref,
    registry_client,
    on_chunk_complete=None, raw_html=None,
    on_l_module_start=None,
    on_table_extraction_start=None,
    on_table_extraction_done=None,
    on_post_chunk_progress=None,
    document_metadata: dict = None,
) -> dict:
    """Run a zero-token KGSpin pipeline via unified run_pipeline().

    Wave 3: one dispatch path. Caller passes a
    :class:`PipelineConfigRef` naming one of the 3 zero-LLM canonical
    pipelines (``fan-out``, ``discovery-rapid``, ``discovery-deep``);
    core resolves the YAML via admin and invokes the matching
    ``Extractor`` subclass.
    """
    from kgspin_core.execution.extractor import KnowledgeGraphExtractor

    backend = _get_gliner_backend()
    extractor = KnowledgeGraphExtractor(bundle)

    def log_cb(msg):
        logger.info(msg)

    result = extractor.run_pipeline(
        text=text,
        main_entity=company_name,
        source_document=f"{ticker}_10K",
        pipeline_config_ref=pipeline_config_ref,
        registry_client=registry_client,
        backend=backend,
        raw_html=raw_html,
        log_callback=log_cb,
        on_chunk_complete=on_chunk_complete,
        on_post_chunk_progress=on_post_chunk_progress,
        document_metadata=document_metadata,
    )

    kg_dict = result.to_dict()
    # Sprint 101: Attach quarantine count for demo display.
    quarantine_count = len(getattr(extractor, '_last_emergent_quarantine', []) or [])
    kg_dict["_quarantine_count"] = quarantine_count
    # Preserve H-Module entities (with aliases) for news seeding
    if hasattr(result, '_h_module_entities') and result._h_module_entities:
        kg_dict["_h_module_entities"] = [e.to_dict() for e in result._h_module_entities]
    return kg_dict
