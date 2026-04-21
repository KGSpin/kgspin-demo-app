"""Unit tests for kgspin_demo_app.config — ADR-001 Phase 2 adoption."""

from __future__ import annotations

from pathlib import Path

import pytest

from kgspin_demo_app.config import (
    ConfigBootstrapError,
    AppSettings,
    apply_settings_to_env,
    load_settings,
)


# The global tests/conftest.py `_configured_demo` fixture already resets
# _bridge_applied + _warned_envs and delenvs the legacy surface per test.
# No extra autouse needed here.


def _write_config(path: Path, body: str) -> None:
    path.write_text(body)


def test_bootstrap_first_run_copies_template_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing config.yaml triggers bootstrap: template copied, exit(1)."""
    # tmp_path / "config.yaml" is what the conftest fixture seeds; use a
    # different path here so we actually exercise the "missing" branch.
    config_path = tmp_path / "nested" / "config.yaml"
    monkeypatch.delenv("KGSPIN_DEMO_CONFIG", raising=False)
    with pytest.raises(ConfigBootstrapError) as exc_info:
        load_settings(config_path=config_path)
    assert config_path.exists(), "bootstrap should have copied the template"
    assert exc_info.value.code == 1
    assert "Created" in exc_info.value.message
    assert "KGSPIN_DEMO_CONFIG" in exc_info.value.message


def test_load_settings_reads_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        "storage:\n"
        "  corpus_root: \"/srv/corpus\"\n"
        "  bundles_dir: \"/srv/bundles\"\n"
        "security:\n"
        "  cors_origins: [\"https://a.example\", \"https://b.example\"]\n"
        "features:\n"
        "  default_bundle: \"financial-v4.1.0-structural\"\n",
    )
    settings = load_settings(config_path=config_path)
    assert isinstance(settings, AppSettings)
    assert settings.storage.corpus_root == "/srv/corpus"
    assert settings.storage.bundles_dir == "/srv/bundles"
    assert settings.security.cors_origins == ["https://a.example", "https://b.example"]
    assert settings.features.default_bundle == "financial-v4.1.0-structural"


def test_extra_keys_rejected_loudly(tmp_path: Path) -> None:
    """ADR-001 gotcha: typos in config.yaml fail via extra='forbid'."""
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        "storage:\n"
        "  corpus_root: \"\"\n"
        "  bundles_dr: \".bundles\"\n",  # typo — "dr" instead of "dir"
    )
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_path=config_path)
    assert "bundles_dr" in str(exc_info.value)


def test_changeme_placeholder_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        "storage:\n"
        "  corpus_root: \"<CHANGE_ME_TO_YOUR_CORPUS>\"\n",
    )
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        load_settings(config_path=config_path)
    assert "<CHANGE_ME" in str(exc_info.value)


def test_legacy_env_overrides_config_with_deprecation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy env vars win over config.yaml + emit DeprecationWarning once."""
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        "storage:\n"
        "  corpus_root: \"/from-config\"\n",
    )
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", "/from-env")
    with pytest.warns(DeprecationWarning, match="KGSPIN_CORPUS_ROOT"):
        settings = load_settings(config_path=config_path)
    assert settings.storage.corpus_root == "/from-env"


def test_bridge_roundtrip_does_not_re_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_settings_to_env writes bridged vars, but a subsequent
    load_settings must not flag them as legacy (they didn't come from
    the operator — we wrote them ourselves)."""
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        "storage:\n"
        "  corpus_root: \"/from-config\"\n"
        "  bundles_dir: \"/from-config/bundles\"\n"
        "security:\n"
        "  cors_origins: [\"https://only.example\"]\n",
    )
    # Ensure no legacy vars are pre-set.
    for v in ("KGSPIN_CORPUS_ROOT", "KGEN_BUNDLES_DIR", "CORS_ORIGINS",
              "KGEN_DEFAULT_BUNDLE"):
        monkeypatch.delenv(v, raising=False)

    settings = load_settings(config_path=config_path)
    apply_settings_to_env(settings)
    # Bridge pushed the values into the legacy surface.
    import os as _os
    assert _os.environ["KGSPIN_CORPUS_ROOT"] == "/from-config"
    assert _os.environ["KGEN_BUNDLES_DIR"] == "/from-config/bundles"
    assert _os.environ["CORS_ORIGINS"] == "https://only.example"

    # Re-loading must NOT warn — those env vars came from us, not the operator.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any DeprecationWarning = test fail
        settings2 = load_settings(config_path=config_path)
    assert settings2.storage.corpus_root == "/from-config"


def test_empty_defaults_do_not_pollute_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default empty-string values (``storage.corpus_root``,
    ``features.default_bundle``) must NOT be pushed into env — that would
    mask the legacy readers' own defaults (e.g., ~/.kgspin/corpus)."""
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, "")  # all defaults
    for v in ("KGSPIN_CORPUS_ROOT", "KGEN_BUNDLES_DIR", "CORS_ORIGINS",
              "KGEN_DEFAULT_BUNDLE"):
        monkeypatch.delenv(v, raising=False)
    settings = load_settings(config_path=config_path)
    apply_settings_to_env(settings)

    import os as _os
    assert "KGSPIN_CORPUS_ROOT" not in _os.environ
    assert "KGEN_DEFAULT_BUNDLE" not in _os.environ
    # bundles_dir default is ".bundles" — non-empty, so this one IS bridged.
    assert _os.environ["KGEN_BUNDLES_DIR"] == ".bundles"
    # cors_origins default is ["*"] — bridged as "*".
    assert _os.environ["CORS_ORIGINS"] == "*"


def test_config_path_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KGSPIN_DEMO_CONFIG=<path> overrides the CWD default."""
    custom = tmp_path / "custom" / "config.yaml"
    custom.parent.mkdir()
    _write_config(
        custom,
        "storage:\n"
        "  corpus_root: \"/via-env-path\"\n",
    )
    monkeypatch.setenv("KGSPIN_DEMO_CONFIG", str(custom))
    settings = load_settings()
    assert settings.storage.corpus_root == "/via-env-path"


def test_cli_overrides_win(tmp_path: Path) -> None:
    """cli_overrides are applied last and win over everything."""
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        "storage:\n"
        "  corpus_root: \"/from-config\"\n",
    )
    settings = load_settings(
        config_path=config_path,
        cli_overrides={"storage.corpus_root": "/from-cli"},
    )
    assert settings.storage.corpus_root == "/from-cli"
