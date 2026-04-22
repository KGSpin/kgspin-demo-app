"""Sprint 10 Task 6 — unit tests for registry-backed demo_compare reads.

Exercises ``_try_corpus_fetch``, ``_fetch_newsapi_articles``, and the
post-subprocess poll helper against a ``FakeRegistryClient``. No live
admin, no subprocesses, no network.

Test matrix covers the acceptance list in sprint-plan.md§Task 6 plus
the Task 4 poll happy/timeout paths and Task 7 admin-down exception
handler coverage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kgspin_interface.registry_client import CorpusDocumentMetadata
from kgspin_interface.resources import CustomPointer, FilePointer

from tests.fakes.registry_client import FakeRegistryClient

# Import the module under test. Tests monkeypatch ``_get_registry_client``.
import demos.extraction.demo_compare as dc


# ---- helpers ----------------------------------------------------------------


def _seed_sec(
    fake: FakeRegistryClient,
    tmp_path: Path,
    ticker: str,
    *,
    fetch_time: datetime,
    body: bytes = b"<html>sec fixture</html>",
    form: str = "10-K",
) -> None:
    raw = tmp_path / f"{ticker}.html"
    raw.write_bytes(body)
    fake.register_corpus_document(
        CorpusDocumentMetadata(
            domain="financial",
            source="sec_edgar",
            identifier={"ticker": ticker, "form": form},
            fetch_timestamp=fetch_time,
            source_extras={"cik": "0000200406", "company_name": f"{ticker} Inc"},
        ),
        FilePointer(value=str(raw)),
        "test:sec_lander",
    )


def _seed_clinical(
    fake: FakeRegistryClient,
    tmp_path: Path,
    nct: str,
    *,
    fetch_time: datetime,
    body: bytes = b'{"trial": "stub"}',
) -> None:
    raw = tmp_path / f"{nct}.json"
    raw.write_bytes(body)
    fake.register_corpus_document(
        CorpusDocumentMetadata(
            domain="clinical",
            source="clinicaltrials_gov",
            identifier={"nct": nct},
            fetch_timestamp=fetch_time,
        ),
        FilePointer(value=str(raw)),
        "test:clinical_lander",
    )


def _seed_yahoo(
    fake: FakeRegistryClient,
    tmp_path: Path,
    ticker: str,
    article_id: str,
    *,
    fetch_time: datetime,
    body: str = "headline\nbody text",
) -> None:
    raw = tmp_path / f"{ticker}_{article_id}.txt"
    raw.write_bytes(body.encode())
    fake.register_corpus_document(
        CorpusDocumentMetadata(
            domain="financial",
            source="marketaux",
            identifier={"article_id": article_id, "ticker": ticker},
            fetch_timestamp=fetch_time,
            source_url=f"https://yahoo.example/{article_id}",
        ),
        FilePointer(value=str(raw)),
        "test:yahoo_lander",
    )


def _seed_health_news(
    fake: FakeRegistryClient,
    tmp_path: Path,
    query: str,
    article_id: str,
    *,
    fetch_time: datetime,
) -> None:
    raw = tmp_path / f"{article_id}.txt"
    raw.write_bytes(b"health body")
    fake.register_corpus_document(
        CorpusDocumentMetadata(
            domain="clinical",
            source="newsapi",
            identifier={"article_id": article_id, "query": query},
            fetch_timestamp=fetch_time,
            source_extras={"query": query},
            source_url=f"https://newsapi.example/{article_id}",
        ),
        FilePointer(value=str(raw)),
        "test:health_news_lander",
    )


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistryClient:
    """Install a ``FakeRegistryClient`` in place of the cached HTTP client."""
    fake = FakeRegistryClient()
    monkeypatch.setattr(dc, "_get_registry_client", lambda: fake)
    return fake


@pytest.fixture
def seeded_fake_registry(
    fake_registry: FakeRegistryClient, tmp_path: Path,
) -> FakeRegistryClient:
    """Seed SEC + clinical + news fixtures with distinct timestamps."""
    base = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    _seed_sec(fake_registry, tmp_path, "JNJ", fetch_time=base,
              body=b"<html>older JNJ</html>", form="10-K")
    _seed_sec(fake_registry, tmp_path / "newer", fetch_time=base + timedelta(days=2),
              body=b"<html>newest JNJ</html>", ticker="JNJ", form="10-Q")
    _seed_sec(fake_registry, tmp_path / "aapl", fetch_time=base, ticker="AAPL",
              body=b"<html>AAPL</html>")
    _seed_sec(fake_registry, tmp_path / "msft", fetch_time=base, ticker="MSFT",
              body=b"<html>MSFT</html>")
    _seed_clinical(fake_registry, tmp_path / "nct", "NCT03456076", fetch_time=base,
                   body=b'{"nct": "NCT03456076"}')
    _seed_yahoo(fake_registry, tmp_path / "yahoo", "JNJ", "art_jnj1", fetch_time=base)
    _seed_yahoo(fake_registry, tmp_path / "yahoo2", "JNJ", "art_jnj2",
                fetch_time=base + timedelta(hours=1))
    _seed_health_news(fake_registry, tmp_path / "hn", "lung cancer", "art_hn1",
                      fetch_time=base)
    _seed_health_news(fake_registry, tmp_path / "hn2", "diabetes", "art_hn2",
                      fetch_time=base)
    return fake_registry


@pytest.fixture(autouse=True)
def _ensure_tmp_nested(tmp_path: Path) -> None:
    for p in ("newer", "aapl", "msft", "nct", "yahoo", "yahoo2", "hn", "hn2"):
        (tmp_path / p).mkdir(exist_ok=True)


# ---- Task 1: _try_corpus_fetch ---------------------------------------------


def test_try_corpus_fetch_sec_happy(seeded_fake_registry: FakeRegistryClient) -> None:
    """[MINOR-2] symmetric case normalization: register JNJ, query jnj."""
    doc = dc._try_corpus_fetch("jnj")
    assert "newest JNJ" in doc.raw_html
    assert doc.loaded_from_cache is True


def test_try_corpus_fetch_sec_latest_wins(seeded_fake_registry: FakeRegistryClient) -> None:
    doc = dc._try_corpus_fetch("JNJ")
    assert "newest JNJ" in doc.raw_html
    assert "older JNJ" not in doc.raw_html


def test_try_corpus_fetch_clinical_happy(seeded_fake_registry: FakeRegistryClient) -> None:
    doc = dc._try_corpus_fetch("NCT03456076")
    assert "NCT03456076" in doc.raw_html


def test_try_corpus_fetch_no_match(fake_registry: FakeRegistryClient) -> None:
    with pytest.raises(dc.CorpusFetchError) as exc:
        dc._try_corpus_fetch("UNKNOWN")
    assert exc.value.reason == "no landed artifact"
    assert "SEC EDGAR" in exc.value.actionable_hint
    assert "Auto-land attempt" in exc.value.actionable_hint
    assert exc.value.attempted  # non-empty


def test_try_corpus_fetch_pointer_scheme_mismatch(
    fake_registry: FakeRegistryClient, tmp_path: Path,
) -> None:
    """Register a corpus_document with a CustomPointer — must raise
    CorpusFetchError with a clear 'unexpected pointer scheme' reason."""
    meta = CorpusDocumentMetadata(
        domain="financial",
        source="sec_edgar",
        identifier={"ticker": "XYZ", "form": "10-K"},
        fetch_timestamp=datetime.now(timezone.utc),
    )
    fake_registry.register_corpus_document(
        meta,
        CustomPointer(scheme="custom-scheme", value="foo"),
        "test:scheme_check",
    )
    with pytest.raises(dc.CorpusFetchError) as exc:
        dc._try_corpus_fetch("XYZ")
    assert "unexpected pointer scheme" in exc.value.reason


# ---- Task 2: _fetch_newsapi_articles ---------------------------------------


def test_fetch_newsapi_marketaux_happy(seeded_fake_registry: FakeRegistryClient) -> None:
    """Sprint 11: financial news now reads source=marketaux (was
    newsapi_financial pre-Sprint 11). Case normalization still applies."""
    items = dc._fetch_newsapi_articles(query="jnj", limit=5)
    assert len(items) == 2
    assert all(it.get("text") for it in items)
    assert all(it.get("url", "").startswith("https://yahoo.example") for it in items)


def test_fetch_newsapi_marketaux_limit(seeded_fake_registry: FakeRegistryClient) -> None:
    items = dc._fetch_newsapi_articles(query="JNJ", limit=1)
    assert len(items) == 1


def test_fetch_newsapi_clinical_substring(seeded_fake_registry: FakeRegistryClient) -> None:
    items = dc._fetch_newsapi_articles(query="lung cancer", limit=5)
    assert len(items) == 1
    assert items[0]["text"] == "health body"


def test_fetch_newsapi_no_match(fake_registry: FakeRegistryClient) -> None:
    with pytest.raises(dc.ProviderConfigurationError) as exc:
        dc._fetch_newsapi_articles(query="nothing-matches", limit=5)
    assert "nothing-matches" in exc.value.hint
    assert "kgspin-demo-lander-marketaux" in exc.value.hint


# ---- Task 4: post-subprocess registry poll ---------------------------------


def test_poll_registers_hit(seeded_fake_registry: FakeRegistryClient) -> None:
    """The poll returns the resource id immediately if the registry
    already has a matching freshly-registered resource."""
    import asyncio
    rid = asyncio.run(dc._poll_registry_for_registration(
        domain="financial",
        source="sec_edgar",
        identifier={"ticker": "JNJ"},
    ))
    assert rid is not None


def test_poll_times_out_on_empty(
    fake_registry: FakeRegistryClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unseeded registry causes the poll to time out and return None.
    Max-wait is shrunk to 0.1s so the test stays fast."""
    import asyncio
    monkeypatch.setattr(dc, "_POST_LANDER_POLL_MAX_SEC", 0.1)
    monkeypatch.setattr(dc, "_POST_LANDER_POLL_INTERVAL_SEC", 0.01)
    rid = asyncio.run(dc._poll_registry_for_registration(
        domain="financial",
        source="sec_edgar",
        identifier={"ticker": "NOTHERE"},
    ))
    assert rid is None


def test_poll_ignores_stale_registrations(
    fake_registry: FakeRegistryClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A resource registered more than
    ``_POST_LANDER_POLL_REGISTERED_WINDOW_SEC`` ago is ignored (treated
    as pre-existing rather than a fresh subprocess landing). We force
    this by shrinking the window to 0 seconds."""
    import asyncio
    _seed_sec(fake_registry, tmp_path, "JNJ",
              fetch_time=datetime.now(timezone.utc))
    monkeypatch.setattr(dc, "_POST_LANDER_POLL_REGISTERED_WINDOW_SEC", 0.0)
    monkeypatch.setattr(dc, "_POST_LANDER_POLL_MAX_SEC", 0.1)
    monkeypatch.setattr(dc, "_POST_LANDER_POLL_INTERVAL_SEC", 0.01)
    rid = asyncio.run(dc._poll_registry_for_registration(
        domain="financial",
        source="sec_edgar",
        identifier={"ticker": "JNJ"},
    ))
    assert rid is None


# ---- Task 7: admin-down exception handler ----------------------------------


def _raise_unreachable(*a, **kw):
    raise RuntimeError("admin http://127.0.0.1:9999 unreachable: ConnectionError: refused")


class _StubClient:
    """Minimal client whose methods raise a configurable RuntimeError."""
    def __init__(self, err: str) -> None:
        self._err = err

    def list(self, *a, **kw):
        raise RuntimeError(self._err)

    def resolve_pointer(self, *a, **kw):
        raise RuntimeError(self._err)

    def close(self) -> None:  # pragma: no cover
        pass


def test_handler_catches_admin_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = "admin http://127.0.0.1:9999 unreachable: ConnectionError: refused"
    monkeypatch.setattr(dc, "_get_registry_client", lambda: _StubClient(err))
    # Use an endpoint that calls _get_registry_client on the hot path. The
    # bundle-options endpoint does, via _registry_bundles_or_none — but that
    # helper catches exceptions internally. The news endpoint uses the
    # fetcher function which raises. Exercise by calling _fetch_newsapi_articles
    # directly then the handler via TestClient on a synthetic route.
    with pytest.raises(RuntimeError) as exc:
        dc._fetch_newsapi_articles(query="anything", limit=1)
    assert dc._is_admin_unreachable(exc.value)


@pytest.mark.parametrize("msg,expected", [
    ("admin http://... unreachable: ConnectionError", True),     # conjunctive hit
    ("something else entirely", False),                          # neither token
    ("admin startup failed", False),                             # missing "unreachable"
    ("the network is unreachable", False),                       # missing "admin "
])
def test_admin_unreachable_conjunctive_match(msg: str, expected: bool) -> None:
    """[VP Eng MAJOR-1] The handler predicate is a CONJUNCTIVE match on
    ``"admin "`` AND ``"unreachable"``. Adversarial strings that carry
    only one of those tokens must NOT be caught."""
    assert dc._is_admin_unreachable(RuntimeError(msg)) is expected


def test_admin_unreachable_not_triggered_for_non_runtime_error() -> None:
    assert dc._is_admin_unreachable(ValueError("admin unreachable")) is False


def test_handler_emits_503_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a RuntimeError with the admin-down signature reaches
    the handler via FastAPI and produces a 503 with the hint payload."""
    @dc.app.get("/__test_raise_admin__")
    async def _raise_admin():
        raise RuntimeError("admin http://127.0.0.1:9999 unreachable: boom")
    client = TestClient(dc.app, raise_server_exceptions=False)
    response = client.get("/__test_raise_admin__")
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "Admin unreachable"
    assert "kgspin-admin serve" in body["hint"]


# ---- Task 3: SSE integration — CorpusFetchError → error event -------------


def test_sse_corpus_missing_for_unknown_ticker(
    fake_registry: FakeRegistryClient,
) -> None:
    """Hit an SSE endpoint that funnels through ``_try_corpus_fetch`` with
    an unknown ticker → the SSE stream must emit an ``error`` event
    carrying the actionable hint produced by Task 1.

    The plan (§Task 3) referenced ``/api/refresh-fullshot/{ticker}`` but
    that route does not exist on this branch. ``/api/refresh-agentic-flash``
    is the equivalent entry point on the merged demo: it calls
    ``run_single_refresh`` which is one of the three ``CorpusFetchError``
    handler sites Task 3 guards.

    This exercises the Task 1 ↔ Task 3 contract: CorpusFetchError raised
    by _try_corpus_fetch is caught inside the SSE handler and rendered
    as a structured error event, not a generic 500."""
    client = TestClient(dc.app)
    response = client.get("/api/refresh-agentic-flash/UNKNOWN")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    body = response.text
    assert "event: error" in body
    assert "no landed artifact" in body or "UNKNOWN" in body
