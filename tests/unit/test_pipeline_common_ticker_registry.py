"""Wave J / PRD-056 v2 — hub-registry ticker resolution (commit 6).

Covers the ``resolve_ticker`` rewrite + the new ``list_registered_tickers``.
The hardcoded ``KNOWN_TICKERS`` dict was retired here; admin's
``/registry/hubs`` is the sole source of truth. On admin unreachable we
raise ``AdminServiceUnreachableError`` instead of silently falling back
(PRD directive).
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

_DEMO_DIR = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))


@pytest.fixture
def pipeline_common():
    import pipeline_common as _pc  # noqa: WPS433
    # Reset the in-process ticker cache between tests.
    _pc._ticker_registry_cache.clear()
    return _pc


class _FakeResp:
    def __init__(self, body): self._body = body
    def read(self): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _fake_urlopen_factory(domain_to_hubs: dict[str, list[dict]]):
    def _fake(url, timeout=5):
        import urllib.parse
        qs = urllib.parse.urlparse(url).query
        params = dict(urllib.parse.parse_qsl(qs))
        domain = params.get("domain", "")
        return _FakeResp(
            json.dumps({"hubs": domain_to_hubs.get(domain, [])}).encode("utf-8")
        )
    return _fake


def _jnj_hub():
    return {
        "canonical_name": "Johnson & Johnson",
        "aliases": ["JNJ", "J&J"],
        "ticker": "JNJ",
        "cik": "0000200406",
        "entity_type": "ORGANIZATION",
        "source_bundles": ["financial-v2"],
        "domain": "financial",
    }


def _mrk_hub():
    return {
        "canonical_name": "Merck",
        "aliases": ["MRK"],
        "ticker": "MRK",
        "cik": "0000310158",
        "entity_type": "ORGANIZATION",
        "source_bundles": ["clinical-v2"],
        "domain": "clinical",
    }


def test_resolve_ticker_uses_hub_registry(pipeline_common, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_factory({"financial": [_jnj_hub()], "clinical": []}),
    )
    info = pipeline_common.resolve_ticker("JNJ")
    assert info["name"] == "Johnson & Johnson"
    assert info["domain"] == "financial"
    assert info["ticker"] == "JNJ"
    assert "data_path" in info


def test_resolve_ticker_finds_clinical_hub_when_not_in_financial(pipeline_common, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_factory({"financial": [], "clinical": [_mrk_hub()]}),
    )
    info = pipeline_common.resolve_ticker("MRK")
    assert info["name"] == "Merck"
    assert info["domain"] == "clinical"


def test_resolve_ticker_caches_across_calls(pipeline_common, monkeypatch):
    call_count = {"n": 0}

    def _counting_urlopen(url, timeout=5):
        call_count["n"] += 1
        return _fake_urlopen_factory({"financial": [_jnj_hub()]})(url, timeout=timeout)

    monkeypatch.setattr("urllib.request.urlopen", _counting_urlopen)
    pipeline_common.resolve_ticker("JNJ")
    before = call_count["n"]
    pipeline_common.resolve_ticker("JNJ")
    assert call_count["n"] == before, "second call should hit the in-process cache"


def test_resolve_ticker_raises_on_admin_unreachable(pipeline_common, monkeypatch):
    def _explode(*args, **kwargs):
        raise URLError("admin offline")

    monkeypatch.setattr("urllib.request.urlopen", _explode)

    from kgspin_core.registry_client import AdminServiceUnreachableError
    with pytest.raises(AdminServiceUnreachableError) as excinfo:
        pipeline_common.resolve_ticker("JNJ")
    assert excinfo.value.operation == "resolve_ticker"


def test_resolve_ticker_alias_match(pipeline_common, monkeypatch):
    # "J&J" is an alias for Johnson & Johnson in the hub row.
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_factory({"financial": [_jnj_hub()], "clinical": []}),
    )
    info = pipeline_common.resolve_ticker("J&J")
    assert info["name"] == "Johnson & Johnson"
    assert info["ticker"] == "J&J"


def test_resolve_ticker_company_name_shortcut_skips_admin(pipeline_common, monkeypatch):
    def _explode(*a, **kw):
        raise AssertionError("admin should not be called when company_name given")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    info = pipeline_common.resolve_ticker("XYZ", company_name="XYZ Corp")
    assert info == {
        "name": "XYZ Corp",
        "domain": "financial",
        "ticker": "XYZ",
        "data_path": info["data_path"],  # path differs per install
    }


def test_list_registered_tickers_returns_sorted(pipeline_common, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen_factory({
            "financial": [_jnj_hub(),
                          {**_jnj_hub(), "ticker": "AAPL", "canonical_name": "Apple"}],
        }),
    )
    out = pipeline_common.list_registered_tickers("financial")
    assert out == ["AAPL", "JNJ"]


def test_list_registered_tickers_raises_on_admin_unreachable(pipeline_common, monkeypatch):
    def _explode(*a, **kw):
        raise URLError("admin offline")

    monkeypatch.setattr("urllib.request.urlopen", _explode)

    from kgspin_core.registry_client import AdminServiceUnreachableError
    with pytest.raises(AdminServiceUnreachableError):
        pipeline_common.list_registered_tickers("financial")


def test_known_tickers_dict_is_deleted(pipeline_common):
    # Regression guard: the retired KNOWN_TICKERS export must not come back.
    assert not hasattr(pipeline_common, "KNOWN_TICKERS")
