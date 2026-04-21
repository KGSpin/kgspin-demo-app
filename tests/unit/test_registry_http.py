"""Sprint 09 Task 2 — tests for ``HttpResourceRegistryClient``.

Seven tests, one per VP Eng + VP Sec criterion listed in the plan:
    1. Protocol isinstance check (runtime_checkable)
    2. Happy path register_corpus_document
    3. 500 response → sanitized single-line RuntimeError
    4. Unreachable admin → RuntimeError
    5. X-Actor header on every POST (corpus + fetcher)
    6. 409 Conflict on register_fetcher → Resource (idempotency)
    7. Non-loopback http:// URL → RuntimeError at construction
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest import mock

import pytest
from kgspin_interface import DOCUMENT_FETCHER_CONTRACT_VERSION, FetcherMetadata
from kgspin_interface.registry_client import (
    Resource,
    ResourceKind,
    ResourceRegistryClient,
    ResourceStatus,
)
from kgspin_interface.resources import (
    CorpusDocumentMetadata,
    CustomPointer,
    FilePointer,
    InvocationSpec,
    Provenance,
)


@pytest.fixture
def admin_url_localhost(monkeypatch: pytest.MonkeyPatch) -> str:
    url = "http://127.0.0.1:8750"
    monkeypatch.setenv("KGSPIN_ADMIN_URL", url)
    return url


def _sample_corpus_doc_metadata() -> CorpusDocumentMetadata:
    return CorpusDocumentMetadata(
        domain="financial",
        source="sec_edgar",
        identifier={"ticker": "JNJ", "filing": "10-K"},
        fetch_timestamp=datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc),
        source_extras={"cik": "0000200406"},
    )


def _sample_fetcher_metadata() -> FetcherMetadata:
    return FetcherMetadata(
        spec=InvocationSpec(
            fetcher_id="edgar",
            base_type="DocumentFetcher",
            contract_version=DOCUMENT_FETCHER_CONTRACT_VERSION,
            module_path="kgspin_demo_app.landers.sec:SecLander",
        ),
        capabilities=("financial.sec_edgar",),
        owner="kgspin-demo",
        description="SEC EDGAR 10-K / 8-K / 10-Q fetcher",
    )


def _sample_resource(kind: ResourceKind, rid: str = "test-id") -> dict:
    pointer: FilePointer | CustomPointer
    if kind == ResourceKind.CORPUS_DOCUMENT:
        pointer = FilePointer(value="/tmp/raw.html")
    else:
        # Fetcher / plugin / etc. use CustomPointer keyed on module path.
        pointer = CustomPointer(scheme="python-module", value="kgspin_demo_app.landers.sec:SecLander")
    return Resource(
        id=rid,
        kind=kind,
        pointer=pointer,
        metadata={"domain": "financial", "source": "sec_edgar"},
        provenance=Provenance(
            registered_at=datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc),
            registered_by="test:actor",
            hash=None,
        ),
        status=ResourceStatus.ACTIVE,
    ).model_dump(mode="json")


# -------- 1. Protocol isinstance check -------------------------------------


def test_adapter_satisfies_protocol(admin_url_localhost: str) -> None:
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient
    client = HttpResourceRegistryClient()
    try:
        assert isinstance(client, ResourceRegistryClient)
    finally:
        client.close()


# -------- 2. Happy-path register_corpus_document ---------------------------


def test_register_corpus_document_happy_path(
    admin_url_localhost: str, httpx_mock
) -> None:
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    httpx_mock.add_response(
        method="POST",
        url=f"{admin_url_localhost}/resources/corpus_document",
        json=_sample_resource(ResourceKind.CORPUS_DOCUMENT, "doc-123"),
        status_code=201,
    )

    client = HttpResourceRegistryClient()
    try:
        result = client.register_corpus_document(
            metadata=_sample_corpus_doc_metadata(),
            pointer=FilePointer(value="/tmp/raw.html"),
            actor="fetcher:edgar",
        )
        assert isinstance(result, Resource)
        assert result.id == "doc-123"
        assert result.kind == ResourceKind.CORPUS_DOCUMENT
    finally:
        client.close()


# -------- 3. 500 response → sanitized single-line error --------------------


def test_500_response_yields_sanitized_single_line_error(
    admin_url_localhost: str, httpx_mock
) -> None:
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    # Multiline body with control chars — should be collapsed + trimmed.
    malicious_body = (
        "Traceback (most recent call last):\n"
        "  File \"/admin/routes.py\", line 42, in register\n"
        "    raise ValueError('oh no')\n"
        "\x00\x01ValueError: oh no\n"
    ) + ("X" * 500)  # very long, should be truncated
    httpx_mock.add_response(
        method="POST",
        url=f"{admin_url_localhost}/resources/corpus_document",
        text=malicious_body,
        status_code=500,
    )

    client = HttpResourceRegistryClient()
    try:
        with pytest.raises(RuntimeError) as excinfo:
            client.register_corpus_document(
                metadata=_sample_corpus_doc_metadata(),
                pointer=FilePointer(value="/tmp/raw.html"),
                actor="fetcher:edgar",
            )
        message = str(excinfo.value)
        # No newlines in the exception message.
        assert "\n" not in message
        # No control chars.
        assert "\x00" not in message and "\x01" not in message
        # ≤ 200 chars for the body portion (plus the path/status prefix).
        # Full cap is roughly 200 + ~60 for the prefix.
        assert len(message) <= 300
        # Status code is present.
        assert "500" in message
    finally:
        client.close()


# -------- 4. Unreachable admin → RuntimeError ------------------------------


def test_unreachable_admin_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point to a definitely-unreachable port on localhost.
    monkeypatch.setenv("KGSPIN_ADMIN_URL", "http://127.0.0.1:1")
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    client = HttpResourceRegistryClient()
    try:
        with pytest.raises(RuntimeError) as excinfo:
            client.register_corpus_document(
                metadata=_sample_corpus_doc_metadata(),
                pointer=FilePointer(value="/tmp/raw.html"),
                actor="fetcher:edgar",
            )
        assert "unreachable" in str(excinfo.value).lower()
    finally:
        client.close()


# -------- 5. X-Actor header on every POST ---------------------------------


def test_x_actor_header_is_set_on_every_post(
    admin_url_localhost: str, httpx_mock
) -> None:
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    httpx_mock.add_response(
        method="POST",
        url=f"{admin_url_localhost}/resources/corpus_document",
        json=_sample_resource(ResourceKind.CORPUS_DOCUMENT, "doc-1"),
        status_code=201,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{admin_url_localhost}/resources/fetcher",
        json=_sample_resource(ResourceKind.FETCHER, "fetcher-1"),
        status_code=201,
    )

    client = HttpResourceRegistryClient()
    try:
        client.register_corpus_document(
            metadata=_sample_corpus_doc_metadata(),
            pointer=FilePointer(value="/tmp/raw.html"),
            actor="fetcher:edgar",
        )
        client.register_fetcher(
            metadata=_sample_fetcher_metadata(),
            actor="demo:packager",
        )
    finally:
        client.close()

    # Walk every recorded request and assert X-Actor was set.
    requests = httpx_mock.get_requests()
    assert len(requests) == 2, f"expected 2 POSTs, got {len(requests)}"
    assert requests[0].headers.get("X-Actor") == "fetcher:edgar"
    assert requests[1].headers.get("X-Actor") == "demo:packager"


# -------- 6. 409 Conflict on register_fetcher → existing Resource ---------


def test_register_fetcher_409_returns_existing_resource(
    admin_url_localhost: str, httpx_mock
) -> None:
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    existing = _sample_resource(ResourceKind.FETCHER, "fetcher:edgar")
    httpx_mock.add_response(
        method="POST",
        url=f"{admin_url_localhost}/resources/fetcher",
        json=existing,
        status_code=409,
    )

    client = HttpResourceRegistryClient()
    try:
        # Should NOT raise — 409 collapses to the existing Resource.
        result = client.register_fetcher(
            metadata=_sample_fetcher_metadata(),
            actor="demo:packager",
        )
        assert isinstance(result, Resource)
        assert result.id == "fetcher:edgar"
    finally:
        client.close()


# -------- 7. Non-loopback http:// URL → construction error ----------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://admin.example.com",
        "http://admin.example.com:8750",
        "http://192.168.1.10:8750",
    ],
)
def test_non_loopback_http_refused_at_construction(
    monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    monkeypatch.setenv("KGSPIN_ADMIN_URL", bad_url)
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    with pytest.raises(RuntimeError) as excinfo:
        HttpResourceRegistryClient()
    msg = str(excinfo.value).lower()
    assert "loopback" in msg or "http" in msg


@pytest.mark.parametrize(
    "good_url",
    [
        "http://127.0.0.1:8750",
        "http://localhost:8750",
        "https://admin.example.com",
        "https://admin.example.com:8750",
    ],
)
def test_loopback_or_https_accepted(
    monkeypatch: pytest.MonkeyPatch, good_url: str
) -> None:
    monkeypatch.setenv("KGSPIN_ADMIN_URL", good_url)
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient
    # Should not raise.
    client = HttpResourceRegistryClient()
    client.close()


# -------- 8. register_fetcher body shape matches admin's RegisterFetcherRequest --


def test_register_fetcher_body_includes_custom_pointer(
    admin_url_localhost: str, httpx_mock
) -> None:
    """Admin's RegisterFetcherRequest has extra='forbid' and requires both
    `metadata` and `pointer` — verified against
    kgspin-admin/tests/http/test_routes_resources.py:253.
    The Protocol signature omits `pointer`, so the adapter must derive a
    CustomPointer(scheme='python-module', value=<module_path>) from the
    spec and include it in the POST body.
    """
    from kgspin_demo_app.registry_http import HttpResourceRegistryClient

    httpx_mock.add_response(
        method="POST",
        url=f"{admin_url_localhost}/resources/fetcher",
        json=_sample_resource(ResourceKind.FETCHER, "fetcher:edgar"),
        status_code=201,
    )

    client = HttpResourceRegistryClient()
    try:
        client.register_fetcher(_sample_fetcher_metadata(), actor="demo:packager")
    finally:
        client.close()

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    import json
    body = json.loads(requests[0].content)
    assert "metadata" in body and "pointer" in body, \
        f"POST body must include both metadata + pointer (admin forbids extras). Got keys: {list(body)}"
    # Pointer shape: CustomPointer(scheme="python-module", value="<module_path>")
    assert body["pointer"]["type"] == "custom"
    assert body["pointer"]["scheme"] == "python-module"
    assert body["pointer"]["value"] == "kgspin_demo_app.landers.sec:SecLander"
