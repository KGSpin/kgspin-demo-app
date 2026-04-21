"""Unit tests for :mod:`kgspin_demo_app.llm_backend` (Sprint 0.5.4, ADR-002)."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import pytest

from kgspin_demo_app.config import AppSettings, LLMSettings
from kgspin_demo_app.llm_backend import (
    LLMParamsError,
    check_endpoint_llm_params,
    reset_resolver_for_tests,
    reset_settings_for_tests,
    resolve_llm_backend,
)


@dataclass
class _FakeBackend:
    """Placeholder object that the fake factory hands back."""

    tag: str


class _FakeResolver:
    """Records the alias ids that were resolved; returns canned records."""

    def __init__(self) -> None:
        from kgspin_interface import LLMAliasRecord
        self.calls: list[str] = []
        self._record_cls = LLMAliasRecord

    def resolve(self, alias_id: str):
        self.calls.append(alias_id)
        return self._record_cls(
            id=alias_id, provider="gemini", model=f"gemini-for-{alias_id}",
        )


@pytest.fixture
def fake_resolver() -> _FakeResolver:
    resolver = _FakeResolver()
    reset_resolver_for_tests(resolver)
    try:
        yield resolver
    finally:
        reset_resolver_for_tests(None)


@pytest.fixture
def patched_factory(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace DefaultBackendFactory.get / create_backend with recorders.

    The recorders hand back a :class:`_FakeBackend` and append the kwargs they
    were called with so tests can assert precedence behaviour without
    exercising the real vendor factories.
    """
    calls: list[dict[str, Any]] = []

    from kgspin_demo_app import llm_backend as _mod

    def _factory_get(**kwargs):
        calls.append({"path": "factory", **kwargs})
        return _FakeBackend(tag=f"factory:{kwargs}")

    def _create_backend(backend_type: str, **kwargs):
        calls.append({"path": "create_backend", "backend_type": backend_type, **kwargs})
        return _FakeBackend(tag=f"create:{backend_type}")

    monkeypatch.setattr(_mod.DefaultBackendFactory, "get", _factory_get)
    monkeypatch.setattr(_mod, "create_backend", _create_backend)
    return calls


def _settings(**llm_kwargs: Any) -> AppSettings:
    return AppSettings(llm=LLMSettings(**llm_kwargs))


# ---------- check_endpoint_llm_params ----------


def test_endpoint_check_allows_alias_only() -> None:
    check_endpoint_llm_params(llm_alias="x", model_supplied=False)


def test_endpoint_check_allows_legacy_only() -> None:
    check_endpoint_llm_params(llm_alias=None, model_supplied=True)


def test_endpoint_check_allows_neither() -> None:
    check_endpoint_llm_params(llm_alias=None, model_supplied=False)


def test_endpoint_check_rejects_both() -> None:
    with pytest.raises(LLMParamsError):
        check_endpoint_llm_params(llm_alias="x", model_supplied=True)


# ---------- resolve_llm_backend: explicit selectors ----------


def test_explicit_alias_resolves_through_resolver(
    fake_resolver: _FakeResolver, patched_factory: list[dict[str, Any]],
) -> None:
    result = resolve_llm_backend(llm_alias="gemini_flash")
    assert isinstance(result, _FakeBackend)
    assert patched_factory[0]["path"] == "factory"
    assert patched_factory[0]["alias"] == "gemini_flash"
    assert patched_factory[0]["resolver"] is fake_resolver


def test_explicit_provider_and_model_go_direct(
    patched_factory: list[dict[str, Any]],
) -> None:
    resolve_llm_backend(llm_provider="gemini", llm_model="gemini-42-flash")
    assert patched_factory[0]["path"] == "factory"
    assert patched_factory[0]["provider"] == "gemini"
    assert patched_factory[0]["model"] == "gemini-42-flash"
    assert "alias" not in patched_factory[0] or patched_factory[0]["alias"] is None


def test_legacy_model_emits_deprecation_and_uses_gemini(
    patched_factory: list[dict[str, Any]],
) -> None:
    with pytest.warns(DeprecationWarning, match="`model` parameter"):
        resolve_llm_backend(legacy_model="gemini-2.5-flash")
    assert patched_factory[0]["path"] == "create_backend"
    assert patched_factory[0]["backend_type"] == "gemini"
    assert patched_factory[0]["model"] == "gemini-2.5-flash"


# ---------- resolve_llm_backend: ambiguity / partial input ----------


def test_alias_plus_legacy_model_is_ambiguous() -> None:
    with pytest.raises(LLMParamsError, match="ambiguous"):
        resolve_llm_backend(llm_alias="x", legacy_model="y")


def test_alias_plus_provider_is_ambiguous() -> None:
    with pytest.raises(LLMParamsError, match="ambiguous"):
        resolve_llm_backend(llm_alias="x", llm_provider="gemini")


def test_provider_without_model_raises() -> None:
    with pytest.raises(LLMParamsError, match="Direct mode"):
        resolve_llm_backend(llm_provider="gemini")


def test_model_without_provider_raises() -> None:
    with pytest.raises(LLMParamsError, match="Direct mode"):
        resolve_llm_backend(llm_model="gemini-2.5-flash")


# ---------- resolve_llm_backend: fall-through to demo config ----------


def test_falls_through_to_default_alias(
    fake_resolver: _FakeResolver, patched_factory: list[dict[str, Any]],
) -> None:
    settings = _settings(default_alias="ship_default")
    resolve_llm_backend(settings=settings)
    assert patched_factory[0]["alias"] == "ship_default"
    assert fake_resolver.calls == []  # resolver.resolve() hit via factory mock, not directly


def test_flow_override_wins_over_default(
    fake_resolver: _FakeResolver, patched_factory: list[dict[str, Any]],
) -> None:
    settings = _settings(
        default_alias="ship_default",
        compare_qa_llm="compare_specific",
    )
    resolve_llm_backend(flow="compare_qa", settings=settings)
    assert patched_factory[0]["alias"] == "compare_specific"


def test_flow_override_null_falls_through_to_default(
    fake_resolver: _FakeResolver, patched_factory: list[dict[str, Any]],
) -> None:
    settings = _settings(
        default_alias="ship_default",
        compare_qa_llm=None,
    )
    resolve_llm_backend(flow="compare_qa", settings=settings)
    assert patched_factory[0]["alias"] == "ship_default"


def test_no_selectors_and_no_default_emits_deprecation(
    patched_factory: list[dict[str, Any]],
) -> None:
    settings = _settings(default_alias=None)
    with pytest.warns(DeprecationWarning, match="llm.default_alias"):
        resolve_llm_backend(settings=settings)
    assert patched_factory[0]["path"] == "create_backend"
    assert patched_factory[0]["backend_type"] == "gemini"


# ---------- extra opts forwarding ----------


def test_opts_forwarded_to_factory(
    fake_resolver: _FakeResolver, patched_factory: list[dict[str, Any]],
) -> None:
    resolve_llm_backend(llm_alias="x", base_url="https://partner.example")
    assert patched_factory[0]["base_url"] == "https://partner.example"
