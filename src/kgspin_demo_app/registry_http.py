"""HTTP adapter for admin's ResourceRegistryClient (Sprint 09 Task 2).

Demo landers + the ``register-fetchers`` CLI use this adapter to
talk to kgspin-admin's HTTP registry. Admin is the authoritative
read path for core post-REQ-005; demo is the write path for
corpus_document + fetcher resources.

Protocol compliance is ``@runtime_checkable`` so ``isinstance(client,
ResourceRegistryClient)`` asserts at runtime — test 1 in
``tests/unit/test_registry_http.py`` pins this.

See docs/architecture/decisions/ADR-003-fetcher-abc-and-admin-registry.md
for the Security Debt label on the current auth posture (X-Actor
is identification, not authentication — local-loopback assumption).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, NoReturn
from urllib.parse import urlparse

import httpx
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
from kgspin_interface.resources import CustomPointer, Pointer


logger = logging.getLogger(__name__)

DEFAULT_ADMIN_URL = "http://127.0.0.1:8750"
DEFAULT_TIMEOUT_SEC = 30

# VP Sec MEDIUM: loopback-only `http://`; non-loopback hosts require `https://`.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# VP Sec MEDIUM: error messages truncated to a single control-character-free line.
_ERROR_BODY_MAX_CHARS = 200
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_error_body(body: str) -> str:
    """Collapse a response body into a single safe line for exception messages.

    Full body still reaches DEBUG-level logs; only the exception message
    is trimmed. VP Sec MEDIUM.
    """
    if not body:
        return ""
    collapsed = _CONTROL_CHARS_RE.sub(" ", body)
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    if len(collapsed) > _ERROR_BODY_MAX_CHARS:
        collapsed = collapsed[: _ERROR_BODY_MAX_CHARS - 1] + "…"
    return collapsed


def _require_transport_safety(url: str) -> None:
    """Enforce HTTPS when the admin URL targets a non-loopback host.

    VP Sec MEDIUM. Raises ``RuntimeError`` at construction if an
    ``http://`` URL targets anything other than localhost / 127.0.0.1 / ::1.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"HttpResourceRegistryClient: refusing plain http:// for non-loopback host "
            f"{host!r}. Use https:// or point the URL at localhost/127.0.0.1/::1. "
            f"(ADR-003 Security Debt — this posture is local-loopback-only.)"
        )


class HttpResourceRegistryClient:
    """HTTP adapter satisfying the ``ResourceRegistryClient`` Protocol.

    Implements the 5 resource methods demo actually uses
    (``register_corpus_document``, ``register_fetcher``, ``list``,
    ``get``, ``resolve_pointer``) against a live admin; stubs the 6
    other Protocol methods with ``NotImplementedError`` to stay
    Protocol-conformant at runtime.

    Idempotency contract: HTTP 409 Conflict responses from admin are
    treated as "already registered" and the existing ``Resource`` is
    returned (parsed from the 409 response body). This makes
    ``register_*`` methods safe to re-run (VP Eng MAJOR risk).

    Transport security: plain ``http://`` is only accepted against
    loopback hosts; any other hostname requires ``https://``.
    """

    def __init__(self) -> None:
        admin_url = os.environ.get("KGSPIN_ADMIN_URL", DEFAULT_ADMIN_URL).rstrip("/")
        _require_transport_safety(admin_url)
        self._admin_url = admin_url
        self._client = httpx.Client(
            base_url=admin_url,
            timeout=DEFAULT_TIMEOUT_SEC,
        )

    # --- helpers -------------------------------------------------------

    def _raise_http_error(self, response: httpx.Response, path: str) -> NoReturn:
        body_raw = response.text or ""
        logger.debug(
            "admin %s %s → %d; full body: %s",
            response.request.method, path, response.status_code, body_raw,
        )
        body = _sanitize_error_body(body_raw)
        raise RuntimeError(
            f"admin {response.request.method} {path} → {response.status_code}: {body}"
            if body
            else f"admin {response.request.method} {path} → {response.status_code}"
        )

    def _post(self, path: str, *, json_body: dict[str, Any], actor: str) -> Resource:
        headers = {"X-Actor": actor}
        try:
            response = self._client.post(path, json=json_body, headers=headers)
        except httpx.RequestError as e:
            raise RuntimeError(
                f"admin {self._admin_url} unreachable: {type(e).__name__}: {str(e)[:100]}"
            ) from e

        if response.status_code == 409:
            # Idempotency contract (VP Eng MAJOR): 409 → return the existing resource.
            try:
                return Resource.model_validate(response.json())
            except Exception:
                # If admin's 409 body isn't a Resource, fall through to error.
                pass
        if 200 <= response.status_code < 300:
            return Resource.model_validate(response.json())
        self._raise_http_error(response, path)

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.RequestError as e:
            raise RuntimeError(
                f"admin {self._admin_url} unreachable: {type(e).__name__}: {str(e)[:100]}"
            ) from e
        if 200 <= response.status_code < 300:
            return response.json()
        if response.status_code == 404:
            return None
        self._raise_http_error(response, path)

    # --- Protocol methods demo uses -----------------------------------

    def register_corpus_document(
        self,
        metadata: CorpusDocumentMetadata,
        pointer: Pointer,
        actor: str,
    ) -> Resource:
        body = {
            "metadata": metadata.model_dump(mode="json"),
            "pointer": pointer.model_dump(mode="json"),
        }
        return self._post("/resources/corpus_document", json_body=body, actor=actor)

    def register_fetcher(self, metadata: FetcherMetadata, actor: str) -> Resource:
        # Sprint 09: admin's RegisterFetcherRequest requires `pointer` in the
        # body (extra='forbid'), but the interface Protocol only passes metadata.
        # Bridge the gap by deriving a CustomPointer from the InvocationSpec's
        # module_path. Per CTO: "CustomPointer(scheme='python-module')
        # establishes a new cluster convention" — admin will accept it with a
        # permissive warning until its Sprint 03/04 handler-registration.
        module_path = metadata.spec.module_path or metadata.spec.fetcher_id
        pointer = CustomPointer(scheme="python-module", value=module_path)
        body = {
            "metadata": metadata.model_dump(mode="json"),
            "pointer": pointer.model_dump(mode="json"),
        }
        return self._post("/resources/fetcher", json_body=body, actor=actor)

    def list(
        self,
        kind: ResourceKind,
        *,
        domain: str | None = None,
        source: str | None = None,
        status: ResourceStatus | None = None,
        limit: int | None = None,
    ) -> list[Resource]:
        params: dict[str, Any] = {"kind": kind.value}
        if domain:
            params["domain"] = domain
        if source:
            params["source"] = source
        if status:
            params["status"] = status.value
        if limit is not None:
            params["limit"] = limit
        data = self._get_json("/resources", params=params)
        if data is None:
            return []
        return [Resource.model_validate(item) for item in data]

    def get(self, id: str) -> Resource | None:
        data = self._get_json(f"/resources/{id}")
        return None if data is None else Resource.model_validate(data)

    def resolve_pointer(self, id: str) -> Pointer | None:
        # Admin's pointer endpoint is at /pointer/{id}, not /resources/{id}/pointer
        # (confirmed via admin's routes_resources.py).
        data = self._get_json(f"/pointer/{id}")
        if data is None:
            return None
        from pydantic import TypeAdapter
        return TypeAdapter(Pointer).validate_python(data)

    # --- Protocol methods demo DOES NOT use (stubs) -------------------
    #
    # These exist only to satisfy @runtime_checkable isinstance checks.
    # Demo never writes bundles, plugins, tuning runs, or prompt templates —
    # those are the authoritative domains of other sibling repos. Calling
    # any of these is a programmer error in demo code.

    def register_bundle_source_yaml(
        self, metadata: BundleSourceYamlMetadata, pointer: Pointer, actor: str,
    ) -> Resource:
        raise NotImplementedError(
            "demo does not compile bundles; register via kgspin-admin or the plugin repo"
        )

    def register_bundle_compiled(
        self, metadata: BundleCompiledMetadata, pointer: Pointer, actor: str,
    ) -> Resource:
        raise NotImplementedError(
            "demo does not compile bundles; register via kgspin-admin or the plugin repo"
        )

    def register_plugin(
        self, metadata: PluginMetadata, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo does not register plugins")

    def register_tuning_run(
        self, metadata: TuningRunMetadata, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo does not run tuning")

    def register_pipeline_config(
        self, metadata: PipelineConfigMetadata, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo does not publish pipeline configs")

    def register_prompt_template(
        self, metadata: PromptTemplateMetadata, actor: str,
    ) -> Resource:
        raise NotImplementedError("demo does not publish prompt templates")

    def close(self) -> None:
        self._client.close()


__all__ = ["HttpResourceRegistryClient", "DEFAULT_ADMIN_URL"]
