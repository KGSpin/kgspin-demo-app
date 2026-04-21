#!/usr/bin/env python3
"""
Quick KGenSkills-only extraction for smell testing.

Runs the production KGSpin pipeline on a ticker and saves the result
to the KGen run log cache (~/.kgenskills/logs/kgen/{TICKER}/) so it
can be compared side-by-side in demo_compare.py.

Uses the same _parse_and_chunk logic as demo_compare.py so the cache
key matches (corpus_kb=200 by default).

Usage:
    uv run python demos/extraction/run_kgen_smell_test.py JNJ
    uv run python demos/extraction/run_kgen_smell_test.py JNJ --corpus-kb 500
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_common import resolve_ticker


def main():
    parser = argparse.ArgumentParser(description="Run KGenSkills extraction (smell test)")
    parser.add_argument("ticker", help="Stock ticker (e.g. JNJ)")
    parser.add_argument("--corpus-kb", type=int, default=200, help="Corpus size in KB (default: 200)")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    corpus_kb = args.corpus_kb
    print(f"=== KGenSkills Smell Test: {ticker} (corpus_kb={corpus_kb}) ===\n")

    # Step 1: Resolve ticker
    print(f"[1/4] Resolving ticker: {ticker}")
    info = resolve_ticker(ticker)
    company_name = info["name"]
    print(f"       Company: {company_name}")

    # Step 2: Fetch SEC 10-K
    from kgenskills.data_sources.edgar import EdgarDataSource

    print(f"[2/4] Fetching SEC 10-K filing...")
    edgar = EdgarDataSource()
    doc = edgar.get_document(ticker, "10-K")
    if not doc:
        print(f"  ERROR: Could not fetch 10-K for {ticker}. Is EDGAR_IDENTITY set?")
        sys.exit(1)
    print(f"       Raw HTML size: {len(doc.raw_html) // 1024}KB")

    # Step 3: Parse, chunk (same as demo_compare.py), extract
    print(f"[3/4] Parsing ({corpus_kb}KB), chunking, and extracting...")
    from demo_compare import (
        _parse_and_chunk, _run_kgenskills,
        KGenRunLog, DEMO_CACHE_VERSION,
    )

    bundle, full_text, truncated_text, actual_kb, all_chunks = _parse_and_chunk(
        doc.raw_html, ticker, corpus_kb=corpus_kb,
    )
    print(f"       Actual corpus: {actual_kb:.0f}KB, {len(all_chunks)} chunks")

    def on_chunk(idx, total, entities_so_far):
        pct = idx * 100 // total
        print(f"       Chunk {idx}/{total} ({pct}%) — {entities_so_far} entities so far", end="\r")

    t0 = time.time()
    kgs_kg = _run_kgenskills(
        truncated_text, company_name, ticker, bundle,
        on_chunk_complete=on_chunk, raw_html=doc.raw_html,
    )
    elapsed = time.time() - t0

    n_ent = len(kgs_kg.get("entities", []))
    n_rel = len(kgs_kg.get("relationships", []))
    print(f"\n       Done: {n_ent} entities, {n_rel} relationships in {elapsed:.1f}s")

    # Step 4: Cache to KGenRunLog (key matches demo_compare.py)
    print(f"[4/4] Saving to compare demo cache...")
    run_log = KGenRunLog()
    cfg_hash = run_log.config_key("kgen", corpus_kb=corpus_kb)

    def _bundle_id(b):
        return Path(getattr(b, 'path', '') or '').name or "unknown"

    bv = _bundle_id(bundle)
    log_path = run_log.log_run(
        ticker, cfg_hash, kgs_kg,
        total_tokens=0, elapsed_seconds=elapsed,
        model="kgen_deterministic",
        cache_version=DEMO_CACHE_VERSION,
        bundle_version=bv,
    )
    total_runs = run_log.count(ticker, cfg_hash)
    print(f"       Cache key: {cfg_hash}")
    print(f"       Cached to: {log_path}")
    print(f"       Total cached runs for this config: {total_runs}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  SMELL TEST RESULTS: {ticker}")
    print(f"{'=' * 60}")
    print(f"  Entities:      {n_ent}")
    print(f"  Relationships: {n_rel}")
    print(f"  Duration:      {elapsed:.1f}s")
    print(f"  Corpus:        {actual_kb:.0f}KB ({len(all_chunks)} chunks)")
    print(f"  Bundle:        {bv}")
    print(f"{'=' * 60}")

    # Top entities (field is 'text' in to_dict() output)
    entities = kgs_kg.get("entities", [])
    if entities:
        print(f"\n  Top 30 entities:")
        for e in entities[:30]:
            etype = e.get("entity_type", "?")
            name = e.get("text", e.get("normalized_text", "?"))
            print(f"    [{etype}] {name}")

    # Top relationships (fields are subject/predicate/object dicts)
    rels = kgs_kg.get("relationships", [])
    if rels:
        print(f"\n  All {len(rels)} relationships:")
        for r in rels:
            subj = r.get("subject", {})
            obj = r.get("object", {})
            src = subj.get("text", "?") if isinstance(subj, dict) else subj
            tgt = obj.get("text", "?") if isinstance(obj, dict) else obj
            rtype = r.get("predicate", "?")
            print(f"    {src} --[{rtype}]--> {tgt}")

    print(f"\n  Ready for compare demo: open demo_compare.py and select {ticker}")


if __name__ == "__main__":
    main()
