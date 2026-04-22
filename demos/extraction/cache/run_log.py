"""Disk-based run-log cache classes.

Each run-log persists one logical extraction result as a JSON file under
``~/.kgenskills/logs/<pipeline>/<DOC_ID>/<config_key>@<timestamp>.json``.

Wave A renamed the on-disk directory segment from ``{TICKER}`` to ``{DOC_ID}``
and bumped ``DEMO_CACHE_VERSION`` to ``5.0.0`` to invalidate pre-rename runs.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class GeminiRunLog:
    """Disk-based run log for Gemini KG extraction results.

    Wave A: all ``ticker`` parameters renamed to ``doc_id``; on-disk
    directory structure uses ``{DOC_ID}`` segments. A ``DEMO_CACHE_VERSION``
    bump forces all pre-Wave-A cached runs to be invalidated.

    Structure: ``~/.kgenskills/logs/gemini/{DOC_ID}/{config_key}@{timestamp}.json``
    Each file stores one complete extraction run.
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "gemini"
    MAX_RUNS = 20  # Max logged runs per doc_id+config

    def config_key(self, method: str, **kwargs) -> str:
        """Human-readable cache key from method name + args.

        Example: kgen_bv=1.2.3_corpus_kb=200_cv=2.4.0
        """
        parts = [method]
        for k, v in sorted(kwargs.items()):
            parts.append(f"{k}={v}")
        return "_".join(parts)

    def _run_dir(self, doc_id: str) -> Path:
        return self.LOG_ROOT / doc_id.upper()

    @staticmethod
    def _strip_bv(cfg_key: str) -> str:
        """Remove bv=... and pv=... segments for version-agnostic matching.

        Bundle version and prompt version changes should not invalidate
        cached LLM extraction runs — the results are still valid.
        Sprint 42.6: Also strip bs= and cv= for backward compat with old cached files.
        """
        key = re.sub(r'_?bv=[^_@]*', '', cfg_key)
        key = re.sub(r'_?pv=[^_@]*', '', key)
        key = re.sub(r'_?bs=[^_@]*', '', key)
        key = re.sub(r'_?cv=[^_@]*', '', key)
        return key

    def _run_files(self, doc_id: str, cfg_key: str) -> List[Path]:
        """Get all run files for doc_id+config, sorted newest first.

        Matches are bv- and pv-agnostic: a query for 'gemini_corpus_kb=200_cv=2.5.0'
        also matches old files with different bv= or pv= segments,
        so that bundle/prompt version changes don't invalidate the disk cache.
        """
        run_dir = self._run_dir(doc_id)
        if not run_dir.exists():
            return []
        normalized = self._strip_bv(cfg_key)
        files = [
            f for f in run_dir.glob("*.json")
            if self._strip_bv(f.stem.split("@", 1)[0]) == normalized
        ]
        files.sort(
            key=lambda p: p.stem.split("@", 1)[1] if "@" in p.stem else "",
            reverse=True,
        )
        return files

    def log_run(
        self,
        doc_id: str,
        cfg_hash: str,
        kg: dict,
        total_tokens: int,
        elapsed_seconds: float,
        model: str,
        analysis: Optional[dict] = None,
        cache_version: str = "",
        bundle_version: str = "",
    ) -> Optional[Path]:
        """Log a completed (or explicitly-failed) run to disk.

        Returns the file path, or None if the run was empty AND carried
        no explicit failure marker — that combination is the cache-
        pollution signature we guard against.
        """
        entity_count = len(kg.get("entities", []) or [])
        relationship_count = len(kg.get("relationships", []) or [])
        run_status = kg.get("status") or ""

        # Cache-pollution guard: zero ents, zero rels, AND no failure
        # marker → almost certainly a silently-swallowed error. Skip so
        # the UI's replay path doesn't render a blank slot.
        if (
            entity_count == 0
            and relationship_count == 0
            and run_status != "failed"
        ):
            logger.warning(
                "Refusing to log empty run (doc_id=%s cfg=%s model=%s "
                "elapsed=%.1fs tokens=%d) — likely an errored extraction. "
                "Check upstream traceback.",
                doc_id.upper(), cfg_hash, model, elapsed_seconds, total_tokens,
            )
            return None

        run_dir = self._run_dir(doc_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"{datetime.now(timezone.utc).microsecond:06d}Z"
        filename = f"{cfg_hash}@{ts}.json"
        filepath = run_dir / filename

        run_data = {
            "doc_id": doc_id.upper(),
            "config_key": cfg_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            "kg": kg,
            "analysis": analysis,
            "demo_cache_version": cache_version,
            "bundle_version": bundle_version,
        }

        filepath.write_text(json.dumps(run_data, default=str, indent=2))
        logger.info(f"Logged Gemini run: {filepath}")

        # Cleanup old runs beyond MAX_RUNS
        existing = self._run_files(doc_id, cfg_hash)
        if len(existing) > self.MAX_RUNS:
            for old_file in existing[self.MAX_RUNS:]:
                old_file.unlink(missing_ok=True)
                logger.info(f"Cleaned up old run: {old_file}")

        return filepath

    def update_run_analysis(
        self, doc_id: str, cfg_hash: str, index: int, analysis: dict
    ) -> None:
        """Update the analysis field of a logged run."""
        files = self._run_files(doc_id, cfg_hash)
        if index < 0 or index >= len(files):
            return
        filepath = files[index]
        run_data = json.loads(filepath.read_text())
        run_data["analysis"] = analysis
        filepath.write_text(json.dumps(run_data, default=str, indent=2))

    def list_runs(self, doc_id: str, cfg_hash: str) -> List[dict]:
        """List all logged runs for doc_id+config, sorted newest first."""
        files = self._run_files(doc_id, cfg_hash)
        runs = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                runs.append({
                    "path": str(f),
                    "created_at": data.get("created_at", ""),
                    "model": data.get("model", ""),
                    "total_tokens": data.get("total_tokens", 0),
                    "elapsed_seconds": data.get("elapsed_seconds", 0),
                    "entity_count": data.get("entity_count", 0),
                    "relationship_count": data.get("relationship_count", 0),
                    "has_analysis": data.get("analysis") is not None,
                })
            except Exception:
                continue
        return runs

    def load_run(self, doc_id: str, cfg_hash: str, index: int) -> Optional[dict]:
        """Load a specific run by index (0 = newest). Returns full run data."""
        files = self._run_files(doc_id, cfg_hash)
        if index < 0 or index >= len(files):
            return None
        try:
            return json.loads(files[index].read_text())
        except Exception:
            return None

    def latest(self, doc_id: str, cfg_hash: str) -> Optional[dict]:
        """Load the most recent run. Shortcut for load_run(..., 0)."""
        return self.load_run(doc_id, cfg_hash, 0)

    def count(self, doc_id: str, cfg_hash: str) -> int:
        """Number of logged runs for this doc_id+config."""
        return len(self._run_files(doc_id, cfg_hash))


# Singleton run logs
_run_log = GeminiRunLog()


class ModularRunLog(GeminiRunLog):
    """Disk-based run log for LLM Multi-Stage extraction results.

    Separate namespace from Full Shot logs: ~/.kgenskills/logs/modular/{DOC_ID}/
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "modular"


_modular_run_log = ModularRunLog()


class KGenRunLog(GeminiRunLog):
    """Disk-based run log for KGSpin extraction results.

    Sprint 33.10: Separate namespace so page refreshes don't re-run the 430s
    deterministic pipeline. ~/.kgenskills/logs/kgen/{DOC_ID}/
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "kgen"

    @staticmethod
    def _strip_bv(cfg_key: str) -> str:
        """Strip bv=, pv=, bs=, cv= for cache matching.

        KGen cache keys by ticker+size only. Old cached files with bv= in
        their filenames still need to match the new key (without bv=).
        """
        key = re.sub(r'_?bv=[^_@]*', '', cfg_key)
        key = re.sub(r'_?pv=[^_@]*', '', key)
        key = re.sub(r'_?bs=[^_@]*', '', key)
        key = re.sub(r'_?cv=[^_@]*', '', key)
        return key


_kgen_run_log = KGenRunLog()


class IntelRunLog(GeminiRunLog):
    """Sprint 33.17: Disk-based run log for Intelligence pipeline results.

    ~/.kgenskills/logs/intel/{DOC_ID}/
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "intel"


_intel_run_log = IntelRunLog()


class ImpactQARunLog(GeminiRunLog):
    """Disk-based run log for Impact Q&A comparison results.

    Wave A: ``~/.kgenskills/logs/impact_qa/{DOC_ID}/`` (was ``{DOC_ID}``).
    Each file stores one complete Q&A run (all questions + summary +
    quality analysis).
    """

    LOG_ROOT = Path.home() / ".kgenskills" / "logs" / "impact_qa"

    def log_qa_run(
        self,
        doc_id: str,
        results: list,
        summary: dict,
        quality_analysis: dict | None,
        elapsed_seconds: float,
    ) -> Path:
        """Log a completed Q&A comparison run to disk."""
        doc_id = doc_id.upper()
        run_dir = self._run_dir(doc_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        ts = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + f"{datetime.now(timezone.utc).microsecond:06d}Z"
        )
        cfg_key = "impact_qa"
        filename = f"{cfg_key}@{ts}.json"
        filepath = run_dir / filename

        total_tokens = sum(
            r.get("tokens_with", 0) + r.get("tokens_without", 0) for r in results
        )

        run_data = {
            "doc_id": doc_id,
            "config_key": cfg_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": "gemini",
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "questions_count": len(results),
            "results": results,
            "summary": summary,
            "quality_analysis": quality_analysis,
        }

        filepath.write_text(json.dumps(run_data, default=str, indent=2))
        logger.info(f"Logged impact Q&A run: {filepath}")

        # Cleanup old runs beyond MAX_RUNS
        existing = self._run_files(doc_id, cfg_key)
        if len(existing) > self.MAX_RUNS:
            for old_file in existing[self.MAX_RUNS:]:
                old_file.unlink(missing_ok=True)

        return filepath


_impact_qa_run_log = ImpactQARunLog()
