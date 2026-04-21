#!/usr/bin/env python3
"""
Overnight batch extraction — runs all 5 pipelines across all tickers.

Pipelines:
    1. KGSpin Emergent   (deterministic, ~60-120s per ticker)
    2. KGSpin Structural (deterministic, ~60-120s per ticker)
    3. KGSpin Base       (deterministic, ~60-120s per ticker)
    4. LLM Full Shot     (Gemini API, ~10-30s per ticker)
    5. LLM Multi-Stage   (Gemini API, ~30-90s per ticker)

All results are saved to the demo run log (~/.kgenskills/logs/) so they
can be viewed and compared in the demo UI (demo_compare.py).

Usage:
    # All tickers, all pipelines, default domain (financial-v12)
    uv run python demos/extraction/run_overnight_batch.py

    # Specific tickers only
    uv run python demos/extraction/run_overnight_batch.py --tickers JNJ AAPL MSFT

    # Specific pipelines only
    uv run python demos/extraction/run_overnight_batch.py --pipelines emergent full_shot

    # Custom domain version
    uv run python demos/extraction/run_overnight_batch.py --domain financial-v11

    # Custom corpus size (KB)
    uv run python demos/extraction/run_overnight_batch.py --corpus-kb 500

    # Dry run (show what would run without executing)
    uv run python demos/extraction/run_overnight_batch.py --dry-run

    # Resume from a specific ticker (skip already-completed ones)
    uv run python demos/extraction/run_overnight_batch.py --resume-from GOOGL
"""

import argparse
import json
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
    DOMAIN_YAMLS_DIR,
    DOMAIN_BUNDLES_DIR,
    resolve_domain_bundle_path,
    resolve_domain_yaml_path,
    BUNDLE_PATH,
    PATTERNS_PATH,
)

# All available tickers with local EDGAR data
ALL_TICKERS = list(KNOWN_TICKERS.keys())

# Pipeline definitions — Wave 3 canonical names (hyphen form for admin).
KGSPIN_PIPELINES = ["fan-out", "discovery-rapid", "discovery-deep"]
LLM_PIPELINES = ["full_shot", "multi_stage"]
ALL_PIPELINES = KGSPIN_PIPELINES + LLM_PIPELINES


def _timestamp():
    return datetime.now().strftime("%H:%M:%S")


def _log(msg, level="INFO"):
    print(f"[{_timestamp()}] [{level}] {msg}", flush=True)


def _log_separator(char="=", width=72):
    print(char * width, flush=True)


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


def run_kgspin(
    ticker, company_name, pipeline_id, domain_id,
    corpus_kb, sec_doc, info,
):
    """Run a zero-LLM KGSpin pipeline (fan-out / discovery-rapid /
    discovery-deep) and save to run log. ``pipeline_id`` is the
    hyphenated admin pipeline config name.
    """
    from demo_compare import (
        _parse_and_chunk, _run_kgenskills, _split_bundle_id,
        _pipeline_ref_from_pipeline_id, _get_registry_client,
        KGenRunLog, DEMO_CACHE_VERSION,
    )

    # Parse & chunk — bundle is resolved from domain only; pipeline YAML
    # travels via pipeline_config_ref below.
    bundle, full_text, truncated_text, actual_kb, all_chunks = _parse_and_chunk(
        sec_doc.raw_html, ticker, corpus_kb=corpus_kb,
        bundle_name=domain_id,
    )

    # Extract
    t0 = time.time()
    kgs_kg = _run_kgenskills(
        truncated_text, company_name, ticker, bundle,
        _pipeline_ref_from_pipeline_id(pipeline_id),
        _get_registry_client(),
        raw_html=sec_doc.raw_html,
    )
    elapsed = time.time() - t0

    n_ent = len(kgs_kg.get("entities", []))
    n_rel = len(kgs_kg.get("relationships", []))

    # Save to run log with split cache key
    run_log = KGenRunLog()
    bid = _split_bundle_id(domain_id, pipeline_id)
    cfg_key = run_log.config_key("kgen", bid=bid, corpus_kb=corpus_kb)
    run_log.log_run(
        ticker, cfg_key, kgs_kg,
        total_tokens=0, elapsed_seconds=elapsed,
        model="kgen_deterministic",
        cache_version=DEMO_CACHE_VERSION,
        bundle_version=bid,
    )

    return {
        "entities": n_ent,
        "relationships": n_rel,
        "elapsed": elapsed,
        "actual_kb": actual_kb,
        "chunks": len(all_chunks),
        "cache_key": cfg_key,
    }


def run_llm_full_shot(
    ticker, company_name, domain_id,
    corpus_kb, sec_doc, model,
    llm_alias=None,
):
    """Run LLM Full Shot extraction and save to run log.

    Stage 0.5.4 (ADR-002): optional ``llm_alias`` selects an
    admin-registered alias. When set, the batch log's ``model`` field
    carries the alias id for provenance; when unset, the vendor/model
    string drives caching as before.
    """
    from demo_compare import (
        _parse_and_chunk, _run_agentic_flash,
        GeminiRunLog, DEMO_CACHE_VERSION,
        _prompt_version_hash,
    )

    # Parse & chunk — bundle only; pipeline YAML travels via the wrapper.
    bundle, full_text, truncated_text, actual_kb, all_chunks = _parse_and_chunk(
        sec_doc.raw_html, ticker, corpus_kb=corpus_kb,
        bundle_name=domain_id,
    )

    # Resolve domain-specific paths for the LLM prompt
    try:
        llm_bundle_path = resolve_domain_bundle_path(domain_id)
        llm_patterns_path = resolve_domain_yaml_path(domain_id)
    except FileNotFoundError:
        llm_bundle_path = BUNDLE_PATH
        llm_patterns_path = PATTERNS_PATH

    # Extract — alias selector wins over legacy `model=` string.
    t0 = time.time()
    kg, total_tokens, api_elapsed, error_count, truncated = _run_agentic_flash(
        truncated_text, company_name, f"{ticker}_10K",
        model=None if llm_alias else model,
        bundle_path=llm_bundle_path,
        patterns_path=llm_patterns_path,
        llm_alias=llm_alias,
    )
    elapsed = time.time() - t0

    n_ent = len(kg.get("entities", []))
    n_rel = len(kg.get("relationships", []))

    # Build cache key matching demo format. When an alias is in play the
    # alias id drives the cache key (different alias = different cache
    # slot) — the resolved model string is the wire identity but not
    # what the operator typed.
    cache_model = llm_alias or model
    pv = _prompt_version_hash(
        "gemini_extractor", "GeminiKGExtractor",
        llm_bundle_path, llm_patterns_path, cache_model,
    )
    run_log = GeminiRunLog()
    cfg_kwargs = {
        "corpus_kb": corpus_kb, "model": cache_model,
        "pv": pv, "cv": DEMO_CACHE_VERSION,
    }
    if domain_id:
        cfg_kwargs["dom"] = domain_id
    cfg_key = run_log.config_key("gemini", **cfg_kwargs)

    # Only save if we got real results
    if n_ent > 0 and not truncated:
        kg.setdefault("provenance", {})["corpus_kb"] = round(actual_kb, 1)
        run_log.log_run(
            ticker, cfg_key, kg,
            total_tokens=total_tokens, elapsed_seconds=elapsed,
            model=cache_model,
            cache_version=DEMO_CACHE_VERSION,
            bundle_version=domain_id,
        )

    return {
        "entities": n_ent,
        "relationships": n_rel,
        "elapsed": elapsed,
        "tokens": total_tokens,
        "errors": error_count,
        "truncated": truncated,
        "actual_kb": actual_kb,
        "cache_key": cfg_key,
    }


def run_llm_multi_stage(
    ticker, company_name, domain_id,
    corpus_kb, sec_doc, model, chunk_size=12,
    llm_alias=None,
):
    """Run LLM Multi-Stage extraction and save to run log.

    Stage 0.5.4 (ADR-002): optional ``llm_alias`` selects an
    admin-registered alias; see :func:`run_llm_full_shot`.
    """
    from demo_compare import (
        _parse_and_chunk, _run_agentic_analyst,
        ModularRunLog, DEMO_CACHE_VERSION,
        _prompt_version_hash,
    )

    # Parse & chunk — bundle only; pipeline YAML travels via the wrapper.
    bundle, full_text, truncated_text, actual_kb, all_chunks = _parse_and_chunk(
        sec_doc.raw_html, ticker, corpus_kb=corpus_kb,
        bundle_name=domain_id,
    )

    # Resolve domain-specific paths for the LLM prompt
    try:
        llm_bundle_path = resolve_domain_bundle_path(domain_id)
        llm_patterns_path = resolve_domain_yaml_path(domain_id)
    except FileNotFoundError:
        llm_bundle_path = BUNDLE_PATH
        llm_patterns_path = PATTERNS_PATH

    # Extract — alias selector wins over legacy `model=` string.
    t0 = time.time()
    kg, total_tokens, _, api_elapsed, error_count = _run_agentic_analyst(
        truncated_text, company_name, f"{ticker}_10K",
        chunk_size=chunk_size,
        model=None if llm_alias else model,
        bundle_path=llm_bundle_path,
        patterns_path=llm_patterns_path,
        llm_alias=llm_alias,
    )
    elapsed = time.time() - t0

    n_ent = len(kg.get("entities", []))
    n_rel = len(kg.get("relationships", []))

    # Build cache key matching demo format. Alias id drives the cache key
    # when present (ADR-002 Sprint 0.5.4).
    cache_model = llm_alias or model
    pv = _prompt_version_hash(
        "gemini_aligned_extractor", "GeminiAlignedExtractor",
        llm_bundle_path, llm_patterns_path, cache_model,
    )
    run_log = ModularRunLog()
    cfg_kwargs = {
        "corpus_kb": corpus_kb, "model": cache_model,
        "pv": pv, "cv": DEMO_CACHE_VERSION,
    }
    if domain_id:
        cfg_kwargs["dom"] = domain_id
    cfg_key = run_log.config_key("modular", **cfg_kwargs)

    # Only save if we got real results
    if n_ent > 0:
        kg.setdefault("provenance", {})["corpus_kb"] = round(actual_kb, 1)
        run_log.log_run(
            ticker, cfg_key, kg,
            total_tokens=total_tokens, elapsed_seconds=elapsed,
            model=cache_model,
            cache_version=DEMO_CACHE_VERSION,
            bundle_version=domain_id,
        )

    return {
        "entities": n_ent,
        "relationships": n_rel,
        "elapsed": elapsed,
        "tokens": total_tokens,
        "errors": error_count,
        "actual_kb": actual_kb,
        "cache_key": cfg_key,
    }


def fetch_sec_filing(ticker):
    """Fetch SEC 10-K filing (cached)."""
    from kgenskills.data_sources.edgar import EdgarDataSource
    edgar = EdgarDataSource()
    doc = edgar.get_document(ticker, "10-K")
    return doc


def main():
    parser = argparse.ArgumentParser(
        description="Overnight batch extraction — all pipelines × all tickers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help=f"Tickers to process (default: all {len(ALL_TICKERS)}). "
             f"Available: {', '.join(ALL_TICKERS)}",
    )
    parser.add_argument(
        "--pipelines", nargs="+", default=None,
        choices=ALL_PIPELINES,
        help="Pipelines to run (default: all 5). "
             "Options: emergent, structural, base, full_shot, multi_stage",
    )
    parser.add_argument(
        "--domain", default="financial-v12",
        help="Domain version to use (default: financial-v12)",
    )
    parser.add_argument(
        "--corpus-kb", type=int, default=200,
        help="Corpus size in KB (default: 200). Use 0 for full document.",
    )
    parser.add_argument(
        "--model", default="gemini-2.5-flash",
        help="Gemini model for LLM pipelines (default: gemini-2.5-flash). "
             "Deprecated: prefer --llm-alias (ADR-002).",
    )
    parser.add_argument(
        "--llm-alias", dest="llm_alias", default=None,
        help="Admin-registered LLM alias id (ADR-002). When set, --model is "
             "ignored for backend selection but still used as a cache-key fallback.",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=12,
        help="Macro-chunk count for Multi-Stage (default: 12)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would run without executing",
    )
    parser.add_argument(
        "--resume-from", default=None,
        help="Resume from this ticker (skip earlier ones)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Save results summary JSON to this path",
    )
    args = parser.parse_args()

    tickers = [t.upper() for t in (args.tickers or ALL_TICKERS)]
    pipelines = args.pipelines or ALL_PIPELINES
    domain_id = args.domain
    corpus_kb = args.corpus_kb
    model = args.model
    llm_alias = args.llm_alias

    # Resume support: skip tickers before resume_from
    if args.resume_from:
        resume = args.resume_from.upper()
        if resume in tickers:
            idx = tickers.index(resume)
            skipped = tickers[:idx]
            tickers = tickers[idx:]
            _log(f"Resuming from {resume}, skipping {len(skipped)}: {', '.join(skipped)}")
        else:
            _log(f"WARNING: --resume-from {resume} not in ticker list, running all", "WARN")

    # Compute total jobs
    total_jobs = len(tickers) * len(pipelines)
    kgspin_jobs = len(tickers) * len([p for p in pipelines if p in KGSPIN_PIPELINES])
    llm_jobs = len(tickers) * len([p for p in pipelines if p in LLM_PIPELINES])

    _log_separator()
    _log(f"OVERNIGHT BATCH EXTRACTION")
    _log_separator()
    _log(f"Tickers:    {len(tickers)} — {', '.join(tickers)}")
    _log(f"Pipelines:  {len(pipelines)} — {', '.join(pipelines)}")
    _log(f"Domain:     {domain_id}")
    _log(f"Corpus:     {corpus_kb}KB" if corpus_kb > 0 else "Corpus:     Full document")
    _log(f"LLM Model:  {model}")
    if llm_alias:
        _log(f"LLM Alias:  {llm_alias} (overrides --model for backend selection)")
    _log(f"Total jobs: {total_jobs} ({kgspin_jobs} KGSpin + {llm_jobs} LLM)")
    _log_separator()

    if args.dry_run:
        _log("DRY RUN — listing all jobs:")
        job_num = 0
        for ticker in tickers:
            for pipeline in pipelines:
                job_num += 1
                _log(f"  [{job_num:3d}/{total_jobs}] {ticker} × {pipeline}")
        _log(f"\nEstimated runtime: KGSpin ~{kgspin_jobs * 90}s + LLM ~{llm_jobs * 40}s")
        _log(f"Total: ~{_format_duration(kgspin_jobs * 90 + llm_jobs * 40)}")
        return

    # Validate domain bundle exists
    try:
        resolve_domain_bundle_path(domain_id)
    except FileNotFoundError:
        _log(f"ERROR: Domain bundle '{domain_id}' not found in .bundles/domains/", "ERROR")
        sys.exit(1)

    # Results tracking
    results = []
    errors = []
    batch_start = time.time()
    job_num = 0

    for ticker_idx, ticker in enumerate(tickers):
        _log_separator("-")
        _log(f"TICKER {ticker_idx + 1}/{len(tickers)}: {ticker}")
        _log_separator("-")

        # Step 1: Resolve ticker info
        info = resolve_ticker(ticker)
        company_name = info["name"]
        _log(f"Company: {company_name}")

        # Step 2: Fetch SEC 10-K (once per ticker, reused across pipelines)
        _log(f"Fetching SEC 10-K...")
        try:
            sec_doc = fetch_sec_filing(ticker)
            if not sec_doc:
                _log(f"SKIP: Could not fetch 10-K for {ticker}", "ERROR")
                for pipeline in pipelines:
                    job_num += 1
                    errors.append({
                        "ticker": ticker, "pipeline": pipeline,
                        "error": "Could not fetch 10-K filing",
                    })
                continue
            _log(f"10-K fetched: {len(sec_doc.raw_html) // 1024}KB raw HTML")
        except Exception as e:
            _log(f"SKIP: EDGAR fetch failed for {ticker}: {e}", "ERROR")
            for pipeline in pipelines:
                job_num += 1
                errors.append({
                    "ticker": ticker, "pipeline": pipeline,
                    "error": f"EDGAR fetch: {e}",
                })
            continue

        # Step 3: Run each pipeline
        for pipeline in pipelines:
            job_num += 1
            _log(f"[{job_num}/{total_jobs}] {ticker} × {pipeline}...")

            try:
                if pipeline in KGSPIN_PIPELINES:
                    result = run_kgspin(
                        ticker, company_name, pipeline, domain_id,
                        corpus_kb, sec_doc, info,
                    )
                elif pipeline == "full_shot":
                    result = run_llm_full_shot(
                        ticker, company_name, domain_id,
                        corpus_kb, sec_doc, model,
                        llm_alias=llm_alias,
                    )
                elif pipeline == "multi_stage":
                    result = run_llm_multi_stage(
                        ticker, company_name, domain_id,
                        corpus_kb, sec_doc, model, args.chunk_size,
                        llm_alias=llm_alias,
                    )
                else:
                    _log(f"  Unknown pipeline: {pipeline}", "WARN")
                    continue

                result["ticker"] = ticker
                result["pipeline"] = pipeline
                result["domain"] = domain_id
                results.append(result)

                _log(
                    f"  OK: {result['entities']} entities, "
                    f"{result['relationships']} rels, "
                    f"{_format_duration(result['elapsed'])}"
                    + (f", {result.get('tokens', 0)} tokens" if pipeline in LLM_PIPELINES else "")
                )

            except Exception as e:
                tb = traceback.format_exc()
                _log(f"  FAILED: {e}", "ERROR")
                _log(f"  {tb.splitlines()[-1]}", "ERROR")
                errors.append({
                    "ticker": ticker, "pipeline": pipeline,
                    "error": str(e),
                    "traceback": tb,
                })

        # Progress update after each ticker
        elapsed_total = time.time() - batch_start
        done_pct = job_num * 100 // total_jobs
        _log(f"Progress: {done_pct}% ({job_num}/{total_jobs}) — elapsed {_format_duration(elapsed_total)}")

    # Final summary
    batch_elapsed = time.time() - batch_start
    _log_separator("=")
    _log("BATCH COMPLETE")
    _log_separator("=")
    _log(f"Total time:  {_format_duration(batch_elapsed)}")
    _log(f"Successful:  {len(results)}/{total_jobs}")
    _log(f"Failed:      {len(errors)}/{total_jobs}")

    if results:
        _log("")
        _log("RESULTS SUMMARY:")
        _log(f"{'Ticker':<8} {'Pipeline':<14} {'Entities':>8} {'Rels':>6} {'Time':>8} {'Tokens':>8}")
        _log("-" * 60)
        for r in results:
            tokens_str = str(r.get("tokens", "")) if r.get("tokens") else ""
            _log(
                f"{r['ticker']:<8} {r['pipeline']:<14} "
                f"{r['entities']:>8} {r['relationships']:>6} "
                f"{_format_duration(r['elapsed']):>8} {tokens_str:>8}"
            )

    if errors:
        _log("")
        _log("ERRORS:")
        for e in errors:
            _log(f"  {e['ticker']} × {e['pipeline']}: {e['error']}", "ERROR")

    # Save results JSON
    output_path = args.output or str(
        PROJECT_ROOT / "docs" / "sprints" / "sprint-118"
        / f"batch-results-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    )
    summary = {
        "started_at": datetime.fromtimestamp(batch_start, timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(batch_elapsed, 1),
        "domain": domain_id,
        "corpus_kb": corpus_kb,
        "model": model,
        "llm_alias": llm_alias,
        "tickers": tickers,
        "pipelines": pipelines,
        "total_jobs": total_jobs,
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(summary, indent=2, default=str))
    _log(f"\nResults saved to: {output_path}")

    _log(f"\nAll results are in the demo run log. Start the demo UI to browse:")
    _log(f"  uv run python demos/extraction/demo_compare.py")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
