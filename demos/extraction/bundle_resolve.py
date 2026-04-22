"""Bundle + GLiNER backend resolution with module-level caches.

``_get_bundle`` resolves a domain bundle name (or the default) to a loaded
``ExtractionBundle`` instance, reusing a thread-safe cache. ``_get_gliner_backend``
lazily instantiates the GLiNER backend for zero-LLM pipelines. ``purge_caches``
clears both caches; the demo's ``/api/purge-cache`` endpoint delegates here.
"""

from __future__ import annotations

import logging
import threading

from pipeline_common import (
    BUNDLE_PATH,
    resolve_domain_bundle_path,
)

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_bundle_cache: dict[str, object] = {}  # bundle_name -> ExtractionBundle
_CACHED_BUNDLE = None  # kept for purge_cache compatibility
_CACHED_GLINER_BACKEND = None


def _get_bundle(bundle_name: str | None = None):
    """Load and cache a domain bundle by name. Defaults to BUNDLE_PATH.

    Wave 3: returns the unoverlaid ``ExtractionBundle.load(domain_path)``.
    The pipeline config travels separately via ``pipeline_config_ref`` on
    ``run_pipeline`` and is resolved by core against admin at dispatch.
    """
    global _CACHED_BUNDLE

    if bundle_name:
        if bundle_name in _bundle_cache:
            return _bundle_cache[bundle_name]
        with _init_lock:
            if bundle_name not in _bundle_cache:
                from kgspin_core.execution.extractor import ExtractionBundle
                domain_path = resolve_domain_bundle_path(bundle_name)
                _bundle_cache[bundle_name] = ExtractionBundle.load(domain_path)
            return _bundle_cache[bundle_name]

    bundle_path = BUNDLE_PATH
    if bundle_path is None:
        raise FileNotFoundError(
            "No default bundle available: admin has no financial "
            "bundle_compiled registered. Run `kgspin-admin sync "
            "archetypes <blueprint>` to register bundles."
        )
    default_name = BUNDLE_PATH.name

    if default_name in _bundle_cache:
        return _bundle_cache[default_name]

    with _init_lock:
        if default_name not in _bundle_cache:
            from kgspin_core.execution.extractor import ExtractionBundle
            _bundle_cache[default_name] = ExtractionBundle.load(bundle_path)
        _CACHED_BUNDLE = _bundle_cache[default_name]
        return _bundle_cache[default_name]


def _bundle_id(bundle_name: str | None = None) -> str:
    """Sprint 42.6: Return the bundle directory name — the unique ID.

    Used for cache keys, display, and log metadata. No parsing or stripping.
    Same string that locates the bundle on disk.

    Examples:
        'financial-fast-v1.0.0' -> 'financial-fast-v1.0.0'
        'financial-v1.8.0'      -> 'financial-v1.8.0'
        None                    -> default bundle directory name
    """
    return bundle_name or BUNDLE_PATH.name


def _split_bundle_id(domain_id: str, pipeline_id: str) -> str:
    """Sprint 118: Build cache-safe bundle identifier for split bundles."""
    return f"dom={domain_id}_p={pipeline_id}"


def _get_gliner_backend():
    global _CACHED_GLINER_BACKEND
    if _CACHED_GLINER_BACKEND is None:
        with _init_lock:
            if _CACHED_GLINER_BACKEND is None:
                try:
                    from kgspin_core.agents.backends import create_backend
                    _CACHED_GLINER_BACKEND = create_backend(
                        backend_type="gliner",
                        labels=[
                            "PERSON", "ORGANIZATION", "LOCATION", "PRODUCT",
                            "GENERIC_BUSINESS_CATEGORY", "ABSTRACT_CONCEPT",
                        ],
                        negative_labels={
                            "GENERIC_BUSINESS_CATEGORY", "ABSTRACT_CONCEPT",
                        },
                    )
                except Exception:
                    pass
    return _CACHED_GLINER_BACKEND


def purge_caches() -> None:
    """Clear both the bundle cache and the one-shot ``_CACHED_BUNDLE`` slot.

    Exposed so the demo's ``/api/purge-cache`` endpoint can clear the
    in-memory bundle cache without reaching into module internals.
    """
    global _CACHED_BUNDLE
    _CACHED_BUNDLE = None
    _bundle_cache.clear()
