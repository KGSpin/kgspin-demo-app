#!/usr/bin/env python3
"""
Overnight KG Quality Experiment — 5-phase pipeline.

Runs LLM extractions first (as golden reference), generates consolidated
golden entity/relationship lists via Gemini CLI, then runs all 3 KGSpin
strategies with full logging, and finally calls VP reviewers on the results.

Phases:
    1. LLM Full Shot + Multi-Stage on each ticker  (~5-10 min)
    2. Gemini CLI generates golden entity/rel lists  (~2-3 min per ticker)
    3. KGSpin Emergent/Structural/Base on each ticker (~5-15 min per ticker)
    4. Cross-strategy comparison analysis              (instant)
    5. VP review of collected results                   (~2-3 min)

Output directory structure:
    docs/sprints/sprint-118/overnight-experiment/
        {TICKER}/
            llm_full_shot.json        — LLM Full Shot KG
            llm_multi_stage.json      — LLM Multi-Stage KG
            golden_entities.json      — Gemini-curated golden entity list
            golden_relationships.json — Gemini-curated golden relationship list
            kgspin_emergent.json      — KGSpin Emergent KG
            kgspin_structural.json    — KGSpin Structural KG
            kgspin_base.json          — KGSpin Base KG
            comparison.json           — Cross-strategy comparison metrics
        summary.json                  — Experiment-wide summary
        comparison_report.md          — Human-readable comparison report
        vp-eng-review.md              — VP Eng evaluation
        vp-prod-review.md             — VP Product evaluation

Usage:
    # Full run (5 tickers, all phases)
    uv run python demos/extraction/run_overnight_experiment.py \\
        --tickers JNJ AAPL MSFT JPM NVDA

    # Dry run
    uv run python demos/extraction/run_overnight_experiment.py --dry-run

    # Skip LLM phase (reuse existing golden data)
    uv run python demos/extraction/run_overnight_experiment.py --skip-llm

    # Skip VP review phase
    uv run python demos/extraction/run_overnight_experiment.py --skip-vp-review
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_common import (
    KNOWN_TICKERS,
    resolve_ticker,
    resolve_domain_bundle_path,
    resolve_domain_yaml_path,
    BUNDLE_PATH,
    PATTERNS_PATH,
)

ALL_TICKERS = list(KNOWN_TICKERS.keys())
KGSPIN_STRATEGIES = ["emergent", "structural", "base"]

# ── Logging ──────────────────────────────────────────────────────────────────

def _ts():
    return datetime.now().strftime("%H:%M:%S")

def _log(msg, level="INFO"):
    print(f"[{_ts()}] [{level}] {msg}", flush=True)

def _banner(text, char="=", width=72):
    print(f"\n{char * width}", flush=True)
    print(f"  {text}", flush=True)
    print(f"{char * width}\n", flush=True)

def _format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m{secs:.0f}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h{mins}m{secs:.0f}s"


# ── Phase 1: LLM Extractions ────────────────────────────────────────────────

def phase1_llm_extractions(tickers, domain_id, corpus_kb, model, output_dir, llm_alias=None):
    """Run LLM Full Shot + Multi-Stage and save raw KGs to disk.

    Stage 0.5.4 (ADR-002): ``llm_alias`` threads through to the batch
    runner's alias-aware backend factory.
    """
    from run_overnight_batch import run_llm_full_shot, run_llm_multi_stage, fetch_sec_filing

    _banner("PHASE 1: LLM Extractions (Golden Reference)")
    results = {}

    for i, ticker in enumerate(tickers):
        _log(f"[{i+1}/{len(tickers)}] {ticker}: Fetching 10-K...")
        info = resolve_ticker(ticker)
        company_name = info["name"]

        sec_doc = fetch_sec_filing(ticker)
        if not sec_doc:
            _log(f"  SKIP: Could not fetch 10-K for {ticker}", "ERROR")
            continue

        ticker_dir = output_dir / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)

        # Full Shot
        _log(f"  Running LLM Full Shot...")
        try:
            fs_result = run_llm_full_shot(
                ticker, company_name, domain_id, corpus_kb, sec_doc, model,
                llm_alias=llm_alias,
            )
            _log(f"  Full Shot: {fs_result['entities']} entities, {fs_result['relationships']} rels, {_format_duration(fs_result['elapsed'])}")

            # Save raw KG from run log
            from demo_compare import GeminiRunLog
            run_log = GeminiRunLog()
            latest = run_log.latest(ticker, fs_result["cache_key"])
            if latest:
                (ticker_dir / "llm_full_shot.json").write_text(
                    json.dumps(latest, indent=2, default=str)
                )
            results.setdefault(ticker, {})["full_shot"] = fs_result
        except Exception as e:
            _log(f"  Full Shot FAILED: {e}", "ERROR")
            results.setdefault(ticker, {})["full_shot"] = {"error": str(e)}

        # Multi-Stage
        _log(f"  Running LLM Multi-Stage...")
        try:
            ms_result = run_llm_multi_stage(
                ticker, company_name, domain_id, corpus_kb, sec_doc, model,
                llm_alias=llm_alias,
            )
            _log(f"  Multi-Stage: {ms_result['entities']} entities, {ms_result['relationships']} rels, {_format_duration(ms_result['elapsed'])}")

            from demo_compare import ModularRunLog
            run_log = ModularRunLog()
            latest = run_log.latest(ticker, ms_result["cache_key"])
            if latest:
                (ticker_dir / "llm_multi_stage.json").write_text(
                    json.dumps(latest, indent=2, default=str)
                )
            results.setdefault(ticker, {})["multi_stage"] = ms_result
        except Exception as e:
            _log(f"  Multi-Stage FAILED: {e}", "ERROR")
            results.setdefault(ticker, {})["multi_stage"] = {"error": str(e)}

    return results


# ── Phase 2: Golden Data via Gemini CLI ──────────────────────────────────────

def _build_golden_prompt(ticker, company_name, llm_entities, llm_relationships):
    """Build prompt for Gemini CLI to produce golden entity/relationship lists."""

    # Deduplicate entities across both LLM runs
    ent_texts = set()
    ent_list = []
    for e in llm_entities:
        key = (e.get("text", "").strip().lower(), e.get("entity_type", ""))
        if key not in ent_texts and key[0]:
            ent_texts.add(key)
            ent_list.append(f"  - [{e.get('entity_type', '?')}] {e.get('text', '?')}")

    rel_list = []
    rel_texts = set()
    for r in llm_relationships:
        subj = r.get("subject", {})
        obj = r.get("object", {})
        src = subj.get("text", "?") if isinstance(subj, dict) else str(subj)
        tgt = obj.get("text", "?") if isinstance(obj, dict) else str(obj)
        pred = r.get("predicate", "?")
        key = (src.lower(), pred.lower(), tgt.lower())
        if key not in rel_texts:
            rel_texts.add(key)
            rel_list.append(f"  - {src} --[{pred}]--> {tgt}")

    prompt = f"""You are a financial knowledge graph expert reviewing entity and relationship extractions from {company_name} ({ticker})'s 10-K SEC filing.

Two LLM extraction systems produced the following candidate entities and relationships.
Your job is to curate a GOLDEN reference list by:

1. KEEPING entities that are real, specific named entities relevant to understanding {company_name}'s business
2. REMOVING noise: generic terms, sentence fragments, partial phrases, overly broad categories, numbers without context, legal boilerplate terms
3. FIXING entity types if misclassified
4. KEEPING relationships that represent real, verifiable business facts
5. REMOVING relationships that are vague, redundant, or obviously wrong

ENTITY TYPE RULES:
- PERSON: Named individuals (executives, board members, key people)
- ORGANIZATION: Named companies, subsidiaries, agencies, institutions
- LOCATION: Named places (cities, countries, offices, facilities)
- PRODUCT: Named products, drugs, technologies, services, brands
- EXECUTIVE: C-suite, VP+, board members (subtype of PERSON)
- EMPLOYEE: Non-executive named individuals (subtype of PERSON)
- COMPANY: Business entities, subsidiaries (subtype of ORGANIZATION)
- REGULATOR: Government/regulatory bodies (subtype of ORGANIZATION)
- OFFICE: Named offices, headquarters, facilities (subtype of LOCATION)
- MARKET: Named markets, segments, geographic markets (subtype of LOCATION)
- BRANDED_PRODUCT: Specific branded products (subtype of PRODUCT)
- SERVICE: Named services, platforms (subtype of PRODUCT)

NOISE INDICATORS (REMOVE these):
- Generic business terms: "revenue", "operations", "competitors", "customers"
- Sentence fragments or partial phrases
- Single common words: "market", "risk", "growth"
- Legal/regulatory boilerplate: "Section 302", "Item 1A"
- Numbers or dates without entity context
- Pronouns or demonstratives: "the Company", "its subsidiaries"
- Overly broad categories: "healthcare products", "pharmaceutical segment"

=== CANDIDATE ENTITIES ({len(ent_list)} total) ===
{chr(10).join(ent_list[:500])}
{"... (truncated, " + str(len(ent_list) - 500) + " more)" if len(ent_list) > 500 else ""}

=== CANDIDATE RELATIONSHIPS ({len(rel_list)} total) ===
{chr(10).join(rel_list[:300])}
{"... (truncated, " + str(len(rel_list) - 300) + " more)" if len(rel_list) > 300 else ""}

Respond with ONLY valid JSON in this exact format (no markdown, no explanation):
{{
  "golden_entities": [
    {{"text": "Johnson & Johnson", "entity_type": "COMPANY", "keep_reason": "Primary subject company"}},
    ...
  ],
  "golden_relationships": [
    {{"subject": "Johnson & Johnson", "predicate": "MANUFACTURES", "object": "Tylenol", "keep_reason": "Verified product relationship"}},
    ...
  ],
  "noise_entities_removed": [
    {{"text": "revenue", "entity_type": "PRODUCT", "remove_reason": "Generic business term, not a named entity"}},
    ...up to 30 examples...
  ],
  "noise_patterns": [
    "Generic business terminology (revenue, operations, market share)",
    "Legal section references (Item 1A, Section 302)",
    ...
  ]
}}"""
    return prompt


def phase2_golden_data(tickers, output_dir):
    """Use Gemini CLI to curate golden entity/relationship lists from LLM results."""
    _banner("PHASE 2: Golden Data Generation (Gemini CLI)")

    gemini_cmd = os.environ.get("GEMINI_CMD", "gemini")

    for i, ticker in enumerate(tickers):
        ticker_dir = output_dir / ticker
        if not ticker_dir.exists():
            _log(f"  SKIP {ticker}: no Phase 1 data", "WARN")
            continue

        _log(f"[{i+1}/{len(tickers)}] {ticker}: Generating golden data...")

        # Load LLM results
        all_entities = []
        all_relationships = []

        for llm_file in ["llm_full_shot.json", "llm_multi_stage.json"]:
            fpath = ticker_dir / llm_file
            if fpath.exists():
                data = json.loads(fpath.read_text())
                kg = data.get("kg", data)
                all_entities.extend(kg.get("entities", []))
                all_relationships.extend(kg.get("relationships", []))

        if not all_entities:
            _log(f"  SKIP {ticker}: no LLM entities found", "WARN")
            continue

        _log(f"  Input: {len(all_entities)} entities, {len(all_relationships)} rels from LLMs")

        info = resolve_ticker(ticker)
        prompt = _build_golden_prompt(ticker, info["name"], all_entities, all_relationships)

        # Write prompt to temp file
        prompt_file = ticker_dir / "_golden_prompt.txt"
        prompt_file.write_text(prompt)

        golden_file = ticker_dir / "_golden_raw.txt"

        try:
            result = subprocess.run(
                [gemini_cmd],
                input=prompt, capture_output=True, text=True, timeout=300,
                cwd=str(PROJECT_ROOT),
            )
            raw_output = result.stdout.strip()
            golden_file.write_text(raw_output)

            # Parse JSON from output (strip markdown fences if present)
            json_text = raw_output
            if "```json" in json_text:
                json_text = json_text.split("```json", 1)[1].split("```", 1)[0]
            elif "```" in json_text:
                json_text = json_text.split("```", 1)[1].split("```", 1)[0]

            golden = json.loads(json_text.strip())

            # Save structured golden data
            golden_ents = golden.get("golden_entities", [])
            golden_rels = golden.get("golden_relationships", [])
            noise_removed = golden.get("noise_entities_removed", [])
            noise_patterns = golden.get("noise_patterns", [])

            (ticker_dir / "golden_entities.json").write_text(
                json.dumps({
                    "ticker": ticker,
                    "company": info["name"],
                    "entity_count": len(golden_ents),
                    "entities": golden_ents,
                    "noise_removed_count": len(noise_removed),
                    "noise_removed_examples": noise_removed,
                    "noise_patterns": noise_patterns,
                }, indent=2)
            )

            (ticker_dir / "golden_relationships.json").write_text(
                json.dumps({
                    "ticker": ticker,
                    "company": info["name"],
                    "relationship_count": len(golden_rels),
                    "relationships": golden_rels,
                }, indent=2)
            )

            _log(f"  Golden: {len(golden_ents)} entities, {len(golden_rels)} rels")
            _log(f"  Noise removed: {len(noise_removed)} examples, {len(noise_patterns)} patterns")

        except subprocess.TimeoutExpired:
            _log(f"  Gemini CLI timed out for {ticker}", "ERROR")
        except json.JSONDecodeError as e:
            _log(f"  Failed to parse Gemini output as JSON: {e}", "ERROR")
            _log(f"  Raw output saved to {golden_file}", "WARN")
        except Exception as e:
            _log(f"  Golden data generation failed: {e}", "ERROR")
            traceback.print_exc()


# ── Phase 3: KGSpin Extractions ─────────────────────────────────────────────

def phase3_kgspin_extractions(tickers, domain_id, corpus_kb, output_dir):
    """Run all 3 KGSpin strategies with detailed entity/relationship logging."""
    from run_overnight_batch import run_kgspin, fetch_sec_filing

    _banner("PHASE 3: KGSpin Extractions (Emergent / Structural / Base)")
    results = {}

    for i, ticker in enumerate(tickers):
        _log(f"[{i+1}/{len(tickers)}] {ticker}")
        info = resolve_ticker(ticker)
        company_name = info["name"]

        sec_doc = fetch_sec_filing(ticker)
        if not sec_doc:
            _log(f"  SKIP: Could not fetch 10-K for {ticker}", "ERROR")
            continue

        ticker_dir = output_dir / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)
        results[ticker] = {}

        for strategy in KGSPIN_STRATEGIES:
            _log(f"  Running KGSpin {strategy}...")
            try:
                r = run_kgspin(
                    ticker, company_name, strategy, domain_id,
                    corpus_kb, sec_doc, info,
                )
                _log(f"    {r['entities']} entities, {r['relationships']} rels, {_format_duration(r['elapsed'])}")
                results[ticker][strategy] = r

                # Save raw KG from run log
                from demo_compare import KGenRunLog, _split_bundle_id
                run_log = KGenRunLog()
                bid = _split_bundle_id(domain_id, strategy)
                cfg_key = run_log.config_key("kgen", bid=bid, corpus_kb=corpus_kb)
                latest = run_log.latest(ticker, cfg_key)
                if latest:
                    (ticker_dir / f"kgspin_{strategy}.json").write_text(
                        json.dumps(latest, indent=2, default=str)
                    )

            except Exception as e:
                _log(f"    FAILED: {e}", "ERROR")
                results[ticker][strategy] = {"error": str(e)}

    return results


# ── Phase 4: Cross-Strategy Comparison ──────────────────────────────────────

def _extract_entities_set(kg_data):
    """Extract normalized entity set from a KG dict."""
    kg = kg_data.get("kg", kg_data)
    entities = {}
    for e in kg.get("entities", []):
        text = e.get("text", "").strip()
        if text:
            key = text.lower()
            if key not in entities:
                entities[key] = {
                    "text": text,
                    "entity_type": e.get("entity_type", "UNKNOWN"),
                    "confidence": e.get("confidence", 0),
                }
    return entities


def _extract_rels_set(kg_data):
    """Extract normalized relationship set from a KG dict."""
    kg = kg_data.get("kg", kg_data)
    rels = {}
    for r in kg.get("relationships", []):
        subj = r.get("subject", {})
        obj = r.get("object", {})
        src = (subj.get("text", "?") if isinstance(subj, dict) else str(subj)).strip().lower()
        tgt = (obj.get("text", "?") if isinstance(obj, dict) else str(obj)).strip().lower()
        pred = r.get("predicate", "?").strip()
        key = f"{src}|{pred}|{tgt}"
        if key not in rels:
            rels[key] = {
                "subject": subj.get("text", "?") if isinstance(subj, dict) else str(subj),
                "predicate": pred,
                "object": obj.get("text", "?") if isinstance(obj, dict) else str(obj),
                "confidence": r.get("confidence", 0),
                "source": r.get("source", ""),
            }
    return rels


def _jaccard(set_a, set_b):
    """Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0


def phase4_comparison(tickers, output_dir):
    """Generate cross-strategy comparison analysis."""
    _banner("PHASE 4: Cross-Strategy Comparison")

    all_comparisons = {}
    report_lines = [
        "# Overnight Experiment: Cross-Strategy Comparison Report",
        f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    pipelines = ["llm_full_shot", "llm_multi_stage", "kgspin_emergent", "kgspin_structural", "kgspin_base"]
    pipeline_labels = {
        "llm_full_shot": "LLM Full Shot",
        "llm_multi_stage": "LLM Multi-Stage",
        "kgspin_emergent": "KGSpin Emergent",
        "kgspin_structural": "KGSpin Structural",
        "kgspin_base": "KGSpin Base",
    }

    for ticker in tickers:
        ticker_dir = output_dir / ticker
        if not ticker_dir.exists():
            continue

        _log(f"Comparing {ticker}...")
        info = resolve_ticker(ticker)

        # Load all KGs
        kgs = {}
        entity_sets = {}
        rel_sets = {}
        for p in pipelines:
            fpath = ticker_dir / f"{p}.json"
            if fpath.exists():
                data = json.loads(fpath.read_text())
                kgs[p] = data
                entity_sets[p] = _extract_entities_set(data)
                rel_sets[p] = _extract_rels_set(data)

        # Load golden data
        golden_ents = {}
        golden_rels = {}
        golden_ent_path = ticker_dir / "golden_entities.json"
        golden_rel_path = ticker_dir / "golden_relationships.json"
        if golden_ent_path.exists():
            gdata = json.loads(golden_ent_path.read_text())
            for e in gdata.get("entities", []):
                key = e.get("text", "").strip().lower()
                if key:
                    golden_ents[key] = e
        if golden_rel_path.exists():
            gdata = json.loads(golden_rel_path.read_text())
            for r in gdata.get("relationships", []):
                src = r.get("subject", "").strip().lower()
                tgt = r.get("object", "").strip().lower()
                pred = r.get("predicate", "").strip()
                key = f"{src}|{pred}|{tgt}"
                golden_rels[key] = r

        comparison = {
            "ticker": ticker,
            "company": info["name"],
            "golden_entity_count": len(golden_ents),
            "golden_rel_count": len(golden_rels),
            "pipelines": {},
            "entity_overlap": {},
            "precision_vs_golden": {},
            "recall_vs_golden": {},
        }

        # Per-pipeline stats
        for p in pipelines:
            if p not in entity_sets:
                continue
            ents = entity_sets[p]
            rels = rel_sets.get(p, {})

            # Type distribution
            type_dist = {}
            for e in ents.values():
                t = e["entity_type"]
                type_dist[t] = type_dist.get(t, 0) + 1

            comparison["pipelines"][p] = {
                "entity_count": len(ents),
                "rel_count": len(rels),
                "entity_type_distribution": type_dist,
                "rel_density": round(len(rels) / max(len(ents), 1), 2),
            }

            # Precision/Recall vs golden
            if golden_ents:
                p_set = set(ents.keys())
                g_set = set(golden_ents.keys())
                tp = len(p_set & g_set)
                precision = tp / len(p_set) if p_set else 0
                recall = tp / len(g_set) if g_set else 0
                f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
                comparison["precision_vs_golden"][p] = round(precision, 3)
                comparison["recall_vs_golden"][p] = round(recall, 3)
                comparison["pipelines"][p]["precision"] = round(precision, 3)
                comparison["pipelines"][p]["recall"] = round(recall, 3)
                comparison["pipelines"][p]["f1"] = round(f1, 3)
                comparison["pipelines"][p]["true_positives"] = tp
                comparison["pipelines"][p]["false_positives"] = len(p_set - g_set)
                comparison["pipelines"][p]["false_negatives"] = len(g_set - p_set)

        # Pairwise entity overlap (Jaccard)
        for p1 in pipelines:
            for p2 in pipelines:
                if p1 >= p2 or p1 not in entity_sets or p2 not in entity_sets:
                    continue
                j = _jaccard(set(entity_sets[p1].keys()), set(entity_sets[p2].keys()))
                comparison["entity_overlap"][f"{p1}_vs_{p2}"] = round(j, 3)

        # Emergent noise analysis (entities unique to emergent vs golden)
        emergent_noise = []
        if "kgspin_emergent" in entity_sets and golden_ents:
            emergent_keys = set(entity_sets["kgspin_emergent"].keys())
            golden_keys = set(golden_ents.keys())
            noise_keys = emergent_keys - golden_keys
            for key in sorted(noise_keys):
                e = entity_sets["kgspin_emergent"][key]
                emergent_noise.append({
                    "text": e["text"],
                    "entity_type": e["entity_type"],
                    "confidence": e.get("confidence", 0),
                })
            comparison["emergent_noise"] = {
                "count": len(emergent_noise),
                "examples": emergent_noise[:100],
            }

        # Emergent gaps (entities in golden but missing from emergent)
        emergent_gaps = []
        if "kgspin_emergent" in entity_sets and golden_ents:
            emergent_keys = set(entity_sets["kgspin_emergent"].keys())
            golden_keys = set(golden_ents.keys())
            gap_keys = golden_keys - emergent_keys
            for key in sorted(gap_keys):
                e = golden_ents[key]
                emergent_gaps.append({
                    "text": e["text"],
                    "entity_type": e.get("entity_type", "?"),
                })
            comparison["emergent_gaps"] = {
                "count": len(emergent_gaps),
                "examples": emergent_gaps[:100],
            }

        # Strengths of base/structural over emergent
        for alt in ["kgspin_structural", "kgspin_base"]:
            if alt in entity_sets and "kgspin_emergent" in entity_sets and golden_ents:
                alt_keys = set(entity_sets[alt].keys())
                em_keys = set(entity_sets["kgspin_emergent"].keys())
                golden_keys = set(golden_ents.keys())
                # Entities that alt finds correctly but emergent misses
                alt_wins = (alt_keys & golden_keys) - em_keys
                comparison[f"{alt}_wins_over_emergent"] = {
                    "count": len(alt_wins),
                    "examples": [
                        {"text": entity_sets[alt][k]["text"], "entity_type": entity_sets[alt][k]["entity_type"]}
                        for k in sorted(alt_wins)
                    ][:50],
                }

        # Save per-ticker comparison
        (ticker_dir / "comparison.json").write_text(
            json.dumps(comparison, indent=2, default=str)
        )
        all_comparisons[ticker] = comparison

        # Build report section
        report_lines.append(f"## {ticker} — {info['name']}")
        report_lines.append("")
        report_lines.append(f"Golden reference: {len(golden_ents)} entities, {len(golden_rels)} relationships")
        report_lines.append("")
        report_lines.append("| Pipeline | Entities | Rels | Precision | Recall | F1 | FP | FN |")
        report_lines.append("|----------|----------|------|-----------|--------|-----|-----|-----|")
        for p in pipelines:
            if p not in comparison.get("pipelines", {}):
                continue
            ps = comparison["pipelines"][p]
            report_lines.append(
                f"| {pipeline_labels.get(p, p)} | {ps['entity_count']} | {ps['rel_count']} | "
                f"{ps.get('precision', 'N/A')} | {ps.get('recall', 'N/A')} | {ps.get('f1', 'N/A')} | "
                f"{ps.get('false_positives', 'N/A')} | {ps.get('false_negatives', 'N/A')} |"
            )

        report_lines.append("")

        # Emergent noise summary
        if "emergent_noise" in comparison:
            noise = comparison["emergent_noise"]
            report_lines.append(f"### Emergent Noise: {noise['count']} false positives")
            report_lines.append("")
            # Group by type
            noise_by_type = {}
            for n in noise["examples"]:
                t = n["entity_type"]
                noise_by_type.setdefault(t, []).append(n["text"])
            for t, examples in sorted(noise_by_type.items(), key=lambda x: -len(x[1])):
                report_lines.append(f"- **{t}** ({len(examples)} noise entities): {', '.join(examples[:10])}")
            report_lines.append("")

        # Emergent gaps summary
        if "emergent_gaps" in comparison:
            gaps = comparison["emergent_gaps"]
            report_lines.append(f"### Emergent Gaps: {gaps['count']} false negatives (missing from golden)")
            report_lines.append("")
            gap_by_type = {}
            for g in gaps["examples"]:
                t = g["entity_type"]
                gap_by_type.setdefault(t, []).append(g["text"])
            for t, examples in sorted(gap_by_type.items(), key=lambda x: -len(x[1])):
                report_lines.append(f"- **{t}** ({len(examples)} missed): {', '.join(examples[:10])}")
            report_lines.append("")

        # Strategy advantages
        for alt in ["kgspin_structural", "kgspin_base"]:
            wins_key = f"{alt}_wins_over_emergent"
            if wins_key in comparison:
                wins = comparison[wins_key]
                alt_label = pipeline_labels.get(alt, alt)
                report_lines.append(f"### {alt_label} Wins Over Emergent: {wins['count']} entities")
                report_lines.append("")
                if wins["examples"]:
                    for w in wins["examples"][:20]:
                        report_lines.append(f"- [{w['entity_type']}] {w['text']}")
                report_lines.append("")

        report_lines.append("---")
        report_lines.append("")

    # Entity overlap heatmap summary
    report_lines.append("## Cross-Strategy Entity Overlap (Jaccard)")
    report_lines.append("")
    report_lines.append("| Pair | Jaccard |")
    report_lines.append("|------|---------|")
    for ticker in tickers:
        if ticker not in all_comparisons:
            continue
        for pair, j in all_comparisons[ticker].get("entity_overlap", {}).items():
            p1, p2 = pair.split("_vs_")
            report_lines.append(f"| {ticker}: {pipeline_labels.get(p1, p1)} vs {pipeline_labels.get(p2, p2)} | {j} |")
    report_lines.append("")

    # Write report
    report_path = output_dir / "comparison_report.md"
    report_path.write_text("\n".join(report_lines))
    _log(f"Comparison report: {report_path}")

    # Write summary JSON
    (output_dir / "summary.json").write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tickers": tickers,
            "comparisons": all_comparisons,
        }, indent=2, default=str)
    )

    return all_comparisons


# ── Phase 5: VP Review ──────────────────────────────────────────────────────

def phase5_vp_review(output_dir):
    """Run VP Eng and VP Product reviews on the comparison report."""
    _banner("PHASE 5: VP Reviews")

    report_path = output_dir / "comparison_report.md"
    if not report_path.exists():
        _log("SKIP: No comparison report found for VP review", "WARN")
        return

    vp_review_script = PROJECT_ROOT / "scripts" / "agentic" / "vp-review.sh"
    if not vp_review_script.exists():
        _log(f"SKIP: VP review script not found at {vp_review_script}", "WARN")
        return

    reviews = [
        ("vp-eng", output_dir / "vp-eng-review.md"),
        ("vp-prod", output_dir / "vp-prod-review.md"),
    ]

    for persona, out_path in reviews:
        _log(f"Requesting {persona} review...")
        try:
            result = subprocess.run(
                [str(vp_review_script), persona, str(report_path), str(out_path)],
                capture_output=True, text=True, timeout=600,
                cwd=str(PROJECT_ROOT),
            )
            if result.returncode == 0 and out_path.exists():
                lines = len(out_path.read_text().splitlines())
                _log(f"  {persona} review written: {out_path} ({lines} lines)")
            else:
                _log(f"  {persona} review failed: {result.stderr[:200]}", "ERROR")
        except subprocess.TimeoutExpired:
            _log(f"  {persona} review timed out", "ERROR")
        except Exception as e:
            _log(f"  {persona} review error: {e}", "ERROR")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Overnight KG quality experiment — LLM golden data + KGSpin comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
    uv run python demos/extraction/run_overnight_experiment.py \\
        --tickers JNJ AAPL MSFT JPM NVDA \\
        2>&1 | tee docs/sprints/sprint-118/overnight-experiment-log.txt
        """,
    )
    parser.add_argument(
        "--tickers", nargs="+", default=["JNJ", "AAPL", "MSFT", "JPM", "NVDA"],
        help="Tickers to process (default: JNJ AAPL MSFT JPM NVDA)",
    )
    parser.add_argument(
        "--domain", default="financial-v12",
        help="Domain version (default: financial-v12)",
    )
    parser.add_argument(
        "--corpus-kb", type=int, default=0,
        help="Corpus size in KB (default: 0 = full document)",
    )
    parser.add_argument(
        "--model", default="gemini-2.5-flash",
        help="Gemini model for LLM pipelines (default: gemini-2.5-flash). "
             "Deprecated: prefer --llm-alias (ADR-002).",
    )
    parser.add_argument(
        "--llm-alias", dest="llm_alias", default=None,
        help="Admin-registered LLM alias id (ADR-002). Overrides --model "
             "for backend selection; --model is still used as a cache-key "
             "fallback when --llm-alias is unset.",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: docs/sprints/sprint-118/overnight-experiment/)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    parser.add_argument("--skip-llm", action="store_true", help="Skip Phase 1 (reuse existing LLM data)")
    parser.add_argument("--skip-golden", action="store_true", help="Skip Phase 2 (reuse existing golden data)")
    parser.add_argument("--skip-kgspin", action="store_true", help="Skip Phase 3 (reuse existing KGSpin data)")
    parser.add_argument("--skip-vp-review", action="store_true", help="Skip Phase 5 (VP reviews)")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers]
    domain_id = args.domain
    corpus_kb = args.corpus_kb
    model = args.model
    llm_alias = args.llm_alias

    output_dir = Path(args.output_dir) if args.output_dir else (
        PROJECT_ROOT / "docs" / "sprints" / "sprint-118" / "overnight-experiment"
    )

    _banner("OVERNIGHT KG QUALITY EXPERIMENT")
    _log(f"Tickers:   {', '.join(tickers)}")
    _log(f"Domain:    {domain_id}")
    _log(f"Corpus:    {'Full document' if corpus_kb == 0 else f'{corpus_kb}KB'}")
    _log(f"Model:     {model}")
    if llm_alias:
        _log(f"LLM Alias: {llm_alias} (ADR-002 — overrides --model)")
    _log(f"Output:    {output_dir}")
    _log("")

    phases = []
    if not args.skip_llm:
        phases.append(("Phase 1", "LLM Full Shot + Multi-Stage", f"{len(tickers)} tickers × 2 LLMs"))
    if not args.skip_golden:
        phases.append(("Phase 2", "Golden data via Gemini CLI", f"{len(tickers)} tickers"))
    if not args.skip_kgspin:
        phases.append(("Phase 3", "KGSpin Emergent/Structural/Base", f"{len(tickers)} tickers × 3 strategies"))
    phases.append(("Phase 4", "Cross-strategy comparison", "analysis"))
    if not args.skip_vp_review:
        phases.append(("Phase 5", "VP Eng + VP Prod reviews", "2 reviews"))

    _log("Phases:")
    for name, desc, scope in phases:
        _log(f"  {name}: {desc} ({scope})")

    est_minutes = 0
    if not args.skip_llm:
        est_minutes += len(tickers) * 2  # ~2 min per ticker for LLMs
    if not args.skip_golden:
        est_minutes += len(tickers) * 3  # ~3 min per ticker for Gemini CLI
    if not args.skip_kgspin:
        est_minutes += len(tickers) * 6  # ~2 min per strategy × 3 strategies
    est_minutes += 1  # comparison
    if not args.skip_vp_review:
        est_minutes += 5  # VP reviews
    _log(f"\nEstimated runtime: ~{est_minutes} minutes")

    if args.dry_run:
        _log("\nDRY RUN — no extraction will be performed")
        return 0

    # Validate domain
    try:
        resolve_domain_bundle_path(domain_id)
    except FileNotFoundError:
        _log(f"ERROR: Domain bundle '{domain_id}' not found", "ERROR")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save experiment config
    config = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tickers": tickers,
        "domain": domain_id,
        "corpus_kb": corpus_kb,
        "model": model,
        "phases": [{"name": n, "desc": d, "scope": s} for n, d, s in phases],
    }
    (output_dir / "config.json").write_text(json.dumps(config, indent=2))

    experiment_start = time.time()

    # Phase 1: LLM Extractions
    if not args.skip_llm:
        t0 = time.time()
        phase1_llm_extractions(tickers, domain_id, corpus_kb, model, output_dir, llm_alias=llm_alias)
        _log(f"Phase 1 complete: {_format_duration(time.time() - t0)}")

    # Phase 2: Golden Data
    if not args.skip_golden:
        t0 = time.time()
        phase2_golden_data(tickers, output_dir)
        _log(f"Phase 2 complete: {_format_duration(time.time() - t0)}")

    # Phase 3: KGSpin Extractions
    if not args.skip_kgspin:
        t0 = time.time()
        phase3_kgspin_extractions(tickers, domain_id, corpus_kb, output_dir)
        _log(f"Phase 3 complete: {_format_duration(time.time() - t0)}")

    # Phase 4: Comparison
    t0 = time.time()
    phase4_comparison(tickers, output_dir)
    _log(f"Phase 4 complete: {_format_duration(time.time() - t0)}")

    # Phase 5: VP Reviews
    if not args.skip_vp_review:
        t0 = time.time()
        phase5_vp_review(output_dir)
        _log(f"Phase 5 complete: {_format_duration(time.time() - t0)}")

    total = time.time() - experiment_start
    _banner(f"EXPERIMENT COMPLETE — {_format_duration(total)}")

    _log(f"Results in: {output_dir}")
    _log(f"Key files:")
    _log(f"  {output_dir / 'comparison_report.md'}  — Human-readable report")
    _log(f"  {output_dir / 'summary.json'}          — Machine-readable data")
    if (output_dir / "vp-eng-review.md").exists():
        _log(f"  {output_dir / 'vp-eng-review.md'}      — VP Eng evaluation")
    if (output_dir / "vp-prod-review.md").exists():
        _log(f"  {output_dir / 'vp-prod-review.md'}     — VP Prod evaluation")

    _log(f"\nPer-ticker data in: {output_dir}/{{TICKER}}/")
    _log(f"  golden_entities.json, golden_relationships.json")
    _log(f"  kgspin_emergent.json, kgspin_structural.json, kgspin_base.json")
    _log(f"  comparison.json")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
