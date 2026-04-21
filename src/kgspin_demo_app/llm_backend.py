"""Demo-side LLM backend resolver (ADR-002 Phase 4).

Every LLM call in ``demos/extraction/demo_compare.py`` resolves through
:func:`resolve_llm_backend`, which threads the ADR-002 precedence:

1. Explicit ``llm_alias`` — resolve via :class:`kgspin_interface.LLMAliasResolver`
   and hand to :class:`~kgspin_core.agents.backend_factory.DefaultBackendFactory`.
2. Explicit ``llm_provider`` + ``llm_model`` — direct construction (escape hatch).
3. Legacy ``model`` (compat path from the pre-alpha ``?model=…`` query param) —
   ``create_backend("gemini", model=…)`` with a DeprecationWarning.
4. Neither — fall through to the demo's config default: per-flow override in
   ``AppSettings.llm.<flow>_llm``, else ``AppSettings.llm.default_alias``,
   else the hardcoded legacy fallback (``create_backend("gemini")``) with a
   DeprecationWarning.

Passing a selector from more than one mode at the same time raises
:class:`LLMParamsError` (endpoints surface this as HTTP 400).

The admin URL is read from the ``KGSPIN_ADMIN_URL`` env var (ADR-001 §5
secrets-adjacent: client-side, not in ``config.yaml``). Tests inject a fake
resolver via :func:`reset_resolver_for_tests`.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

from kgspin_core.agents.backend_factory import DefaultBackendFactory
from kgspin_core.agents.backends import create_backend
from kgspin_interface import LLMAliasResolver, ModelBackend

from kgspin_demo_app.config import AppSettings, load_settings


class LLMParamsError(ValueError):
    """Raised when LLM-selector parameters are ambiguous or malformed.

    Subclass of ``ValueError`` so the FastAPI layer can map it to a 400 via
    a single ``except LLMParamsError`` branch without catching unrelated
    ``ValueError``s from deeper in core.
    """


# Module-level singletons. Built lazily on first call; reset between tests
# via :func:`reset_resolver_for_tests` / :func:`reset_settings_for_tests`.
_RESOLVER: LLMAliasResolver | None = None
_SETTINGS: AppSettings | None = None


def _get_resolver() -> LLMAliasResolver:
    """Return the process-wide :class:`LLMAliasResolver`.

    Admin URL comes from ``KGSPIN_ADMIN_URL``; defaults to
    ``http://localhost:8080`` to match the kgspin-admin dev default.
    """
    global _RESOLVER
    if _RESOLVER is None:
        admin_url = os.environ.get("KGSPIN_ADMIN_URL", "http://localhost:8080")
        _RESOLVER = LLMAliasResolver(admin_url=admin_url)
    return _RESOLVER


def reset_resolver_for_tests(resolver: LLMAliasResolver | None = None) -> None:
    """Replace (or clear) the module-level resolver. Tests only."""
    global _RESOLVER
    _RESOLVER = resolver


def _get_settings() -> AppSettings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load_settings()
    return _SETTINGS


def reset_settings_for_tests(settings: AppSettings | None = None) -> None:
    """Replace (or clear) the cached settings. Tests only."""
    global _SETTINGS
    _SETTINGS = settings


def _flow_alias(settings: AppSettings, flow: str | None) -> str | None:
    """Return the per-flow override alias, if configured for ``flow``."""
    if flow is None:
        return None
    field = f"{flow}_llm"
    return getattr(settings.llm, field, None)


def resolve_llm_backend(
    *,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    legacy_model: str | None = None,
    flow: str | None = None,
    settings: AppSettings | None = None,
    **opts: Any,
) -> ModelBackend:
    """Resolve a ``ModelBackend`` per the demo's ADR-002 precedence chain.

    Args:
        llm_alias: Admin-registered alias id.
        llm_provider: Vendor name (direct-mode escape hatch).
        llm_model: Vendor-specific model string (requires ``llm_provider``).
        legacy_model: Value from the deprecated ``model`` query / body
            parameter. Treated as a Gemini-only request with a
            ``DeprecationWarning``.
        flow: Name of the LLM-invoking flow (``compare_qa``, ``wtm``,
            ``impact``, ``auto_flag``, ``auto_discover_tp``,
            ``quality_analysis``). When set, the flow-specific override
            (``AppSettings.llm.<flow>_llm``) takes precedence over
            ``default_alias`` for the fall-through case.
        settings: Pre-loaded :class:`AppSettings`; for tests. Production
            code omits and lets the module load its own.
        **opts: Forwarded to the backend constructor (``base_url``, etc.).

    Raises:
        LLMParamsError: on ambiguous / malformed selectors.
    """
    # Count the selector modes: only one of (alias, provider+model, legacy) may
    # be non-empty at the call edge. The fall-through case passes none.
    modes = [
        bool(llm_alias),
        bool(llm_provider or llm_model),
        bool(legacy_model),
    ]
    if sum(modes) > 1:
        raise LLMParamsError(
            "LLM selector is ambiguous: pass at most one of `llm_alias`, "
            "`(llm_provider, llm_model)`, or the deprecated `model` "
            "parameter. `llm_alias` is preferred (ADR-002)."
        )

    if llm_alias:
        return DefaultBackendFactory.get(
            alias=llm_alias, resolver=_get_resolver(), **opts,
        )

    if llm_provider or llm_model:
        if not (llm_provider and llm_model):
            raise LLMParamsError(
                "Direct mode requires both `llm_provider` and `llm_model`; "
                "got only one. Pass both, or use `llm_alias` instead."
            )
        return DefaultBackendFactory.get(
            provider=llm_provider, model=llm_model, **opts,
        )

    if legacy_model:
        warnings.warn(
            "The `model` parameter is deprecated; pass `llm_alias=` to "
            "select an admin-registered LLM alias instead (ADR-002 §7).",
            DeprecationWarning,
            stacklevel=2,
        )
        return create_backend("gemini", model=legacy_model, **opts)

    # Fall-through: no explicit selector. Use the demo's configured default.
    settings = settings or _get_settings()
    alias = _flow_alias(settings, flow) or settings.llm.default_alias
    if alias:
        return DefaultBackendFactory.get(
            alias=alias, resolver=_get_resolver(), **opts,
        )

    warnings.warn(
        "No LLM selector supplied and `llm.default_alias` is unset in "
        "config.yaml; falling back to the legacy GeminiBackend default. "
        "Set `llm.default_alias` to a registered alias id to silence this "
        "warning (ADR-002 §7).",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_backend("gemini", **opts)


def check_endpoint_llm_params(
    *, llm_alias: str | None, model_supplied: bool,
) -> None:
    """Validate the two-selector surface exposed by FastAPI endpoints.

    Endpoints accept ``llm_alias`` (new) and ``model`` (deprecated compat).
    Passing both is ambiguous — raise :class:`LLMParamsError` so the caller
    can convert to HTTP 400.

    ``model_supplied`` is a boolean the caller computes from the raw
    request (``"model" in request.query_params`` for GET, ``"model" in
    body`` for POST). Inspecting the raw request is the only way to
    distinguish "user explicitly passed ``model``" from "FastAPI filled
    in the default value" — FastAPI discards that signal by the time the
    handler runs.
    """
    if llm_alias and model_supplied:
        raise LLMParamsError(
            "Pass either `llm_alias` or the deprecated `model`, not both. "
            "`llm_alias` selects an admin-registered alias; `model` is the "
            "pre-ADR-002 Gemini-only path."
        )


__all__ = [
    "LLMParamsError",
    "check_endpoint_llm_params",
    "reset_resolver_for_tests",
    "reset_settings_for_tests",
    "resolve_llm_backend",
]
