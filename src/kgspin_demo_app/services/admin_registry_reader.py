"""Cached, circuit-breaker-wrapped admin registry reads for demo UI.

Sprint 12 Task 3 + Task 4 (VP Eng condition — cache resilience):
Demo's pipeline + bundle dropdowns read from admin at request time.
To keep the UI responsive under a flapping or slow admin, every read
goes through:

- A **per-request cache** with a short TTL (default 2s) so repeat
  calls within one request burst hit admin once.
- A **circuit-breaker** that trips after N consecutive failures in a
  rolling window. While tripped, reads return the last-known-good
  snapshot (or the empty state if no snapshot exists) without
  re-attempting admin until the cooldown expires.

Per VP Prod 2026-04-19 review, empty-state messages use the softer
copy: ``"No pipelines available — ask your admin to register them."``

Design notes:

- Single-process FastAPI demo. State is a module-level singleton
  guarded by ``threading.Lock``. Multi-worker deployments would need
  to move this into an external cache (Redis, etc.); flagged as a
  Sprint 13+ follow-up.
- Cached payload is the demo-side UI-slot dict shape, not admin's
  raw ``Resource`` objects. Translation happens once per refresh so
  downstream consumers don't re-translate.
- Fallback to a seed YAML file is the VP-Prod-authorized transitional
  path for Sprint 12 while kgspin-archetypes registers the seed
  pipeline_configs. See
  ``docs/handovers/2026-04-20-archetypes-team-seed-content.md``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kgspin_interface.registry_client import (
    ResourceKind,
    ResourceRegistryClient,
)


logger = logging.getLogger(__name__)

# --- Tunables --------------------------------------------------------------

CACHE_TTL_SECONDS: float = 2.0
FAILURE_THRESHOLD: int = 3
BREAKER_COOLDOWN_SECONDS: float = 60.0


# --- State types -----------------------------------------------------------


@dataclass
class CircuitBreaker:
    """Two-state breaker: NORMAL → TRIPPED → NORMAL on cooldown expiry.

    Fields are public so tests can inspect + drive state directly.
    Not thread-safe on its own; callers hold an external lock.
    """

    failure_count: int = 0
    tripped_until: float = 0.0  # monotonic; 0 means not tripped

    def record_success(self) -> None:
        self.failure_count = 0
        self.tripped_until = 0.0

    def record_failure(self, now: float) -> None:
        self.failure_count += 1
        if self.failure_count >= FAILURE_THRESHOLD:
            self.tripped_until = now + BREAKER_COOLDOWN_SECONDS
            logger.warning(
                "[ADMIN_READER] circuit breaker TRIPPED for %.0fs after %d "
                "consecutive failures",
                BREAKER_COOLDOWN_SECONDS, self.failure_count,
            )

    def is_tripped(self, now: float) -> bool:
        if self.tripped_until == 0.0:
            return False
        if now >= self.tripped_until:
            # Cooldown expired — reset to NORMAL state before next attempt.
            self.failure_count = 0
            self.tripped_until = 0.0
            logger.info("[ADMIN_READER] circuit breaker cooldown elapsed; resuming")
            return False
        return True


@dataclass
class _CacheEntry:
    """Last-known-good payload + timestamp."""

    payload: list[dict[str, Any]]
    last_refresh: float = 0.0
    has_snapshot: bool = False


# --- Module-level state (singleton per-process) ----------------------------


_pipelines_lock = threading.Lock()
_pipelines_cache = _CacheEntry(payload=[])
_pipelines_breaker = CircuitBreaker()

_bundles_lock = threading.Lock()
_bundles_cache = _CacheEntry(payload=[])
_bundles_breaker = CircuitBreaker()


# --- Public API ------------------------------------------------------------


def list_pipeline_configs(
    client: ResourceRegistryClient,
    *,
    seed_fallback_path: Path | None = None,
    now: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    """Return UI-slot-shaped pipeline dicts, admin-first with fallback.

    Cache-hit path: return the cached payload if < ``CACHE_TTL_SECONDS``
    old. Breaker-tripped path: return the last-known-good snapshot.
    Otherwise: query admin, translate to UI-slot shape, cache, return.

    ``seed_fallback_path`` points at a Sprint-12 transitional YAML file
    used when admin returns empty (archetypes hasn't registered the
    seed pipeline_configs yet). When archetypes ships, the fallback is
    no longer exercised but stays in place until Sprint 13 cleanup.
    """
    return _read_with_fallback(
        client,
        kind=ResourceKind.PIPELINE_CONFIG,
        translate=_pipeline_metadata_to_ui_slot,
        seed_fallback_path=seed_fallback_path,
        now=now,
        cache=_pipelines_cache,
        breaker=_pipelines_breaker,
        lock=_pipelines_lock,
    )


def list_bundle_configs(
    client: ResourceRegistryClient,
    *,
    domain: str | None = None,
    seed_fallback_path: Path | None = None,
    now: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    """Return UI-dropdown-shaped bundle dicts, admin-first with fallback.

    Same resilience contract as ``list_pipeline_configs``. Optional
    ``domain`` filter narrows to bundles whose metadata includes the
    matching domain tag; unfiltered returns all registered bundles.
    """
    entries = _read_with_fallback(
        client,
        kind=ResourceKind.BUNDLE_COMPILED,
        translate=_bundle_metadata_to_ui_entry,
        seed_fallback_path=seed_fallback_path,
        now=now,
        cache=_bundles_cache,
        breaker=_bundles_breaker,
        lock=_bundles_lock,
    )
    if domain:
        entries = [e for e in entries if e.get("domain") == domain]
    return entries


def get_prompt_template_text(
    client: ResourceRegistryClient,
    name: str,
    *,
    fallback: str = "",
    version: str | None = None,
) -> str:
    """Sprint 12 Task 7: resolve a prompt_template by name → text.

    Looks up the prompt_template in admin's registry, resolves its
    pointer, and returns the text bytes as a UTF-8 string. Returns
    ``fallback`` on any failure path (admin down, prompt missing,
    pointer unresolvable, invalid UTF-8) — callers decide whether
    empty-fallback means degraded-but-usable or hard-error.

    ``version`` pins a specific version; omit to take the highest
    version available (deterministic by lexicographic sort).

    Not cached — prompts can change mid-sprint by archetype edit; the
    per-request call pays the admin latency but ensures demo reflects
    edits immediately. Circuit-breaker still applies via the
    underlying ``client.list()`` call semantics.
    """
    try:
        resources = client.list(ResourceKind.PROMPT_TEMPLATE)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[PROMPT_TEMPLATE] admin list failed (%s: %s); using fallback for %r",
            type(e).__name__, str(e)[:100], name,
        )
        return fallback

    matches = [
        r for r in resources
        if (r.metadata or {}).get("name") == name
        and (version is None or (r.metadata or {}).get("version") == version)
    ]
    if not matches:
        return fallback
    matches.sort(key=lambda r: (r.metadata or {}).get("version", ""), reverse=True)
    resource = matches[0]

    try:
        pointer = client.resolve_pointer(resource.id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[PROMPT_TEMPLATE] resolve_pointer failed for %r: %s: %s",
            resource.id, type(e).__name__, str(e)[:100],
        )
        return fallback
    if pointer is None:
        return fallback

    # FilePointer → read bytes; other pointer schemes degrade to fallback
    # until Sprint 13+ adds scheme-specific resolvers.
    pointer_value = getattr(pointer, "value", None)
    if not pointer_value:
        return fallback
    try:
        from pathlib import Path
        p = Path(str(pointer_value))
        if p.is_file():
            return p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[PROMPT_TEMPLATE] reading pointer %r failed: %s: %s",
            pointer_value, type(e).__name__, str(e)[:100],
        )
    return fallback


def get_pipeline_params(
    client: ResourceRegistryClient,
    name: str,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sprint 12 Task 8: return the per-pipeline params dict.

    Reads the admin-registered pipeline_config by name. The params
    dict is sourced from the config's ``diagnostics.params`` sub-dict
    (ADR-004 §1.5 pattern — diagnostics is the demo-semantic bag;
    structured fields live there). Returns ``defaults`` (or ``{}``)
    on any lookup failure.

    Reused for: ``confidence_floor``, ``clinical_seed_queries``,
    ``MAX_ENTITIES``, ``MAX_RELS``. Sprint 12 wires confidence_floor
    through this path as a proof-of-pattern; remaining params follow
    in Sprint 13 per the plan's "migration rollout" note.
    """
    base = dict(defaults or {})
    try:
        resources = client.list(ResourceKind.PIPELINE_CONFIG)
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "[PIPELINE_PARAMS] admin list failed (%s: %s); using defaults for %r",
            type(e).__name__, str(e)[:100], name,
        )
        return base

    matches = [r for r in resources if (r.metadata or {}).get("name") == name]
    if not matches:
        return base
    matches.sort(key=lambda r: (r.metadata or {}).get("version", ""), reverse=True)
    meta = matches[0].metadata or {}
    diag = meta.get("diagnostics") if isinstance(meta.get("diagnostics"), dict) else {}
    params = diag.get("params") if isinstance(diag.get("params"), dict) else {}
    base.update(params)
    return base


def reset_caches_for_testing() -> None:
    """Clear state so tests see a fresh breaker + empty cache.

    Public (not underscore-prefixed) because tests import it; not part
    of the runtime API. No concurrent-safety consideration since tests
    run in a single thread per test function.
    """
    global _pipelines_cache, _pipelines_breaker
    global _bundles_cache, _bundles_breaker
    with _pipelines_lock:
        _pipelines_cache = _CacheEntry(payload=[])
        _pipelines_breaker = CircuitBreaker()
    with _bundles_lock:
        _bundles_cache = _CacheEntry(payload=[])
        _bundles_breaker = CircuitBreaker()


# --- Internal: shared read-with-fallback state machine ---------------------


def _read_with_fallback(
    client: ResourceRegistryClient,
    *,
    kind: ResourceKind,
    translate: Callable[[dict[str, Any]], dict[str, Any]],
    seed_fallback_path: Path | None,
    now: Callable[[], float],
    cache: _CacheEntry,
    breaker: CircuitBreaker,
    lock: threading.Lock,
) -> list[dict[str, Any]]:
    with lock:
        current = now()

        # 1. Fresh cache → return immediately.
        if cache.has_snapshot and (current - cache.last_refresh) < CACHE_TTL_SECONDS:
            return list(cache.payload)

        # 2. Breaker tripped → serve last-known-good without hitting admin.
        if breaker.is_tripped(current):
            if cache.has_snapshot:
                return list(cache.payload)
            return _load_seed_fallback(seed_fallback_path, kind)

        # 3. Try admin.
        try:
            resources = client.list(kind)
        except Exception as e:  # noqa: BLE001 — admin errors route to breaker
            breaker.record_failure(current)
            logger.warning(
                "[ADMIN_READER] kind=%s admin read failed (%s: %s); "
                "failure_count=%d",
                kind.value, type(e).__name__, str(e)[:100],
                breaker.failure_count,
            )
            if cache.has_snapshot:
                return list(cache.payload)
            return _load_seed_fallback(seed_fallback_path, kind)

        breaker.record_success()

        # 4. Translate + cache.
        translated: list[dict[str, Any]] = []
        for r in resources:
            meta = r.metadata or {}
            try:
                translated.append(translate(meta))
            except Exception as e:  # noqa: BLE001 — one bad entry shouldn't kill the list
                logger.warning(
                    "[ADMIN_READER] kind=%s translate failed for %s: %s",
                    kind.value, getattr(r, "id", "?"), e,
                )
                continue

        # Empty admin → fall back to seed YAML until archetypes registers.
        if not translated:
            fallback = _load_seed_fallback(seed_fallback_path, kind)
            cache.payload = fallback
        else:
            cache.payload = translated
        cache.last_refresh = current
        cache.has_snapshot = True
        return list(cache.payload)


# --- Translation ------------------------------------------------------------


def _pipeline_metadata_to_ui_slot(meta: dict[str, Any]) -> dict[str, Any]:
    """Map admin's PipelineConfigMetadata dict → UI slot dict.

    The UI slot's presentational fields (label, tagline, capability,
    help_anchor, default_slot) live in
    ``PipelineConfig.diagnostics.demo_ui`` per the 2026-04-20
    archetypes handover memo (Dev lean on open question 1). When a
    config lacks ``diagnostics.demo_ui``, fall back to sensible
    defaults derived from ``name`` / ``description``.
    """
    name = meta.get("name") or ""
    description = meta.get("description") or ""
    fusion_policy = meta.get("fusion_policy") or "union"
    backends = meta.get("backends_used") or ()

    demo_ui: dict[str, Any] = {}
    # Admin's PipelineConfigMetadata frozen model exposes only the
    # summary fields; the full PipelineConfig (including diagnostics)
    # requires resolve_pointer + YAML parse. We surface a subset here
    # from metadata + let callers enrich via resolve_pointer if needed.
    if isinstance(meta.get("diagnostics"), dict):
        demo_ui = dict(meta["diagnostics"].get("demo_ui") or {})

    return {
        "id": name,
        "label": demo_ui.get("label") or name.replace("_", " ").title(),
        "capability": demo_ui.get("capability") or "Discovery",
        "pipeline_id": demo_ui.get("pipeline_id") or name,
        "backend": (
            demo_ui.get("backend")
            or (backends[0] if backends else "deterministic")
        ),
        "tagline": demo_ui.get("tagline") or "",
        "description": description,
        "help_anchor": demo_ui.get("help_anchor") or name,
        "default_slot": demo_ui.get("default_slot"),
        "fusion_policy": fusion_policy,
        "version": meta.get("version") or "1.0.0",
    }


def _bundle_metadata_to_ui_entry(meta: dict[str, Any]) -> dict[str, Any]:
    """Map admin's BundleCompiledMetadata dict → bundle dropdown entry.

    The existing ``/api/bundles`` response shape is a flat list of
    bundle names + a ``default``. This translation preserves the
    name/version/domain fields; the caller builds the default
    selection on its own.
    """
    return {
        "name": meta.get("name") or "",
        "version": meta.get("version") or "",
        "domain": meta.get("domain") or "",
        "description": meta.get("description") or "",
    }


# --- Seed fallback (transitional Sprint-12 path) ---------------------------


def _load_seed_fallback(
    path: Path | None,
    kind: ResourceKind,
) -> list[dict[str, Any]]:
    """Load the Sprint-12 transitional seed YAML when admin is empty.

    Returns ``[]`` if no fallback path was provided OR the path
    doesn't exist. VP Prod Sprint 12 Phase 1 review copy rule applies:
    an empty return flows through to the UI as the softer "No
    pipelines available — ask your admin to register them." message.
    """
    if path is None or not path.is_file():
        return []
    import yaml
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[ADMIN_READER] kind=%s seed fallback %s failed to parse: %s",
            kind.value, path, e,
        )
        return []

    # Both pipelines + bundles use a top-level "slots" or "bundles"
    # list in their seed YAMLs. Keep the shape honest to what's on disk.
    if kind == ResourceKind.PIPELINE_CONFIG:
        entries = data.get("slots") or []
    elif kind == ResourceKind.BUNDLE_COMPILED:
        entries = data.get("bundles") or []
    else:
        entries = []
    return [dict(e) for e in entries if isinstance(e, dict)]
