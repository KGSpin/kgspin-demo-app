"""Regression for ADR-038 preprocessor migration in `_parse_and_chunk`.

`kgspin-core` Sprint 14 (commit e951c84, 2026-05-02) replaced the legacy
`kgspin_core.execution.preprocessors.resolve_preprocessors` symbol with
the `PreprocessorPipeline` API in `kgspin_core.preprocessing`. The demo's
`_parse_and_chunk` was the only stale callsite and broke a live demo
with `ImportError: cannot import name 'resolve_preprocessors'`. This
test pins the new wiring so the import path is exercised in CI.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest


_DEMO_PATH = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_PATH) not in sys.path:
    sys.path.insert(0, str(_DEMO_PATH))


@dataclass
class _StubBundle:
    """Minimal duck-typed bundle for ADR-038 pipeline construction.

    Empty `preprocessors` list → `build_pipeline_from_bundle` returns an
    empty pipeline that no-ops via UTF-8 round-trip (no admin fetch).
    """

    domain: str = "test"
    version: str = "v0"
    max_chunk_size: int = 4000
    preprocessors: list = field(default_factory=list)


def test_parse_and_chunk_uses_adr038_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_parse_and_chunk` imports cleanly and runs the new pipeline.

    Empty-preprocessor bundle exercises the legitimate empty-chain path:
    `PreprocessorPipeline` is constructed, `run_on_bytes` decodes the
    bytes back to text, and the rest of the chunking pipeline produces
    a non-empty result. The point of the assertion is that the legacy
    `resolve_preprocessors` import is no longer present.
    """
    import demo_compare as dc  # type: ignore

    monkeypatch.setattr(dc, "_get_bundle", lambda *_a, **_kw: _StubBundle())

    html = (
        "<html><body>"
        "<p>The quick brown fox jumps over the lazy dog. " * 10
        + "</p></body></html>"
    )

    bundle, full_text, truncated_text, actual_kb, all_chunks = dc._parse_and_chunk(
        html, "TEST", corpus_kb=0,
    )

    assert isinstance(bundle, _StubBundle)
    assert "quick brown fox" in full_text
    assert "quick brown fox" in truncated_text
    assert actual_kb > 0
    assert len(all_chunks) > 0
