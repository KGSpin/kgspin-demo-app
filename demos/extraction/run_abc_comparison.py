#!/usr/bin/env python3
"""
Run A/B/C module-level comparison: KGenSkills (A) vs LLM-Modular (B) vs Gemini (C).

Standalone script — no web server required. Prints diagnostic H-Score/L-Score
to terminal and saves full results to JSON.

Stage 0.5.4 (ADR-002): accepts ``--llm-alias`` to select an
admin-registered alias for the LLM-Modular pipeline; ``--model`` is retained
as deprecated compat.

Note: ``GeminiModularExtractor`` was removed in INIT-001 Sprint 03 along
with the demo-owned extractor classes; the Modular cache step now uses a
deterministic hash derived from the text + bundle version + model/alias
instead of a prompt-template hash.

Usage:
    uv run python demos/extraction/run_abc_comparison.py PFE
    uv run python demos/extraction/run_abc_comparison.py PFE --max-chunks 10
    uv run python demos/extraction/run_abc_comparison.py PFE --llm-alias gemini_flash
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_common import (
    BUNDLE_PATH,
    PATTERNS_PATH,
    resolve_ticker,
    html_to_text,
    strip_ixbrl,
    select_content_chunks,
)


def fetch_sec_text(ticker: str, max_chunks: int):
    """Fetch SEC 10-K and return (selected_text, company_name, bundle, raw_html, chunks)."""
    from kgenskills.execution.extractor import ExtractionBundle, DocumentChunker
    from kgenskills.data_sources.edgar import EdgarDataSource

    print(f"\n[1/5] Resolving ticker: {ticker}")
    info = resolve_ticker(ticker)
    company_name = info["name"]
    print(f"       Company: {company_name}")

    print(f"[2/5] Fetching SEC 10-K filing...")
    edgar = EdgarDataSource()
    doc = edgar.get_document(ticker, "10-K")
    if not doc:
        print(f"  ERROR: Could not fetch 10-K for {ticker}. Is EDGAR_IDENTITY set?")
        sys.exit(1)
    print(f"       Size: {len(doc.raw_html) // 1024}KB")

    print(f"[3/5] Parsing and chunking text...")
    bundle = ExtractionBundle.load(BUNDLE_PATH)
    text = html_to_text(strip_ixbrl(doc.raw_html))
    chunker = DocumentChunker(max_chunk_size=bundle.max_chunk_size)
    all_chunks = chunker.chunk(text, doc_id=f"{ticker}_10K")
    chunks = select_content_chunks(all_chunks, max_chunks)
    selected_text = "\n\n".join(c.text for c in chunks)
    total_chars = sum(len(c.text) for c in chunks)
    print(f"       {len(chunks)} chunks selected ({total_chars // 1000}K chars) of {len(all_chunks)} total")

    return selected_text, company_name, info, bundle, doc.raw_html


def main():
    parser = argparse.ArgumentParser(description="Run A/B/C module-level comparison")
    parser.add_argument("ticker", help="Stock ticker (e.g. PFE)")
    parser.add_argument("--max-chunks", type=int, default=10, help="Max chunks to process")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path")
    parser.add_argument(
        "--model", default=None,
        help="Gemini model string (deprecated — prefer --llm-alias; ADR-002).",
    )
    parser.add_argument(
        "--llm-alias", dest="llm_alias", default=None,
        help="Admin-registered LLM alias id (ADR-002) for System B.",
    )
    args = parser.parse_args()

    ticker = args.ticker.upper()
    print(f"=== KGenSkills A/B/C Diagnostic Comparison: {ticker} ===")

    # Fetch and prepare text
    selected_text, company_name, info, bundle, raw_html = fetch_sec_text(ticker, args.max_chunks)

    # Import the actual pipeline runners from demo_compare
    # (reuses exact same logic as the web UI)
    from demo_compare import (
        _run_kgenskills, _run_agentic_analyst, compute_diagnostic_scores,
        ModularRunLog, GeminiRunLog,
    )

    # Run System A (KGenSkills)
    print(f"\n[4a/5] Running KGenSkills (System A: GLiNER + Vector Fingerprints)...")
    t0 = time.time()

    def on_kgs_chunk(idx, total, entities_so_far):
        pct = idx * 100 // total
        print(f"       Chunk {idx}/{total} ({pct}%) — {entities_so_far} entities so far", end="\r")

    kgs_kg = _run_kgenskills(
        selected_text, company_name, ticker, bundle,
        on_chunk_complete=on_kgs_chunk, raw_html=raw_html,
    )
    kgs_elapsed = time.time() - t0
    n_ent = len(kgs_kg.get("entities", []))
    n_rel = len(kgs_kg.get("relationships", []))
    print(f"\n       Done: {n_ent} entities, {n_rel} relationships in {kgs_elapsed:.1f}s (0 tokens)")

    # Run System B (LLM-Modular)
    print(f"\n[4b/5] Running LLM-Modular (System B: 2-step Gemini)...")

    def on_mod_chunk(idx, total, tokens_so_far):
        pct = idx * 100 // total
        print(f"       Chunk {idx}/{total} ({pct}%) — {tokens_so_far:,} tokens so far", end="\r")

    mod_kg, h_tokens, l_tokens, mod_elapsed, _err_count = _run_agentic_analyst(
        selected_text, company_name, f"{ticker}_10K",
        on_chunk_complete=on_mod_chunk,
        chunk_size=args.max_chunks,
        model=None if args.llm_alias else args.model,
        llm_alias=args.llm_alias,
    )
    mod_n_ent = len(mod_kg.get("entities", []))
    mod_n_rel = len(mod_kg.get("relationships", []))
    print(f"\n       Done: {mod_n_ent} entities, {mod_n_rel} relationships in {mod_elapsed:.1f}s")
    print(f"       H-tokens (entity step): {h_tokens:,}")
    print(f"       L-tokens (linking step): {l_tokens:,}")
    print(f"       Total tokens: {h_tokens + l_tokens:,}")

    # Cache System B results to ModularRunLog (same cache the web UI uses).
    # ``GeminiModularExtractor`` was deleted in INIT-001 Sprint 03, so we
    # derive the prompt-version hash from the bundle version + the selected
    # model (or alias) rather than from an extractor-owned prompt template.
    _selector = args.llm_alias or args.model or "gemini-default"
    _prompt_version = hashlib.md5(
        f"{bundle.version}:{_selector}".encode()
    ).hexdigest()[:8]
    run_log = ModularRunLog()
    cfg_hash = run_log.config_hash(selected_text, bundle.version, _prompt_version)
    mod_model = mod_kg.get("provenance", {}).get("model", _selector)
    log_path = run_log.log_run(
        ticker, cfg_hash, mod_kg,
        h_tokens + l_tokens, mod_elapsed, mod_model,
    )
    print(f"       Cached to ModularRunLog: {log_path}")

    # Compute diagnostic scores
    print(f"\n[5/5] Computing pairwise diagnostic scores...")
    scores = compute_diagnostic_scores(kgs_kg, mod_kg=mod_kg, company_name=company_name)

    # Display results
    pair = scores["pairs"].get("kgs_vs_multistage", {})

    print(f"\n{'=' * 60}")
    print(f"  DIAGNOSTIC RESULTS: {ticker}")
    print(f"{'=' * 60}")
    print(f"")
    print(f"  Entity Overlap:        {pair.get('entity_overlap', 0)} shared, "
          f"{pair.get('a_only_entities', 0)} KGSpin-only, {pair.get('b_only_entities', 0)} LLM-only")
    print(f"  Relationship Overlap:  {pair.get('relationship_overlap', 0)} shared, "
          f"{pair.get('a_only_relationships', 0)} KGSpin-only, {pair.get('b_only_relationships', 0)} LLM-only")
    print(f"")
    print(f"  System A (KGenSkills):   {n_ent} entities, {n_rel} rels, 0 tokens, {kgs_elapsed:.1f}s")
    print(f"  System B (LLM-Modular):  {mod_n_ent} entities, {mod_n_rel} rels, {h_tokens + l_tokens:,} tokens, {mod_elapsed:.1f}s")
    print(f"")
    print(f"{'=' * 60}")

    # Save results
    output_path = args.output or f"demos/extraction/output/{ticker}_abc_comparison.json"
    output_file = PROJECT_ROOT / output_path
    output_file.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "ticker": ticker,
        "company_name": company_name,
        "max_chunks": args.max_chunks,
        "scores": scores,
        "system_a": {
            "name": "KGenSkills (GLiNER + Vector Fingerprints)",
            "entities": n_ent,
            "relationships": n_rel,
            "tokens": 0,
            "elapsed_seconds": round(kgs_elapsed, 2),
            "kg": kgs_kg,
        },
        "system_b": {
            "name": "LLM-Modular (2-step Gemini)",
            "entities": mod_n_ent,
            "relationships": mod_n_rel,
            "h_tokens": h_tokens,
            "l_tokens": l_tokens,
            "total_tokens": h_tokens + l_tokens,
            "elapsed_seconds": round(mod_elapsed, 2),
            "kg": mod_kg,
        },
    }

    output_file.write_text(json.dumps(result, default=str, indent=2))
    print(f"\nFull results saved to: {output_file}")


if __name__ == "__main__":
    main()
