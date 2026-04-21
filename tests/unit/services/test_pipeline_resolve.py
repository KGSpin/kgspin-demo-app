"""W1-D — pipeline_common admin-query path tests.

Covers ``resolve_pipeline_config`` (delegates to a mocked
``PipelineResolver``) and ``list_available_pipelines`` (parses admin's
``/resources?kind=pipeline_config`` payload). Confirms the demo no
longer touches the filesystem for pipeline configs (ADR-003 §5).
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_DEMO_DIR = Path(__file__).resolve().parents[3] / "demos" / "extraction"
sys.path.insert(0, str(_DEMO_DIR))

import pipeline_common  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_resolver_singleton():
    pipeline_common._pipeline_resolver = None
    yield
    pipeline_common._pipeline_resolver = None


def test_resolve_pipeline_config_delegates_to_resolver(monkeypatch):
    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = {"name": "emergent", "stages": []}
    monkeypatch.setattr(
        pipeline_common, "_get_pipeline_resolver", lambda: fake_resolver,
    )

    result = pipeline_common.resolve_pipeline_config("emergent")

    assert result == {"name": "emergent", "stages": []}
    fake_resolver.resolve.assert_called_once_with("emergent")


def test_resolve_pipeline_config_caches_resolver_singleton(monkeypatch):
    """Lazy singleton: PipelineResolver is constructed once per process."""
    constructed = []

    class _FakeResolver:
        def __init__(self, admin_url):
            constructed.append(admin_url)

        def resolve(self, pipeline_id):
            return {"id": pipeline_id}

    fake_module = type(sys)("kgspin_core.execution.pipeline_resolver")
    fake_module.PipelineResolver = _FakeResolver
    monkeypatch.setitem(
        sys.modules, "kgspin_core.execution.pipeline_resolver", fake_module,
    )
    monkeypatch.setenv("KGSPIN_ADMIN_URL", "http://test-admin:9999/")

    pipeline_common.resolve_pipeline_config("emergent")
    pipeline_common.resolve_pipeline_config("structural")

    assert constructed == ["http://test-admin:9999"]


def _fake_urlopen(payload):
    body = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.read.return_value = body
    response.__enter__ = lambda self: response
    response.__exit__ = lambda self, *exc: False
    return response


def test_list_available_pipelines_parses_resources_envelope(monkeypatch):
    payload = {
        "resources": [
            {"id": "pipeline_config:emergent",
             "metadata": {"name": "emergent"}},
            {"id": "pipeline_config:structural",
             "metadata": {"name": "structural"}},
        ],
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: _fake_urlopen(payload),
    )

    result = pipeline_common.list_available_pipelines()

    assert result == ["emergent", "structural"]


def test_list_available_pipelines_parses_bare_list(monkeypatch):
    payload = [
        {"id": "agentic", "metadata": {}},
        {"id": "fan_out", "metadata": {"name": "fan_out"}},
    ]
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: _fake_urlopen(payload),
    )

    result = pipeline_common.list_available_pipelines()

    assert result == ["agentic", "fan_out"]


def test_list_available_pipelines_returns_empty_on_admin_down(monkeypatch):
    import urllib.error

    def _raise(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    assert pipeline_common.list_available_pipelines() == []


def test_list_available_pipelines_returns_empty_on_garbage_payload(monkeypatch):
    response = MagicMock()
    response.read.return_value = b"not json"
    response.__enter__ = lambda self: response
    response.__exit__ = lambda self, *exc: False
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: response,
    )

    assert pipeline_common.list_available_pipelines() == []


def test_pipeline_configs_dir_constant_removed():
    """ADR-003 §5: the on-disk pipeline configs dir is gone for good."""
    assert not hasattr(pipeline_common, "PIPELINE_CONFIGS_DIR")
