"""Minimal in-process admin HTTP shim for Sprint 09 Task 9 smoke.

NOT shipped — lives under ``tests/manual/`` (not in pyproject's
packages list) per VP Sec audit. Bound only to ``127.0.0.1`` per
VP Sec test-eval criterion.

Implements the subset of admin's HTTP routes the Sprint 09
``HttpResourceRegistryClient`` calls, backed by an in-memory dict.
Enough to exercise the register-then-list round-trip end-to-end
without a real admin instance running.

Usage (from test_smoke_e2e.py or manual shell):

    from tests.manual.admin_shim import start_shim
    with start_shim() as shim:
        # shim.url is e.g. http://127.0.0.1:8750
        os.environ["KGSPIN_ADMIN_URL"] = shim.url
        ...
"""

from __future__ import annotations

import contextlib
import socket
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from kgspin_interface import ids as _ids
from kgspin_interface.registry_client import Resource, ResourceKind, ResourceStatus
from kgspin_interface.resources import Provenance


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store: dict[str, Resource] = {}

    @app.post("/resources/corpus_document", status_code=201)
    async def register_corpus_document(request: Request) -> JSONResponse:
        actor = request.headers.get("X-Actor") or ""
        if not actor.strip():
            raise HTTPException(status_code=400, detail={"title": "Missing X-Actor"})
        body = await request.json()
        meta = body["metadata"]
        pointer = body["pointer"]
        rid = _ids.corpus_document_id(meta["domain"], meta["source"], meta["identifier"])
        if rid in store:
            # Idempotency — return existing.
            return JSONResponse(
                status_code=409, content=store[rid].model_dump(mode="json"),
            )
        resource = Resource(
            id=rid,
            kind=ResourceKind.CORPUS_DOCUMENT,
            pointer=pointer,  # shim trusts the discriminated-union validator
            metadata=meta,
            provenance=Provenance(
                registered_at=datetime.now(timezone.utc),
                registered_by=actor,
                hash=None,
            ),
            status=ResourceStatus.ACTIVE,
        )
        store[rid] = resource
        return JSONResponse(status_code=201, content=resource.model_dump(mode="json"))

    @app.post("/resources/fetcher", status_code=201)
    async def register_fetcher(request: Request) -> JSONResponse:
        actor = request.headers.get("X-Actor") or ""
        if not actor.strip():
            raise HTTPException(status_code=400, detail={"title": "Missing X-Actor"})
        body = await request.json()
        meta = body["metadata"]
        pointer = body["pointer"]
        rid = _ids.fetcher_id(meta["spec"]["fetcher_id"])
        if rid in store:
            return JSONResponse(
                status_code=409, content=store[rid].model_dump(mode="json"),
            )
        resource = Resource(
            id=rid,
            kind=ResourceKind.FETCHER,
            pointer=pointer,
            metadata=meta,
            provenance=Provenance(
                registered_at=datetime.now(timezone.utc),
                registered_by=actor,
                hash=None,
            ),
            status=ResourceStatus.ACTIVE,
        )
        store[rid] = resource
        return JSONResponse(status_code=201, content=resource.model_dump(mode="json"))

    @app.get("/resources/{resource_id:path}")
    async def get_resource(resource_id: str) -> JSONResponse:
        r = store.get(resource_id)
        if r is None:
            raise HTTPException(status_code=404, detail={"title": "not found"})
        return JSONResponse(status_code=200, content=r.model_dump(mode="json"))

    @app.get("/resources")
    async def list_resources(kind: str, domain: str | None = None,
                             source: str | None = None, limit: int | None = None) -> JSONResponse:
        try:
            want = ResourceKind(kind)
        except ValueError:
            raise HTTPException(status_code=400, detail={"title": f"invalid kind {kind!r}"})
        out = []
        for r in store.values():
            if r.kind != want:
                continue
            m = r.metadata or {}
            if domain and m.get("domain") != domain:
                continue
            if source and m.get("source") != source:
                continue
            out.append(r.model_dump(mode="json"))
            if limit and len(out) >= limit:
                break
        return JSONResponse(status_code=200, content=out)

    @app.get("/pointer/{resource_id:path}")
    async def get_pointer(resource_id: str) -> JSONResponse:
        r = store.get(resource_id)
        if r is None or r.pointer is None:
            raise HTTPException(status_code=404, detail={"title": "no pointer"})
        return JSONResponse(status_code=200, content=r.pointer.model_dump(mode="json"))

    # Stash the store handle for test introspection.
    app.state.store = store
    return app


class _Shim:
    def __init__(self, url: str, thread: threading.Thread, shutdown_event: threading.Event, store: dict):
        self.url = url
        self._thread = thread
        self._shutdown = shutdown_event
        self.store = store


@contextlib.contextmanager
def start_shim():
    """Spawn the shim on a random 127.0.0.1 port; yield a handle with
    .url + .store. Shuts down cleanly on context exit."""
    import uvicorn
    app = build_app()
    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start accepting connections (≤5s).
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=1)
        raise RuntimeError(f"admin shim didn't come up on 127.0.0.1:{port} within 5s")

    try:
        yield _Shim(url=f"http://127.0.0.1:{port}",
                    thread=thread, shutdown_event=threading.Event(),
                    store=app.state.store)
    finally:
        server.should_exit = True
        thread.join(timeout=2)
