"""Public extraction wrappers for non-FastAPI callers.

PRD-004 v5 Phase 5A introduces a corpus builder
(``scripts/build_rag_corpus.py``) that needs to invoke the same fan_out
extraction the live demo uses, but without instantiating the FastAPI
app. Rather than have the script reach into ``demo_compare.py``'s
underscore-private internals, we expose a single thin public helper
here that constructs the same (bundle, pipeline_config_ref,
registry_client) triple and delegates to ``_run_kgenskills``.

The demo's existing call sites continue to use ``_run_kgenskills``
directly — this module is purely additive.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# `demos/extraction` is on sys.path when imported from the running demo
# server; for CLI callers (build_rag_corpus.py) we ensure the folder is
# importable so `bundle_resolve` / `demo_compare` resolve.
_DEMO_DIR = Path(__file__).resolve().parents[1]
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

logger = logging.getLogger(__name__)


def run_fan_out_extraction(
    text: str,
    company_name: str,
    ticker: str,
    *,
    raw_html: Optional[str] = None,
    bundle_name: Optional[str] = None,
    document_metadata: Optional[dict] = None,
    pipeline: str = "fan_out",
) -> dict:
    """Run a zero-LLM extraction pipeline (default ``fan_out``) and return the
    KG dict.

    Wraps the four-input contract of ``_run_kgenskills`` (text +
    company_name + ticker + bundle + pipeline_config_ref +
    registry_client) so non-FastAPI callers get a stable public surface.

    Parameters
    ----------
    text : str
        The plaintext to extract from. Caller has already done any HTML
        scrubbing (the corpus builder strips HTML before calling this).
    company_name : str
        The filer's canonical name. Threaded into the H-module resolver
        as the ``company_name`` override (see Defect 1 fix 2026-04-24).
    ticker : str
        The ticker / doc id; used for source_document and cache keys.
    raw_html : str, optional
        Original HTML when available; some bundle plugins use it for
        table-aware extraction.
    bundle_name : str, optional
        Domain bundle name (e.g. ``"financial-v2"``). When ``None``,
        defers to the demo's default bundle resolution.
    document_metadata : dict, optional
        Dict matching ``bundle.document_seed_facts[].source_field``
        keys. Plumbed verbatim into ``_run_kgenskills``.
    pipeline : str
        Canonical pipeline strategy (``"fan_out"`` by default — densest
        zero-LLM KG; the corpus builder uses this).

    Returns
    -------
    dict
        The fan_out extractor's ``kg_dict`` (entities, relationships,
        provenance, document_context).

    Notes
    -----
    Uses ``demo_compare`` internals (``_run_kgenskills``,
    ``_pipeline_ref_from_strategy``, ``_get_registry_client``) — these
    are stable enough to import for tooling per the dev-team's existing
    practice, and Wave G already used them similarly. Refactors to those
    private symbols would update this wrapper in lock-step.
    """
    # Late imports keep the public_api module importable without
    # bringing the FastAPI app online (build_rag_corpus.py needs that).
    from bundle_resolve import _get_bundle  # noqa: WPS433
    from demo_compare import (  # noqa: WPS433
        _get_registry_client,
        _pipeline_ref_from_strategy,
    )
    from extraction.kgen import _run_kgenskills  # noqa: WPS433

    bundle = _get_bundle(bundle_name=bundle_name)
    pipeline_config_ref = _pipeline_ref_from_strategy(pipeline)
    registry_client = _get_registry_client()

    logger.info(
        "[public_api] run_fan_out_extraction ticker=%s pipeline=%s text_chars=%d",
        ticker, pipeline, len(text),
    )
    return _run_kgenskills(
        text=text,
        company_name=company_name,
        ticker=ticker,
        bundle=bundle,
        pipeline_config_ref=pipeline_config_ref,
        registry_client=registry_client,
        raw_html=raw_html,
        document_metadata=document_metadata,
    )


__all__ = ["run_fan_out_extraction"]
