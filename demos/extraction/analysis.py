"""Quality-analysis LLM runner.

Wraps ``build_quality_analysis_prompt`` (see ``prompts.py``) with the LLM
backend resolution path. Kept separate from the prompt builder so the
prompt text can be unit-tested without touching the LLM layer.
"""

from __future__ import annotations

import json
from typing import Optional

from kgspin_demo_app.utils.kg_filters import compute_schema_compliance

from prompts import build_quality_analysis_prompt


def run_quality_analysis(
    kgs_kg: dict, gem_kg: dict, gem_tokens: int,
    mod_kg: Optional[dict] = None, mod_tokens: int = 0,
    kgs_stats: Optional[dict] = None,
    gem_stats: Optional[dict] = None,
    mod_stats: Optional[dict] = None,
    *,
    llm_alias: str | None = None,
    legacy_model: str | None = None,
) -> dict:
    """Run quality analysis using an LLM (2-way or 3-way).

    Stage 0.5.4 (ADR-002): backend selection follows the demo's alias
    precedence (``llm_alias`` → legacy ``model`` → flow override →
    ``default_alias``). Callers thread the ambient request selectors in.
    """
    from kgspin_demo_app.llm_backend import resolve_llm_backend
    # ``_load_valid_entity_types`` still lives in ``demo_compare`` (pending a
    # scoring.py carve). Lazy import keeps the module-load order safe.
    from demo_compare import _load_valid_entity_types

    # Sprint 90: Pre-compute schema compliance (deterministic, returned alongside LLM analysis)
    valid_types = _load_valid_entity_types()
    schema_compliance = {
        "kgenskills": compute_schema_compliance(kgs_kg, valid_types),
    }
    if gem_kg:
        schema_compliance["fullshot"] = compute_schema_compliance(gem_kg, valid_types)
    if mod_kg:
        schema_compliance["multistage"] = compute_schema_compliance(mod_kg, valid_types)

    try:
        backend = resolve_llm_backend(
            llm_alias=llm_alias,
            legacy_model=legacy_model,
            flow="quality_analysis",
        )
        prompt = build_quality_analysis_prompt(
            kgs_kg, gem_kg, gem_tokens, mod_kg, mod_tokens,
            kgs_stats=kgs_stats, gem_stats=gem_stats, mod_stats=mod_stats,
        )
        result = backend.complete(prompt)

        try:
            analysis = json.loads(result.text)
        except json.JSONDecodeError:
            analysis = {"summary": result.text, "error": "Failed to parse JSON"}

        # Inject pre-computed schema compliance into analysis
        analysis["schema_compliance"] = schema_compliance

        return {"analysis": analysis, "tokens": result.tokens_used}

    except Exception as e:
        return {
            "analysis": {
                "summary": f"Quality analysis failed: {e}",
                "error": str(e),
                "schema_compliance": schema_compliance,
            },
            "tokens": 0,
        }
