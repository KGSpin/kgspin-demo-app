"""W3-D — dispatch-contract tests for each demo ``_run_*`` path.

Every demo wrapper around ``KnowledgeGraphExtractor.run_pipeline`` must
supply both ``pipeline_config_ref`` and ``registry_client``. Wave 3 core
raises on missing kwargs — these tests pin the demo-side contract by
spying on ``extractor.run_pipeline`` and asserting the kwargs match the
canonical 5.

Scope:
- ``_run_kgenskills`` (zero-LLM baseline; caller passes the ref)
- ``_run_agentic_flash`` → canonical ``agentic-flash`` pipeline
- ``_run_agentic_analyst`` → canonical ``agentic-analyst`` pipeline
- ``_run_clinical_gemini_full_shot`` → canonical ``agentic-flash``
- ``_run_clinical_modular`` → canonical ``agentic-analyst``
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_DEMO_PATH = Path(__file__).parent.parent.parent.parent / "demos" / "extraction"
sys.path.insert(0, str(_DEMO_PATH))
import demo_compare as dc  # type: ignore


@pytest.fixture
def stub_bundle(monkeypatch):
    """Replace ``_get_bundle`` with a stub that returns a sentinel bundle."""
    fake_bundle = SimpleNamespace(name="stub-bundle", max_chunk_size=3000)
    monkeypatch.setattr(dc, "_get_bundle", lambda bundle_name=None: fake_bundle)
    return fake_bundle


@pytest.fixture
def stub_registry_client(monkeypatch):
    """Replace ``_get_registry_client`` with a sentinel client."""
    fake_client = SimpleNamespace(name="stub-registry-client")
    monkeypatch.setattr(dc, "_get_registry_client", lambda: fake_client)
    return fake_client


@pytest.fixture
def spy_extractor(monkeypatch, stub_bundle, stub_registry_client):
    """Replace ``KnowledgeGraphExtractor`` with a spy that records the
    kwargs passed to ``run_pipeline``. Returns the spy instance so tests
    can read ``spy.run_pipeline_kwargs``.
    """
    captured: dict = {}

    fake_result = MagicMock()
    fake_result.to_dict.return_value = {"entities": [], "relationships": []}

    class _SpyExtractor:
        def __init__(self, bundle):
            captured["bundle"] = bundle

        def run_pipeline(self, **kwargs):
            captured["run_pipeline_kwargs"] = kwargs
            return fake_result

    # Patch the import site inside demo_compare's wrappers. Each wrapper
    # does `from kgspin_core.execution.extractor import KnowledgeGraphExtractor`
    # at call time, so patch the module attribute.
    from kgspin_core.execution import extractor as _ext_mod
    monkeypatch.setattr(_ext_mod, "KnowledgeGraphExtractor", _SpyExtractor)

    return captured


@pytest.fixture
def stub_gliner(monkeypatch):
    monkeypatch.setattr(dc, "_get_gliner_backend", lambda: None)


@pytest.fixture
def stub_llm_backend(monkeypatch):
    """Replace ``resolve_llm_backend`` to bypass API-key requirements."""
    import kgspin_demo_app.llm_backend as _llm
    monkeypatch.setattr(_llm, "resolve_llm_backend", lambda **kw: None)


def test_run_kgenskills_passes_ref_and_client(
    spy_extractor, stub_bundle, stub_registry_client, stub_gliner,
) -> None:
    ref = dc._pipeline_ref_from_strategy("fan_out")
    dc._run_kgenskills(
        text="hello world",
        company_name="Acme",
        ticker="TEST",
        bundle=stub_bundle,
        pipeline_config_ref=ref,
        registry_client=stub_registry_client,
    )
    kwargs = spy_extractor["run_pipeline_kwargs"]
    assert kwargs["pipeline_config_ref"] is ref
    assert kwargs["registry_client"] is stub_registry_client


def test_run_agentic_flash_uses_canonical_ref(
    spy_extractor, stub_bundle, stub_registry_client, stub_llm_backend,
) -> None:
    dc._run_agentic_flash(
        text="doc", company_name="Acme", source_id="doc_1",
        llm_alias="test-alias",
    )
    kwargs = spy_extractor["run_pipeline_kwargs"]
    ref = kwargs["pipeline_config_ref"]
    assert ref.name == "agentic-flash"
    assert ref.version == "v1"
    assert kwargs["registry_client"] is stub_registry_client


def test_run_agentic_analyst_uses_canonical_ref(
    spy_extractor, stub_bundle, stub_registry_client, stub_llm_backend,
) -> None:
    dc._run_agentic_analyst(
        text="doc", company_name="Acme", source_id="doc_1",
        llm_alias="test-alias",
    )
    kwargs = spy_extractor["run_pipeline_kwargs"]
    ref = kwargs["pipeline_config_ref"]
    assert ref.name == "agentic-analyst"
    assert ref.version == "v1"
    assert kwargs["registry_client"] is stub_registry_client


def test_run_clinical_full_shot_uses_agentic_flash(
    spy_extractor, stub_bundle, stub_registry_client, stub_llm_backend,
) -> None:
    dc._run_clinical_gemini_full_shot(
        text="trial doc", trial_name="NCT000", source_id="nct_doc",
        llm_alias="test-alias",
    )
    kwargs = spy_extractor["run_pipeline_kwargs"]
    ref = kwargs["pipeline_config_ref"]
    assert ref.name == "agentic-flash"
    assert kwargs["registry_client"] is stub_registry_client


def test_run_clinical_modular_uses_agentic_analyst(
    spy_extractor, stub_bundle, stub_registry_client, stub_llm_backend,
) -> None:
    dc._run_clinical_modular(
        text="trial doc", trial_name="NCT000", source_id="nct_doc",
        llm_alias="test-alias",
    )
    kwargs = spy_extractor["run_pipeline_kwargs"]
    ref = kwargs["pipeline_config_ref"]
    assert ref.name == "agentic-analyst"
    assert kwargs["registry_client"] is stub_registry_client
