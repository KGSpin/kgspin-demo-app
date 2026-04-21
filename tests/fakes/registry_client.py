"""Protocol-satisfying in-memory fake of ``ResourceRegistryClient``.

Used by all Sprint 09 unit + integration tests so the suite never
needs a live kgspin-admin. Keyed on resource id (derived via the
canonical id helpers in ``kgspin_interface.ids``) — re-registering
the same canonical id is a no-op, matching admin's idempotency
contract.

NOT shipped to production. Test scope only (VP Sec audit:
``tests/fakes/`` is not in the distribution wheel's ``packages``
entry).
"""

from __future__ import annotations

from datetime import datetime, timezone

from kgspin_interface import ids as _ids
from kgspin_interface.registry_client import (
    BundleCompiledMetadata,
    BundleSourceYamlMetadata,
    CorpusDocumentMetadata,
    FetcherMetadata,
    PipelineConfigMetadata,
    PluginMetadata,
    PromptTemplateMetadata,
    Resource,
    ResourceKind,
    ResourceStatus,
    TuningRunMetadata,
)
from kgspin_interface.resources import CustomPointer, Pointer, Provenance


class FakeRegistryClient:
    """In-memory Protocol-structural fake. See module docstring."""

    def __init__(self) -> None:
        self._store: dict[str, Resource] = {}

    # --- demo-used methods ---------------------------------------------

    def register_corpus_document(
        self,
        metadata: CorpusDocumentMetadata,
        pointer: Pointer,
        actor: str,
    ) -> Resource:
        rid = _ids.corpus_document_id(metadata.domain, metadata.source, metadata.identifier)
        if rid in self._store:
            return self._store[rid]
        resource = Resource(
            id=rid,
            kind=ResourceKind.CORPUS_DOCUMENT,
            pointer=pointer,
            metadata=metadata.model_dump(mode="json"),
            provenance=Provenance(
                registered_at=datetime.now(timezone.utc),
                registered_by=actor,
                hash=None,
            ),
            status=ResourceStatus.ACTIVE,
        )
        self._store[rid] = resource
        return resource

    def register_fetcher(self, metadata: FetcherMetadata, actor: str) -> Resource:
        rid = _ids.fetcher_id(metadata.spec.fetcher_id)
        if rid in self._store:
            return self._store[rid]
        # FETCHER resources require a non-None pointer; use a CustomPointer
        # that records the module:class so consumers can locate the impl.
        pointer = CustomPointer(
            scheme="python-module",
            value=metadata.spec.module_path or metadata.spec.fetcher_id,
        )
        resource = Resource(
            id=rid,
            kind=ResourceKind.FETCHER,
            pointer=pointer,
            metadata=metadata.model_dump(mode="json"),
            provenance=Provenance(
                registered_at=datetime.now(timezone.utc),
                registered_by=actor,
                hash=None,
            ),
            status=ResourceStatus.ACTIVE,
        )
        self._store[rid] = resource
        return resource

    def list(
        self,
        kind: ResourceKind,
        *,
        domain: str | None = None,
        source: str | None = None,
        status: ResourceStatus | None = None,
        limit: int | None = None,
    ) -> list[Resource]:
        out: list[Resource] = []
        for resource in self._store.values():
            if resource.kind != kind:
                continue
            meta = resource.metadata or {}
            if domain and meta.get("domain") != domain:
                continue
            if source and meta.get("source") != source:
                continue
            if status and resource.status != status:
                continue
            out.append(resource)
            if limit is not None and len(out) >= limit:
                break
        return out

    def get(self, id: str) -> Resource | None:
        return self._store.get(id)

    def resolve_pointer(self, id: str) -> Pointer | None:
        r = self._store.get(id)
        return r.pointer if r else None

    # --- stubs for Protocol conformance --------------------------------

    def register_bundle_source_yaml(
        self, metadata: BundleSourceYamlMetadata, pointer: Pointer, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo test fake does not implement bundles")

    def register_bundle_compiled(
        self, metadata: BundleCompiledMetadata, pointer: Pointer, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo test fake does not implement bundles")

    def register_plugin(self, metadata: PluginMetadata, actor: str) -> Resource:
        raise NotImplementedError("demo test fake does not implement plugins")

    def register_tuning_run(self, metadata: TuningRunMetadata, actor: str) -> Resource:
        raise NotImplementedError("demo test fake does not implement tuning runs")

    def register_pipeline_config(
        self, metadata: PipelineConfigMetadata, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo test fake does not implement pipeline configs")

    def register_prompt_template(
        self, metadata: PromptTemplateMetadata, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo test fake does not implement prompt templates")


__all__ = ["FakeRegistryClient"]
