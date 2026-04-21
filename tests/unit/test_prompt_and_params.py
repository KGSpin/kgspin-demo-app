"""Sprint 12 Task 7+8+11 — prompt_template + pipeline_params reader tests.

Covers the infrastructure helpers introduced by Tasks 7 + 8:
- ``get_prompt_template_text`` — admin prompt lookup → pointer bytes
- ``get_pipeline_params`` — admin-registered param dict per pipeline

Sprint 12 wires these as infrastructure. The concrete call-site
migrations (``kg-quality-comparison`` prompt + ``confidence_floor``
param) are proof-of-pattern; remaining prompt + param migrations land
in Sprint 13 per the handover memo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kgspin_interface.registry_client import ResourceKind
from kgspin_interface.resources import (
    FilePointer,
    Provenance,
    Resource,
    ResourceStatus,
)

from kgspin_demo_app.services import admin_registry_reader as reader


class FakeClient:
    """Minimal ResourceRegistryClient for prompt + params tests.

    Supports ``list(kind)`` + ``resolve_pointer(id)`` + ``get(id)``.
    """

    def __init__(self) -> None:
        self._by_kind: dict[ResourceKind, list[Resource]] = {}
        self._pointers: dict[str, Any] = {}
        self._raise_list: Exception | None = None
        self._raise_resolve: Exception | None = None

    def set_resources(self, kind: ResourceKind, resources: list[Resource]) -> None:
        self._by_kind[kind] = resources
        for r in resources:
            self._pointers[r.id] = r.pointer

    def set_list_failure(self, exc: Exception | None) -> None:
        self._raise_list = exc

    def set_resolve_failure(self, exc: Exception | None) -> None:
        self._raise_resolve = exc

    def list(self, kind: ResourceKind, **kwargs: Any) -> list[Resource]:
        if self._raise_list is not None:
            raise self._raise_list
        return list(self._by_kind.get(kind, []))

    def resolve_pointer(self, rid: str):
        if self._raise_resolve is not None:
            raise self._raise_resolve
        return self._pointers.get(rid)

    def get(self, rid: str):
        return None


def _prompt_resource(name: str, version: str, text_file: Path) -> Resource:
    return Resource(
        id=f"prompt_template:{name}:{version}",
        kind=ResourceKind.PROMPT_TEMPLATE,
        pointer=FilePointer(value=str(text_file)),
        metadata={"name": name, "version": version, "description": f"{name} prompt"},
        provenance=Provenance(
            registered_at=datetime.now(timezone.utc),
            registered_by="test",
            hash=None,
        ),
        status=ResourceStatus.ACTIVE,
    )


def _pipeline_resource_with_params(
    name: str, version: str = "1.0.0", params: dict[str, Any] | None = None,
) -> Resource:
    diagnostics: dict[str, Any] = {}
    if params:
        diagnostics["params"] = dict(params)
    return Resource(
        id=f"pipeline_config:{name}:{version}",
        kind=ResourceKind.PIPELINE_CONFIG,
        pointer=FilePointer(value=f"/tmp/fake-{name}.yaml"),
        metadata={
            "name": name,
            "version": version,
            "description": name,
            "fusion_policy": "union",
            "backends_used": ("deterministic",),
            "diagnostics": diagnostics,
        },
        provenance=Provenance(
            registered_at=datetime.now(timezone.utc),
            registered_by="test",
            hash=None,
        ),
        status=ResourceStatus.ACTIVE,
    )


# --- Task 7: prompt_template reader ---------------------------------------


def test_prompt_returns_fallback_when_admin_empty() -> None:
    client = FakeClient()
    out = reader.get_prompt_template_text(client, "kg-quality-comparison", fallback="FB")
    assert out == "FB"


def test_prompt_returns_fallback_when_admin_list_fails() -> None:
    client = FakeClient()
    client.set_list_failure(RuntimeError("admin unreachable"))
    out = reader.get_prompt_template_text(client, "foo", fallback="FB")
    assert out == "FB"


def test_prompt_returns_pointer_contents_on_match(tmp_path: Path) -> None:
    template_file = tmp_path / "kg-quality-comparison.txt"
    template_file.write_text("hello {ticker}", encoding="utf-8")
    client = FakeClient()
    client.set_resources(
        ResourceKind.PROMPT_TEMPLATE,
        [_prompt_resource("kg-quality-comparison", "1.0.0", template_file)],
    )
    out = reader.get_prompt_template_text(client, "kg-quality-comparison", fallback="FB")
    assert "hello {ticker}" in out


def test_prompt_version_filter_pins_exact_match(tmp_path: Path) -> None:
    v1 = tmp_path / "v1.txt"
    v1.write_text("v1 content")
    v2 = tmp_path / "v2.txt"
    v2.write_text("v2 content")
    client = FakeClient()
    client.set_resources(
        ResourceKind.PROMPT_TEMPLATE,
        [
            _prompt_resource("foo", "1.0.0", v1),
            _prompt_resource("foo", "2.0.0", v2),
        ],
    )
    assert "v1 content" in reader.get_prompt_template_text(client, "foo", version="1.0.0")
    assert "v2 content" in reader.get_prompt_template_text(client, "foo", version="2.0.0")


def test_prompt_highest_version_wins_when_version_unset(tmp_path: Path) -> None:
    v1 = tmp_path / "v1.txt"
    v1.write_text("v1 content")
    v2 = tmp_path / "v2.txt"
    v2.write_text("v2 content")
    client = FakeClient()
    client.set_resources(
        ResourceKind.PROMPT_TEMPLATE,
        [
            _prompt_resource("foo", "1.0.0", v1),
            _prompt_resource("foo", "2.0.0", v2),
        ],
    )
    # Lexicographic sort reverse → "2.0.0" > "1.0.0".
    assert "v2 content" in reader.get_prompt_template_text(client, "foo")


def test_prompt_fallback_when_pointer_missing_on_disk(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.txt"
    client = FakeClient()
    client.set_resources(
        ResourceKind.PROMPT_TEMPLATE,
        [_prompt_resource("foo", "1.0.0", missing)],
    )
    out = reader.get_prompt_template_text(client, "foo", fallback="FB")
    assert out == "FB"


def test_prompt_fallback_when_resolve_pointer_raises(tmp_path: Path) -> None:
    existing = tmp_path / "ok.txt"
    existing.write_text("content")
    client = FakeClient()
    client.set_resources(
        ResourceKind.PROMPT_TEMPLATE,
        [_prompt_resource("foo", "1.0.0", existing)],
    )
    client.set_resolve_failure(RuntimeError("admin exploded"))
    out = reader.get_prompt_template_text(client, "foo", fallback="FB")
    assert out == "FB"


# --- Task 8: pipeline_params reader ---------------------------------------


def test_params_returns_defaults_when_admin_empty() -> None:
    client = FakeClient()
    out = reader.get_pipeline_params(
        client, "fan_out", defaults={"confidence_floor": 0.55},
    )
    assert out == {"confidence_floor": 0.55}


def test_params_returns_defaults_when_admin_list_fails() -> None:
    client = FakeClient()
    client.set_list_failure(RuntimeError("admin unreachable"))
    out = reader.get_pipeline_params(client, "fan_out", defaults={"x": 1})
    assert out == {"x": 1}


def test_params_returns_admin_params_when_registered() -> None:
    client = FakeClient()
    client.set_resources(
        ResourceKind.PIPELINE_CONFIG,
        [
            _pipeline_resource_with_params("fan_out", params={
                "confidence_floor": 0.7,
                "clinical_seed_queries": ["lung cancer", "covid-19"],
            }),
        ],
    )
    out = reader.get_pipeline_params(
        client, "fan_out", defaults={"confidence_floor": 0.55},
    )
    assert out["confidence_floor"] == 0.7
    assert out["clinical_seed_queries"] == ["lung cancer", "covid-19"]


def test_params_admin_overrides_defaults() -> None:
    client = FakeClient()
    client.set_resources(
        ResourceKind.PIPELINE_CONFIG,
        [_pipeline_resource_with_params("fan_out", params={"confidence_floor": 0.8})],
    )
    out = reader.get_pipeline_params(
        client, "fan_out",
        defaults={"confidence_floor": 0.55, "other": "keep"},
    )
    assert out == {"confidence_floor": 0.8, "other": "keep"}


def test_params_unknown_pipeline_returns_defaults() -> None:
    client = FakeClient()
    client.set_resources(
        ResourceKind.PIPELINE_CONFIG,
        [_pipeline_resource_with_params("fan_out", params={"x": 1})],
    )
    out = reader.get_pipeline_params(
        client, "unknown_pipeline", defaults={"confidence_floor": 0.55},
    )
    assert out == {"confidence_floor": 0.55}
