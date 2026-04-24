"""Defect 1 fix (2026-04-24) — filer metadata plumbing from SEC lander → H-module.

Covers two wires:

  1. `_adapt_to_sec_doc_shape` reads the post-Wave-A SEC lander keys
     (`company_name_as_filed` top-level, nested `company.canonical_name`).
     Legacy `company_name` stays in the fallback chain; ticker-echo is
     truly last-resort.

  2. `_run_agentic_flash` / `_run_agentic_analyst` forward their new
     `document_metadata` kwarg to `run_pipeline`. Without this, the
     H-module resolver falls back to an ALL-CAPS regex that misses
     mixed-case filers (UnitedHealth Group Incorporated, Apple Inc.,
     NVIDIA Corporation) and the coref map becomes we → UNKNOWN.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_DEMO_DIR = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))


@pytest.fixture
def demo_compare():
    import demo_compare as _dc  # noqa: WPS433
    return _dc


# --- Cache adapter (_adapt_to_sec_doc_shape) --------------------------------


def test_adapter_uses_company_name_as_filed_when_present(demo_compare):
    """Top-level `company_name_as_filed` wins — mixed-case mode for 2023+ filings."""
    metadata = {
        "identifier": {"ticker": "UNH"},
        "source_extras": {
            "company_name_as_filed": "UnitedHealth Group Incorporated",
            "company": {"canonical_name": "UNITEDHEALTH GROUP"},
            "cik": "0000731766",
            "filing_date": "2025-02-14",
            "accession_number": "0000731766-25-000002",
        },
    }
    doc = demo_compare._adapt_to_sec_doc_shape(b"<html/>", metadata, "UNH")
    assert doc.company_name == "UnitedHealth Group Incorporated"
    assert doc.cik == "0000731766"
    assert doc.accession_number == "0000731766-25-000002"
    assert doc.loaded_from_cache is True


def test_adapter_falls_back_to_company_canonical_name(demo_compare):
    """Nested `company.canonical_name` fires when `company_name_as_filed` is absent."""
    metadata = {
        "identifier": {"ticker": "NVDA"},
        "source_extras": {
            "company": {"canonical_name": "NVIDIA CORPORATION"},
            "cik": "0001045810",
        },
    }
    doc = demo_compare._adapt_to_sec_doc_shape(b"<html/>", metadata, "NVDA")
    assert doc.company_name == "NVIDIA CORPORATION"


def test_adapter_honors_legacy_company_name_key(demo_compare):
    """Pre-Wave-A caches with flat `company_name` still resolve correctly."""
    metadata = {
        "identifier": {"ticker": "MSFT"},
        "source_extras": {"company_name": "Microsoft Corporation"},
    }
    doc = demo_compare._adapt_to_sec_doc_shape(b"<html/>", metadata, "MSFT")
    assert doc.company_name == "Microsoft Corporation"


def test_adapter_ticker_echo_is_truly_last_resort(demo_compare):
    """With no canonical-name keys at all, the ticker is the final fallback."""
    metadata = {
        "identifier": {},
        "source_extras": {},
    }
    doc = demo_compare._adapt_to_sec_doc_shape(b"<html/>", metadata, "XYZ")
    assert doc.company_name == "XYZ"


def test_adapter_company_name_as_filed_beats_canonical(demo_compare):
    """Priority order: `company_name_as_filed` is preferred when both are set."""
    metadata = {
        "identifier": {"ticker": "AAPL"},
        "source_extras": {
            "company_name_as_filed": "Apple Inc.",
            "company": {"canonical_name": "APPLE INC"},
        },
    }
    doc = demo_compare._adapt_to_sec_doc_shape(b"<html/>", metadata, "AAPL")
    assert doc.company_name == "Apple Inc."


# --- document_metadata plumbing through _run_agentic_flash / _analyst -------


class _StubResult:
    """Minimal ExtractionResult stand-in for the extractor contract."""
    def __init__(self):
        self.provenance = SimpleNamespace(tokens_used=0)

    def to_dict(self):
        return {"entities": [], "relationships": []}


def test_run_agentic_flash_forwards_document_metadata(monkeypatch):
    """`_run_agentic_flash(document_metadata=...)` reaches `run_pipeline` intact."""
    import extraction.agentic as agentic

    captured = {}

    class _FakeExtractor:
        def __init__(self, bundle): pass
        def run_pipeline(self, **kwargs):
            captured.update(kwargs)
            return _StubResult()

    monkeypatch.setattr(
        "kgspin_core.execution.extractor.KnowledgeGraphExtractor",
        _FakeExtractor,
    )
    monkeypatch.setattr(
        "kgspin_demo_app.llm_backend.resolve_llm_backend",
        lambda **kw: object(),
    )
    monkeypatch.setattr(agentic, "_get_bundle", lambda **kw: MagicMock())
    monkeypatch.setattr(agentic, "_registry_client", lambda: object())
    monkeypatch.setattr(agentic, "_pipeline_ref", lambda s: {"ref": s})

    doc_metadata = {"company_name": "Johnson & Johnson", "doc_id": "JNJ"}
    agentic._run_agentic_flash(
        text="hello",
        company_name="Johnson & Johnson",
        source_id="JNJ_10K",
        document_metadata=doc_metadata,
    )

    assert captured["document_metadata"] == doc_metadata
    assert captured["main_entity"] == "Johnson & Johnson"


def test_run_agentic_analyst_forwards_document_metadata(monkeypatch):
    """`_run_agentic_analyst(document_metadata=...)` reaches `run_pipeline` intact."""
    import extraction.agentic as agentic

    captured = {}

    class _FakeExtractor:
        def __init__(self, bundle): pass
        def run_pipeline(self, **kwargs):
            captured.update(kwargs)
            return _StubResult()

    monkeypatch.setattr(
        "kgspin_core.execution.extractor.KnowledgeGraphExtractor",
        _FakeExtractor,
    )
    monkeypatch.setattr(
        "kgspin_demo_app.llm_backend.resolve_llm_backend",
        lambda **kw: object(),
    )
    monkeypatch.setattr(agentic, "_get_bundle", lambda **kw: MagicMock())
    monkeypatch.setattr(agentic, "_registry_client", lambda: object())
    monkeypatch.setattr(agentic, "_pipeline_ref", lambda s: {"ref": s})

    doc_metadata = {"company_name": "UnitedHealth Group", "doc_id": "UNH"}
    agentic._run_agentic_analyst(
        text="hello",
        company_name="UnitedHealth Group",
        source_id="UNH_10K",
        document_metadata=doc_metadata,
    )

    assert captured["document_metadata"] == doc_metadata
    assert captured["main_entity"] == "UnitedHealth Group"


def test_run_agentic_flash_preserves_backward_compat_without_metadata(monkeypatch):
    """Calling without `document_metadata` passes `None` — backward compat."""
    import extraction.agentic as agentic

    captured = {}

    class _FakeExtractor:
        def __init__(self, bundle): pass
        def run_pipeline(self, **kwargs):
            captured.update(kwargs)
            return _StubResult()

    monkeypatch.setattr(
        "kgspin_core.execution.extractor.KnowledgeGraphExtractor",
        _FakeExtractor,
    )
    monkeypatch.setattr(
        "kgspin_demo_app.llm_backend.resolve_llm_backend",
        lambda **kw: object(),
    )
    monkeypatch.setattr(agentic, "_get_bundle", lambda **kw: MagicMock())
    monkeypatch.setattr(agentic, "_registry_client", lambda: object())
    monkeypatch.setattr(agentic, "_pipeline_ref", lambda s: {"ref": s})

    agentic._run_agentic_flash(
        text="hello",
        company_name="Apple Inc.",
        source_id="AAPL_10K",
    )
    assert captured["document_metadata"] is None
