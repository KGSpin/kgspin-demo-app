"""Shared pytest fixtures for the kgspin-demo test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _configured_demo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Seed a minimal ``config.yaml`` per test + point ``KGSPIN_DEMO_CONFIG`` at it.

    ADR-001 wires ``kgspin_demo_app.config.bootstrap_cli()`` into every CLI ``main()``.
    Without this fixture each test that enters ``main()`` would trip the
    first-run bootstrap and ``SystemExit(1)``. Seeding a tempdir config per test
    also isolates tests from any real ``config.yaml`` the developer may have
    created in the repo root.

    Also unsets any legacy env vars that ``apply_settings_to_env`` may have
    leaked into ``os.environ`` from a previous test — those writes bypass
    monkeypatch and persist across the suite without this reset.
    """
    for env_name in (
        "KGSPIN_CORPUS_ROOT",
        "KGEN_BUNDLES_DIR",
        "KGEN_DEFAULT_BUNDLE",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    # Reset the module-global bridge/warning state so cross-test bleed doesn't
    # hide re-layer bugs or double-warnings.
    from kgspin_demo_app import config as _cfg
    _cfg._bridge_applied.clear()
    _cfg._warned_envs.clear()

    # Sprint 0.5.4: reset the lazy LLM resolver + cached settings so a test
    # that injects a fake resolver doesn't leak into the next test.
    from kgspin_demo_app import llm_backend as _llm
    _llm.reset_resolver_for_tests(None)
    _llm.reset_settings_for_tests(None)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "storage:\n"
        "  corpus_root: \"\"\n"
        "  bundles_dir: \".bundles\"\n"
        "security:\n"
        "  cors_origins:\n"
        "    - \"*\"\n"
        "features:\n"
        "  default_bundle: \"\"\n"
    )
    monkeypatch.setenv("KGSPIN_DEMO_CONFIG", str(config_path))
    return config_path
