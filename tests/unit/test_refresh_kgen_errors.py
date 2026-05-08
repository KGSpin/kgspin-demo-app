"""SSE error-path tests for _run_kgen_refresh.

When kgspin-core raises a typed ``MissingDomainModelError`` (e.g. the
``fan_out_trained`` pipeline runs against a bundle with no
``entity_recognition_model`` field, or the registry can't resolve the
named model), the refresh handler must surface a structured SSE error
event with ``reason="missing_domain_model"`` so the slot UI renders the
"No trained model registered" overlay. The trained pipeline must never
silent-fall-back to the heuristic path — that would mask a real
misconfiguration during a demo.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


_DEMO_PATH = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_PATH) not in sys.path:
    sys.path.insert(0, str(_DEMO_PATH))


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


async def _drain(agen) -> list[str]:
    out: list[str] = []
    async for chunk in agen:
        out.append(chunk)
    return out


def test_missing_domain_model_emits_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    import demo_compare as dc  # type: ignore
    from kgspin_core.exceptions import MissingDomainModelError

    ticker = "TEST_MDME"

    # Pre-seed the cache so the corpus-fetch branch is skipped.
    with dc._cache_lock:
        dc._kg_cache[ticker] = {
            "text": "Apple Inc. designs the iPhone.",
            "raw_html": "<html><body>Apple Inc. designs the iPhone.</body></html>",
            "info": {"name": "Test Corp"},
            "corpus_kb": 1,
            "actual_kb": 1.0,
            "cfg_hash": "",
            "chunk_size": dc.DEFAULT_CHUNK_SIZE,
        }

    monkeypatch.setattr(dc, "_get_bundle", lambda *_a, **_kw: object())

    def _raise_mdme(*_a, **_kw):
        raise MissingDomainModelError(
            "bundle 'financial-v22d' has no entity_recognition_model"
        )

    monkeypatch.setattr(dc, "_run_kgenskills", _raise_mdme)

    try:
        agen = dc._run_kgen_refresh(
            ticker, _FakeRequest(),
            bundle_name="financial-v22d",
            pipeline_id="fan-out-trained",
        )
        events = asyncio.run(_drain(agen))
    finally:
        with dc._cache_lock:
            dc._kg_cache.pop(ticker, None)

    payload = "".join(events)

    assert "event: error" in payload, payload
    assert '"reason": "missing_domain_model"' in payload, payload
    assert '"error_type": "MissingDomainModelError"' in payload, payload
    assert '"recoverable": false' in payload, payload

    # Ordering: error must precede the trailing done so the slot UI
    # stops the spinner instead of staying stuck on "Re-extracting...".
    err_idx = payload.find("event: error")
    done_idx = payload.find("event: done")
    assert err_idx >= 0 and done_idx > err_idx, payload
