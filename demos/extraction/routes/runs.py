"""Run-history endpoints: list + detail for each pipeline's RunLog.

Thin JSON handlers that read from the disk-backed ``*RunLog`` classes.
The per-run `vis` rendering is delegated to ``demo_compare.build_vis_data``
via a function-local import to keep this module decoupled from the
bigger orchestrators.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# Phase 2 INSTALLATION (CTO 2026-04-26) — wire shape constant for the
# triple-hash placeholder shown on legacy cached runs that pre-date the
# Phase 2 surfacing landing. No data migration; honest signal that the
# fields were not recorded at extraction time.
_PRE_PHASE_2 = "<pre-Phase-2>"


def _extraction_metadata_from_kg(kg: dict) -> dict:
    """Lift the triple-hash from a cached run's ``kg.provenance`` block.

    Cached runs predating Phase 2 lack the three hash fields entirely;
    we render a ``<pre-Phase-2>`` placeholder so the UI doesn't show
    empty strings (which look like a bug). Live runs (post-Phase-2)
    pass the orchestrator's stamped values through verbatim.

    Reads new-canonical names first then falls back to legacy names
    (sprint-domain-model-contracts-20260430 rename). Cached run JSON
    files written before the rename carry the old names; new files
    will carry the new names. Both are honored without a data
    migration.
    """
    prov = kg.get("provenance", {}) if isinstance(kg, dict) else {}

    def _lift(new_name: str, old_name: str) -> str | None:
        value = prov.get(new_name)
        if value is None:
            value = prov.get(old_name)
            if value is None:
                return _PRE_PHASE_2
        return value if value else _PRE_PHASE_2

    return {
        "schema_version": prov.get("schema_version", 1),
        "core_code_hash": _lift("core_code_hash", "pipeline_version_hash"),
        "bundle_yaml_hash": _lift("bundle_yaml_hash", "bundle_version_hash"),
        "installation_yaml_hash": _lift("installation_yaml_hash", "installation_version_hash"),
    }

from cache.run_log import (
    GeminiRunLog,
    _impact_qa_run_log,
    _intel_run_log,
    _kgen_run_log,
    _modular_run_log,
    _run_log,
)

router = APIRouter()


def _sort_run_files_by_timestamp(files):
    """Sort run log files by timestamp (newest first), not by full filename.

    Filenames are {config_key}@{timestamp}.json. Sorting by full filename
    breaks when config keys differ (e.g., corpus_kb=500 sorts before
    corpus_kb=200 lexicographically, regardless of actual timestamps).
    """
    return sorted(
        files,
        key=lambda p: p.stem.split("@", 1)[1] if "@" in p.stem else "",
        reverse=True,
    )


def _latest_config_key(runs_by_key: dict) -> str:
    """Pick the config key group with the most recent timestamp."""
    return max(
        runs_by_key,
        key=lambda h: max(
            (f.stem.split("@", 1)[1] for f in runs_by_key[h] if "@" in f.stem),
            default="",
        ),
    )


def _vis(kg: dict) -> dict:
    # Lazy import: demo_compare.build_vis_data still lives in the god file.
    from demo_compare import build_vis_data
    return build_vis_data(kg)


def _sync_kg_cache(ticker: str, field: str, kg: dict) -> None:
    """PRD-048: sync in-memory KG cache so /api/scores reflects viewed run."""
    from demo_compare import _cache_lock, _kg_cache
    with _cache_lock:
        if ticker in _kg_cache:
            _kg_cache[ticker][field] = kg


def _resolve_total_tokens(run_data: dict) -> int:
    """Surface the LLM token count for an LLM-pipeline run record.

    Reads top-level ``total_tokens`` first; falls back to
    ``kg.provenance.tokens_used`` (post-2026-04-21 Phase 2 surface) and
    then to ``kg.provenance.total_tokens`` (legacy field on pre-Phase-2
    cached runs). Returns 0 only when no source has a value — KGSpin
    pipelines (zero LLM calls) legitimately resolve to 0 here.
    """
    if not isinstance(run_data, dict):
        return 0
    top = run_data.get("total_tokens") or 0
    if top:
        return int(top)
    prov = run_data.get("kg", {}).get("provenance", {}) if isinstance(run_data.get("kg"), dict) else {}
    if not isinstance(prov, dict):
        return 0
    return int(prov.get("tokens_used") or prov.get("total_tokens") or 0)


@router.get("/api/gemini-runs/{doc_id}")
async def gemini_runs(doc_id: str):
    """List all logged Gemini runs for a ticker (across all config hashes)."""
    ticker = doc_id.upper()
    run_dir = _run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = parts[0]
            runs_by_key.setdefault(cfg, []).append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@router.get("/api/gemini-runs/{doc_id}/{index}")
async def gemini_run_detail(doc_id: str, index: int):
    """Load a specific Gemini run by index. Includes pre-built vis data."""
    ticker = doc_id.upper()

    run_dir = _run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    cfg_key = all_files[0].stem.split("@", 1)[0]
    run_data = _run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = _vis(kg)
    total = _run_log.count(ticker, cfg_key)

    elapsed_s = run_data.get("elapsed_seconds", 0)
    provenance = kg.get("provenance", {})
    error_count = provenance.get("error_count", 0)
    text_kb = provenance.get("corpus_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None

    _sync_kg_cache(ticker, "gem_kg", kg)

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": run_data.get("entity_count", 0),
            "relationships": run_data.get("relationship_count", 0),
            "tokens": _resolve_total_tokens(run_data),
            "duration_ms": int(elapsed_s * 1000),
            "errors": error_count,
            "throughput_kb_sec": round(throughput, 1) if throughput else None,
            "actual_kb": round(text_kb, 1) if text_kb else None,
        },
        "analysis": run_data.get("analysis"),
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", ""),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
        "extraction_metadata": _extraction_metadata_from_kg(kg),
    })


@router.get("/api/modular-runs/{doc_id}")
async def modular_runs(doc_id: str):
    """List all logged LLM Multi-Stage runs for a ticker."""
    ticker = doc_id.upper()
    run_dir = _modular_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = parts[0]
            runs_by_key.setdefault(cfg, []).append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _modular_run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@router.get("/api/modular-runs/{doc_id}/{index}")
async def modular_run_detail(doc_id: str, index: int):
    """Load a specific LLM Multi-Stage run by index. Includes pre-built vis data."""
    ticker = doc_id.upper()

    run_dir = _modular_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    cfg_key = all_files[0].stem.split("@", 1)[0]
    run_data = _modular_run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = _vis(kg)
    total = _modular_run_log.count(ticker, cfg_key)

    elapsed_s = run_data.get("elapsed_seconds", 0)
    provenance = kg.get("provenance", {})
    error_count = provenance.get("error_count", 0)
    text_kb = provenance.get("corpus_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None

    _sync_kg_cache(ticker, "mod_kg", kg)

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": run_data.get("entity_count", 0),
            "relationships": run_data.get("relationship_count", 0),
            "tokens": _resolve_total_tokens(run_data),
            "duration_ms": int(elapsed_s * 1000),
            "errors": error_count,
            "throughput_kb_sec": round(throughput, 1) if throughput else None,
            "actual_kb": round(text_kb, 1) if text_kb else None,
            "chunks_total": run_data.get("chunks_total", 0),
        },
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", ""),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
        "extraction_metadata": _extraction_metadata_from_kg(kg),
    })


@router.get("/api/kgen-runs/{doc_id}")
async def kgen_runs(doc_id: str):
    """List all logged KGSpin runs for a ticker."""
    ticker = doc_id.upper()
    run_dir = _kgen_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = GeminiRunLog._strip_bv(parts[0])
            runs_by_key.setdefault(cfg, []).append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _kgen_run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@router.get("/api/kgen-runs/{doc_id}/{index}")
async def kgen_run_detail(doc_id: str, index: int):
    """Load a specific KGSpin run by index. Includes pre-built vis data."""
    from demo_compare import _CPU_COST_PER_HOUR, DEFAULT_CHUNK_SIZE

    ticker = doc_id.upper()

    run_dir = _kgen_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    cfg_key = GeminiRunLog._strip_bv(all_files[0].stem.split("@", 1)[0])
    run_data = _kgen_run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = _vis(kg)
    total = _kgen_run_log.count(ticker, cfg_key)

    elapsed_s = run_data.get("elapsed_seconds", 0)
    cpu_cost = (elapsed_s / 3600) * _CPU_COST_PER_HOUR
    text_kb = kg.get("provenance", {}).get("corpus_kb", 0)
    throughput = text_kb / elapsed_s if elapsed_s > 0 and text_kb > 0 else None
    est_chunks = max(1, round(text_kb * 1024 / DEFAULT_CHUNK_SIZE)) if text_kb > 0 else 0

    _sync_kg_cache(ticker, "kgs_kg", kg)

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": len(vis["nodes"]),
            "relationships": len(vis["edges"]),
            "tokens": 0,
            "duration_ms": int(elapsed_s * 1000),
            "throughput_kb_sec": round(throughput, 1) if throughput else None,
            "actual_kb": round(text_kb, 1) if text_kb else None,
            "cpu_cost": round(cpu_cost, 6),
            "num_chunks": est_chunks,
        },
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", "kgen_deterministic"),
        "bundle_version": run_data.get("bundle_version", kg.get("provenance", {}).get("bundle_version", "1.0")),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
        "extraction_metadata": _extraction_metadata_from_kg(kg),
    })


@router.get("/api/intel-runs/{doc_id}")
async def intel_runs(doc_id: str):
    """List all logged Intelligence pipeline runs for a ticker."""
    ticker = doc_id.upper()
    run_dir = _intel_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"runs": [], "total": 0})

    all_files = list(run_dir.glob("*.json"))
    runs_by_key: dict = {}
    for f in all_files:
        parts = f.stem.split("@", 1)
        if len(parts) == 2:
            cfg = parts[0]
            runs_by_key.setdefault(cfg, []).append(f)

    if not runs_by_key:
        return JSONResponse({"runs": [], "total": 0})

    latest_key = _latest_config_key(runs_by_key)
    runs = _intel_run_log.list_runs(ticker, latest_key)

    return JSONResponse({
        "runs": runs,
        "total": len(runs),
        "config_key": latest_key,
    })


@router.get("/api/intel-runs/{doc_id}/{index}")
async def intel_run_detail(doc_id: str, index: int):
    """Load a specific Intelligence pipeline run by index."""
    ticker = doc_id.upper()

    run_dir = _intel_run_log._run_dir(ticker)
    if not run_dir.exists():
        return JSONResponse({"error": "No runs found"}, status_code=404)

    all_files = _sort_run_files_by_timestamp(run_dir.glob("*.json"))
    if not all_files:
        return JSONResponse({"error": "No runs found"}, status_code=404)

    cfg_key = all_files[0].stem.split("@", 1)[0]
    run_data = _intel_run_log.load_run(ticker, cfg_key, index)

    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)

    kg = run_data.get("kg", {})
    vis = _vis(kg)
    total = _intel_run_log.count(ticker, cfg_key)

    elapsed_s = run_data.get("elapsed_seconds", 0)

    return JSONResponse({
        "kg": kg,
        "vis": vis,
        "stats": {
            "entities": len(vis["nodes"]),
            "relationships": len(vis["edges"]),
            "tokens": 0,
            "duration_ms": int(elapsed_s * 1000),
        },
        "analysis": run_data.get("analysis"),
        "created_at": run_data.get("created_at", ""),
        "model": run_data.get("model", ""),
        "run_index": index,
        "total_runs": total,
        "config_key": cfg_key,
        "extraction_metadata": _extraction_metadata_from_kg(kg),
    })


@router.get("/api/impact-qa-runs/{doc_id}")
async def impact_qa_runs(doc_id: str):
    """List all logged Impact Q&A runs for a ticker."""
    ticker = doc_id.upper()
    cfg_key = "impact_qa"
    runs = _impact_qa_run_log.list_runs(ticker, cfg_key)
    return JSONResponse({"runs": runs, "total": len(runs), "config_key": cfg_key})


@router.get("/api/impact-qa-runs/{doc_id}/{index}")
async def impact_qa_run_detail(doc_id: str, index: int):
    """Load a specific Impact Q&A run by index (0 = newest)."""
    ticker = doc_id.upper()
    cfg_key = "impact_qa"
    run_data = _impact_qa_run_log.load_run(ticker, cfg_key, index)
    if not run_data:
        return JSONResponse({"error": f"Run index {index} not found"}, status_code=404)
    total = _impact_qa_run_log.count(ticker, cfg_key)
    return JSONResponse({
        "results": run_data.get("results", []),
        "summary": run_data.get("summary", {}),
        "quality_analysis": run_data.get("quality_analysis"),
        "created_at": run_data.get("created_at", ""),
        "elapsed_seconds": run_data.get("elapsed_seconds", 0),
        "total_tokens": run_data.get("total_tokens", 0),
        "run_index": index,
        "total_runs": total,
    })
