"""End-to-end smoke for the fan_out / fan_out_trained comparison story.

Drives both pipelines through the demo's :func:`_run_kgenskills`
dispatch on the canned AAPL 10-K Products excerpt and asserts the
trained pipeline produces a non-trivial entity set the heuristic
pipeline did not. The trained invoker is mocked at the
``resolve_and_register_entity_recognition_model`` boundary because
the v0.4 Phi-3 adapter weights are not committed to this repo —
the smoke proves the integration is wired, not the model's quality
(that's the morphology repo's smoke).

Marked ``slow`` + ``requires_local_model`` so it stays out of the
default CI suite. Run via::

    pytest -m slow tests/extraction/test_trained_compare_smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kgspin_demo_app.compare_diff import compute_trained_diff


_DEMO_PATH = Path(__file__).resolve().parents[2] / "demos" / "extraction"
if str(_DEMO_PATH) not in sys.path:
    sys.path.insert(0, str(_DEMO_PATH))


_PASSAGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "extraction-passages" / "AAPL-10K-P1.txt"
)


def _build_fake_invoker(passage: str):
    """Build a fake PhiAdapterEntityRecognitionInvoker that returns
    canned spans for the AAPL Products passage.

    Each canned span is a (substring, type) pair; we look up the first
    occurrence of the substring in the passed-in chunk text and emit an
    ``InvocationRecord`` with chunk-relative offsets. This mimics what
    the real Phi-3 BIO decoder would emit on this passage.
    """
    from kgspin_core.execution.domain_model_invoker import InvocationRecord
    from kgspin_interface.models import (
        EntityRecognitionOutput,
        ResolvedModelRef,
    )

    canned_spans = [
        ("iPhone 16 Pro", "COMMERCIAL_OFFERING"),
        ("MacBook Air", "COMMERCIAL_OFFERING"),
        ("MacBook Pro", "COMMERCIAL_OFFERING"),
        ("iPad Pro", "COMMERCIAL_OFFERING"),
        ("Apple Watch Ultra", "COMMERCIAL_OFFERING"),
        ("AirPods Pro", "COMMERCIAL_OFFERING"),
        ("Apple Vision Pro", "COMMERCIAL_OFFERING"),
        ("HomePod mini", "COMMERCIAL_OFFERING"),
        ("Apple Inc.", "COMPANY"),
    ]

    resolved = ResolvedModelRef(
        task="entity-recognition",
        uri="model://financial/entity-recognition/v0.4.0",
        weight_hash="0" * 64,
        training_manifest_uri="manifest://financial/entity-recognition/v0.4.0",
    )

    class _FakeInvoker:
        def invoke_passage(self, chunk_text: str):
            out: list[tuple] = []
            for surface, type_label in canned_spans:
                offset = chunk_text.find(surface)
                if offset < 0:
                    continue
                output = EntityRecognitionOutput(
                    span_text=surface,
                    char_offset=offset,
                    char_len=len(surface),
                    type_scores={type_label: 1.0},
                    confidence=0.95,
                )
                out.append((
                    InvocationRecord(output=output, resolved_model=resolved),
                    offset,
                ))
            return out

    return _FakeInvoker()


@pytest.fixture
def _passage() -> str:
    return _PASSAGE_PATH.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _demo_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed a minimal demo config so demo_compare imports cleanly."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "storage:\n  corpus_root: \"\"\n  bundles_dir: \".bundles\"\n"
        "security:\n  cors_origins:\n    - \"*\"\n"
        "features:\n  default_bundle: \"\"\n"
    )
    monkeypatch.setenv("KGSPIN_DEMO_CONFIG", str(cfg))


@pytest.mark.slow
@pytest.mark.requires_local_model
def test_trained_compare_smoke(_passage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """fan_out + fan_out_trained both run on the AAPL passage; their
    outputs differ; the diff helper resolves a meaningful comparison."""
    import demo_compare as dc  # type: ignore
    from extraction.kgen import _run_kgenskills

    bundle = dc._get_bundle()
    # Synthetic entity_recognition_model field — the resolve fn is
    # monkey-patched below, so the dict shape is what would land in a
    # real bundle but the values are placeholders.
    bundle.entity_recognition_model = {
        "ref": "domain_models/financial/entity-recognition/v0.4.0",
        "invoker": "phi_adapter",
    }
    registry = dc._get_registry_client()

    # Run fan_out (heuristic baseline). May return 0 entities on this
    # specific passage — the Products section is noun-heavy with no
    # verb-driven relationship anchors. That is a feature of the demo,
    # not a bug: it's exactly what the trained pipeline corrects.
    fan_out_kg = _run_kgenskills(
        _passage, "Apple Inc.", "AAPL", bundle,
        dc._pipeline_ref_from_strategy("fan_out"),
        registry,
    )
    fan_out_entities = fan_out_kg.get("entities", []) or []

    # Run fan_out_trained with a mocked invoker. The extractor's
    # __init__ does ``from kgspin_core.execution.trained_pipeline_setup
    # import resolve_and_register_entity_recognition_model``, so we
    # patch the source module — the local-binding import resolves
    # against the patched attribute on every construction.
    import kgspin_core.execution.trained_pipeline_setup as setup_mod
    fake_invoker = _build_fake_invoker(_passage)
    monkeypatch.setattr(
        setup_mod, "resolve_and_register_entity_recognition_model",
        lambda *_a, **_kw: fake_invoker,
    )

    # The admin registry doesn't have a ``fan-out-trained`` pipeline
    # config registered yet (cross-repo prerequisite landing in
    # parallel). Patch the resolver to return a synthetic config so
    # the smoke can drive the extractor end-to-end without the admin
    # round-trip.
    from kgspin_interface.pipelines import FanOutTrainedExtractorConfig
    import kgspin_core.execution.pipeline_resolver_ref as resolver_mod

    synthetic_cfg = FanOutTrainedExtractorConfig(
        name="fan-out-trained", version="v1",
        extractor="fan_out_trained",
        description="trained-pipeline smoke synthetic config",
    )
    real_resolver = resolver_mod.load_pipeline_config_via_registry

    def _resolver(ref, *args, **kwargs):
        if ref.name == "fan-out-trained":
            return synthetic_cfg
        return real_resolver(ref, *args, **kwargs)

    monkeypatch.setattr(
        resolver_mod, "load_pipeline_config_via_registry", _resolver,
    )

    trained_kg = _run_kgenskills(
        _passage, "Apple Inc.", "AAPL", bundle,
        dc._pipeline_ref_from_strategy("fan_out_trained"),
        registry,
    )
    trained_entities = trained_kg.get("entities", []) or []

    # The trained pipeline must surface a non-trivial entity set;
    # otherwise the demo's whole comparison story is meaningless.
    assert len(trained_entities) > 0, (
        f"fan_out_trained returned 0 entities on the AAPL passage; "
        f"comparison demo would be empty. (fan_out: {len(fan_out_entities)})"
    )

    # The two pipelines must produce differentiable output. Identical
    # output here would mean either the diff logic is wrong or one of
    # the pipelines isn't actually wired through.
    diff = compute_trained_diff(
        {"kg": fan_out_kg}, {"kg": trained_kg},
    )
    differentiable = len(diff["only_in_a"]) + len(diff["only_in_b"]) > 0
    assert differentiable, (
        f"fan_out and fan_out_trained produced identical output: "
        f"{[e.get('text') for e in fan_out_entities]}"
    )
