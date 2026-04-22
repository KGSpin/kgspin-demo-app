"""LLM prompt builders used by the demo app.

Currently holds the quality-analysis prompt (used by the compare pipeline to
grade KGs produced by each extractor). The impact-Q&A prompt is still
inline inside ``run_impact`` and has not been extracted into a dedicated
builder yet.
"""

from __future__ import annotations

from typing import Optional

import yaml as _yaml

from kgspin_demo_app.utils.kg_filters import compute_schema_compliance

from pipeline_common import PATTERNS_PATH


def build_quality_analysis_prompt(
    kgs_kg: dict, gem_kg: dict, gem_tokens: int,
    mod_kg: Optional[dict] = None, mod_tokens: int = 0,
    kgs_stats: Optional[dict] = None,
    gem_stats: Optional[dict] = None,
    mod_stats: Optional[dict] = None,
) -> str:
    """Build prompt for Gemini to compare KGs (2-way or 3-way).

    Sprint 90: Now includes pre-computed schema compliance and requests
    per-pipeline structured output for individual quality assessment cards.
    """
    # Lazy local import to avoid a module-load cycle: ``demo_compare`` imports
    # this function, and ``compute_diagnostic_scores`` still lives there
    # (pending the analysis.py carve).
    from demo_compare import compute_diagnostic_scores
    # Load target schema from patterns YAML for grading context
    try:
        with open(PATTERNS_PATH) as f:
            _patterns = _yaml.safe_load(f)
        _types = _patterns.get("types", {})
        valid_type_names = set(_types.keys())
        for info in _types.values():
            valid_type_names.update(info.get("subtypes", {}).keys())
        schema_lines = []
        for parent, info in sorted(_types.items()):
            subs = sorted(info.get("subtypes", {}).keys())
            if subs:
                schema_lines.append(f"  - {parent} (subtypes: {', '.join(subs)})")
            else:
                schema_lines.append(f"  - {parent}")
        valid_rels = [rp["name"] for rp in _patterns.get("relationship_patterns", [])]
        schema_section = f"""## TARGET SCHEMA (what all pipelines were asked to extract)
Valid entity types: {', '.join(sorted(valid_type_names))}
Type hierarchy:
{chr(10).join(schema_lines)}
Valid relationship types: {', '.join(sorted(valid_rels))}

Entities with types NOT in the valid list above are NOISE — they were not requested and should count AGAINST the pipeline that produced them when scoring precision."""
    except Exception:
        schema_section = ""
        valid_type_names = set()

    # Wave 3 follow-up: pipelines can now cache an explicit "failed" state
    # when an LLM call raises (e.g. Flash on a document > context window).
    # Surface that to the analysis prompt so the LLM reasons about the
    # failure rather than treating 0 ents / 0 rels as a silent null.
    failure_notes: list[str] = []
    for label, kg in (("LLM Full Shot", gem_kg), ("LLM Multi-Stage", mod_kg)):
        if not kg:
            continue
        if kg.get("status") == "failed":
            err = kg.get("error", {}) or {}
            reason = err.get("reason", "extraction_failed")
            msg = err.get("message", "no message")
            failure_notes.append(
                f"- {label}: FAILED ({reason}) — {msg}"
            )
    failure_section = ""
    if failure_notes:
        failure_section = (
            "\n## PIPELINE FAILURES (factor into your judgement — do NOT score a "
            "failed pipeline as if it returned zero findings; acknowledge the "
            "failure mode and what it implies about the approach)\n"
            + "\n".join(failure_notes)
            + "\n"
        )

    # Sprint 90: Pre-compute schema compliance (deterministic, not LLM-guessed)
    kgs_compliance = compute_schema_compliance(kgs_kg, valid_type_names)
    gem_compliance = compute_schema_compliance(gem_kg, valid_type_names) if gem_kg else None
    mod_compliance = compute_schema_compliance(mod_kg, valid_type_names) if mod_kg else None

    compliance_section = "\n## PRE-COMPUTED SCHEMA COMPLIANCE (deterministic — use these exact numbers)\n"
    compliance_section += f"- KGSpin: {kgs_compliance['compliance_pct']}% ({kgs_compliance['on_schema']}/{kgs_compliance['total']} on-schema)"
    if kgs_compliance['off_schema_types']:
        compliance_section += f" — off-schema types: {', '.join(kgs_compliance['off_schema_types'])}"
    compliance_section += "\n"
    if gem_compliance:
        compliance_section += f"- LLM Full Shot: {gem_compliance['compliance_pct']}% ({gem_compliance['on_schema']}/{gem_compliance['total']} on-schema)"
        if gem_compliance['off_schema_types']:
            compliance_section += f" — off-schema types: {', '.join(gem_compliance['off_schema_types'])}"
        compliance_section += "\n"
    if mod_compliance:
        compliance_section += f"- LLM Multi-Stage: {mod_compliance['compliance_pct']}% ({mod_compliance['on_schema']}/{mod_compliance['total']} on-schema)"
        if mod_compliance['off_schema_types']:
            compliance_section += f" — off-schema types: {', '.join(mod_compliance['off_schema_types'])}"
        compliance_section += "\n"

    kgs_entities = kgs_kg.get("entities", [])
    gem_entities = gem_kg.get("entities", [])
    kgs_rels = kgs_kg.get("relationships", [])
    gem_rels = gem_kg.get("relationships", [])

    # Truncate to top entities/relationships by confidence to keep prompt size manageable
    MAX_ENTITIES = 100
    MAX_RELS = 80

    def summarize_entities(entities):
        sorted_ents = sorted(entities, key=lambda e: e.get("confidence", 0), reverse=True)
        truncated = len(sorted_ents) > MAX_ENTITIES
        display = sorted_ents[:MAX_ENTITIES]
        lines = []
        for e in display:
            lines.append(f"  - {e.get('text', '?')} ({e.get('entity_type', '?')}, conf={e.get('confidence', 0):.2f})")
        if truncated:
            lines.append(f"  ... and {len(sorted_ents) - MAX_ENTITIES} more (showing top {MAX_ENTITIES} by confidence)")
        return "\n".join(lines)

    def summarize_rels(rels):
        sorted_rels = sorted(rels, key=lambda r: r.get("confidence", 0), reverse=True)
        truncated = len(sorted_rels) > MAX_RELS
        display = sorted_rels[:MAX_RELS]
        lines = []
        for r in display:
            s = r.get("subject", {}).get("text", "?")
            o = r.get("object", {}).get("text", "?")
            p = r.get("predicate", "?")
            c = r.get("confidence", 0)
            lines.append(f"  - {s} --[{p}]--> {o} (conf={c:.2f})")
        if truncated:
            lines.append(f"  ... and {len(sorted_rels) - MAX_RELS} more (showing top {MAX_RELS} by confidence)")
        return "\n".join(lines)

    # Sprint 33.5: Optional Multi-Stage section for 3-way comparison
    mod_section = ""
    mod_pipeline_json = ""
    if mod_kg:
        mod_entities = mod_kg.get("entities", [])
        mod_rels = mod_kg.get("relationships", [])
        mod_section = f"""

## LLM Multi-Stage ({mod_tokens:,} tokens used)
Entities ({len(mod_entities)} total):
{summarize_entities(mod_entities)}

Relationships ({len(mod_rels)} total):
{summarize_rels(mod_rels)}"""
        mod_pipeline_json = """,
    "multistage": {{
      "assessment": "2-3 sentence qualitative assessment",
      "strengths": "key strengths",
      "weaknesses": "key weaknesses",
      "precision": "high/medium/low",
      "recall": "high/medium/low"
    }}"""

    # Build performance metrics section from stats
    perf_section = ""
    if kgs_stats or gem_stats or mod_stats:
        perf_section = "\n## Performance Metrics\n"
        if kgs_stats:
            perf_section += f"- KGSpin: {kgs_stats.get('duration_ms', 0) / 1000:.1f}s, "
            perf_section += f"CPU cost ${kgs_stats.get('cpu_cost', 0):.4f}, "
            perf_section += f"{kgs_stats.get('num_chunks', 0)} chunks (embarrassingly parallel — scales linearly with CPUs)\n"
        if gem_stats:
            perf_section += f"- Full Document: {gem_stats.get('duration_ms', 0) / 1000:.1f}s, "
            perf_section += f"{gem_stats.get('tokens', 0):,} tokens (single monolithic API call, cannot be parallelized)\n"
        if mod_stats:
            perf_section += f"- Chunked: {mod_stats.get('duration_ms', 0) / 1000:.1f}s, "
            perf_section += f"{mod_stats.get('tokens', 0):,} tokens, "
            perf_section += f"{mod_stats.get('chunks_total', 0)} chunks (chunk-level parallelism possible)\n"

    comparison_type = "three" if mod_kg else "two"
    winner_options = "kgenskills|fullshot|multistage|tie" if mod_kg else "kgenskills|fullshot|tie"
    cost_line = f"KGSpin: 0 tokens. LLM Full Shot: {gem_tokens:,} tokens."
    if mod_kg:
        cost_line += f" LLM Multi-Stage: {mod_tokens:,} tokens."

    # Compute 3-way pairwise Performance Delta metrics for the prompt
    delta_scores = compute_diagnostic_scores(kgs_kg, mod_kg=mod_kg, gem_kg=gem_kg if gem_kg else None)

    delta_section = "\n## Pairwise Performance Delta\n"
    for pair_key, pair_data in delta_scores.get("pairs", {}).items():
        pair_labels = {
            "kgs_vs_multistage": ("KGSpin", "LLM Multi-Stage"),
            "kgs_vs_fullshot": ("KGSpin", "LLM Full Shot"),
            "multistage_vs_fullshot": ("LLM Multi-Stage", "LLM Full Shot"),
        }
        a_label, b_label = pair_labels.get(pair_key, ("A", "B"))
        delta_section += f"### {a_label} vs {b_label}\n"
        delta_section += f"- Entity overlap: {pair_data['entity_overlap']} shared, {pair_data['a_only_entities']} {a_label}-only, {pair_data['b_only_entities']} {b_label}-only\n"
        delta_section += f"- Relationship overlap: {pair_data['relationship_overlap']} shared, {pair_data['a_only_relationships']} {a_label}-only, {pair_data['b_only_relationships']} {b_label}-only\n"

    # Build counts summary table
    counts_table = f"""## EXACT COUNTS (use ONLY these numbers — do NOT invent or estimate other counts)
| Pipeline | Entities | Relationships | Tokens | Schema Compliance |
|----------|----------|---------------|--------|-------------------|
| KGSpin | {len(kgs_entities)} | {len(kgs_rels)} | 0 | {kgs_compliance['compliance_pct']}% |
| LLM Full Shot | {len(gem_entities)} | {len(gem_rels)} | {gem_tokens:,} | {gem_compliance['compliance_pct'] if gem_compliance else 'N/A'}% |"""
    if mod_kg:
        counts_table += f"\n| LLM Multi-Stage | {len(mod_entities)} | {len(mod_rels)} | {mod_tokens:,} | {mod_compliance['compliance_pct'] if mod_compliance else 'N/A'}% |"

    return f"""Compare {comparison_type} knowledge graphs extracted from the same document.

{schema_section}
{compliance_section}
{failure_section}
{counts_table}

## KGSpin (Compiled Semantics - 0 LLM tokens)
Entities ({len(kgs_entities)} total):
{summarize_entities(kgs_entities)}

Relationships ({len(kgs_rels)} total):
{summarize_rels(kgs_rels)}

## LLM Full Shot ({gem_tokens:,} tokens used)
Entities ({len(gem_entities)} total):
{summarize_entities(gem_entities)}

Relationships ({len(gem_rels)} total):
{summarize_rels(gem_rels)}
{mod_section}
{perf_section}
{delta_section}
## Analysis
For EACH pipeline, provide an independent qualitative assessment covering: schema compliance (use the pre-computed numbers above), entity coverage, relationship quality, and noise level.

CRITICAL GRADING RULES:
1. Use ONLY the exact counts from the EXACT COUNTS table above. Do NOT hallucinate or infer different counts.
2. **Schema compliance is pre-computed.** Use the exact percentages from the PRE-COMPUTED SCHEMA COMPLIANCE section. Do NOT re-count.
3. **Relationship coverage matters.** Do not declare a winner with significantly fewer relationships.
4. **Cost matters.** A zero-cost pipeline that achieves comparable schema-compliant results should be preferred over an expensive one. Explicitly penalize LLM winners if quality delta over KGSpin is <10% but cost is >100x.
5. Pipelines that only extract on-schema entity types demonstrate better precision and should be scored accordingly.

The Pairwise Performance Delta section above contains pre-computed consensus metrics. Use those numbers — do NOT re-count overlaps yourself.

Return JSON with per-pipeline assessments:
{{
  "summary": "2-3 sentence executive summary",
  "pipelines": {{
    "kgenskills": {{
      "assessment": "2-3 sentence qualitative assessment",
      "strengths": "key strengths",
      "weaknesses": "key weaknesses",
      "precision": "high/medium/low",
      "recall": "high/medium/low"
    }},
    "fullshot": {{
      "assessment": "2-3 sentence qualitative assessment",
      "strengths": "key strengths",
      "weaknesses": "key weaknesses",
      "precision": "high/medium/low",
      "recall": "high/medium/low"
    }}{mod_pipeline_json}
  }},
  "cost_analysis": "{cost_line} Assessment of value including scale economics.",
  "winner": "{winner_options}",
  "winner_reason": "Brief explanation"
}}"""
