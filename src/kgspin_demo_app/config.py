"""Demo runtime configuration surface (ADR-001).

One ``AppSettings`` class declares every structural setting the demo
package reads. The operator configures it by editing ``config.yaml``
(gitignored) generated on first run from the committed
``config.template.yaml``.

Source precedence (highest wins):

1. CLI flags — passed as ``cli_overrides`` to :func:`load_settings`.
2. Legacy env vars — the pre-ADR ``KGSPIN_CORPUS_ROOT`` /
   ``KGEN_BUNDLES_DIR`` / ``KGEN_DEFAULT_BUNDLE`` / ``CORS_ORIGINS``
   names, kept working through the alpha window. Each hit emits a
   ``DeprecationWarning`` on first observation in-process.
3. ``config.yaml`` — operator's per-deployment structural config.
4. ``config.template.yaml`` defaults — baseline shipped with the repo.

Secrets stay in env vars. ``KGSPIN_DEMO_CONFIG`` overrides the
config-file path; ``KGSPIN_ADMIN_URL`` is a client-side env var
(points at admin, which is a different service) and stays env-only.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import warnings
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_FILENAME = "config.yaml"
TEMPLATE_FILENAME = "config.template.yaml"
CONFIG_PATH_ENV = "KGSPIN_DEMO_CONFIG"

_CHANGEME_RE = re.compile(r"^<CHANGE_ME(_[A-Z0-9_]+)?>$")

# Legacy env vars → dotted settings path. Preserved for one release window
# per ADR-001 Risk-3 mitigation; removal is a separate post-alpha sprint.
_LEGACY_ENV_MAP: dict[str, tuple[str, ...]] = {
    "KGSPIN_CORPUS_ROOT": ("storage", "corpus_root"),
    "KGEN_BUNDLES_DIR": ("storage", "bundles_dir"),
    "KGEN_DEFAULT_BUNDLE": ("features", "default_bundle"),
    "CORS_ORIGINS": ("security", "cors_origins"),
}

# Delimiters the legacy names used. Kept here so legacy-path parsing
# stays colocated with the map above.
_COMMA_LIST = ("CORS_ORIGINS",)


class ConfigBootstrapError(SystemExit):
    """Raised (as a SystemExit) when the operator must edit config.yaml.

    Carries exit code 1 so shells and orchestrators treat it as failure,
    per ADR-001 §6.
    """

    def __init__(self, message: str) -> None:
        super().__init__(1)
        self.message = message


_T = TypeVar("_T")


def _is_changeme(v: Any) -> bool:
    return isinstance(v, str) and bool(_CHANGEME_RE.match(v))


def _reject_changeme(field_name: str, value: _T) -> _T:
    if _is_changeme(value):
        raise ValueError(
            f"{field_name} is still the template placeholder {value!r}; "
            f"edit config.yaml and replace it with a real value."
        )
    if isinstance(value, list):
        for item in value:
            if _is_changeme(item):
                raise ValueError(
                    f"{field_name} still contains template placeholder "
                    f"{item!r}; edit config.yaml and replace it with a real value."
                )
    return value


class StorageSettings(BaseModel):
    """On-disk paths for landed artifacts and compiled bundles."""

    # Empty string → landers fall back to ~/.kgspin/corpus (see
    # landers._shared.get_corpus_root). Operators who want a shared
    # corpus set this to an absolute path.
    corpus_root: str = ""

    # Where compiled extraction bundles live (consumed by the API
    # server and pipeline_common).
    bundles_dir: str = ".bundles"

    model_config = {"extra": "forbid"}

    @field_validator("corpus_root")
    @classmethod
    def _check_corpus_root(cls, v: str) -> str:
        return _reject_changeme("storage.corpus_root", v)

    @field_validator("bundles_dir")
    @classmethod
    def _check_bundles_dir(cls, v: str) -> str:
        return _reject_changeme("storage.bundles_dir", v)


class SecuritySettings(BaseModel):
    """API-surface security posture."""

    # CORS allowlist for the FastAPI server. ``["*"]`` allows all
    # origins (dev default); set to the exact origins in production.
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    model_config = {"extra": "forbid"}

    @field_validator("cors_origins")
    @classmethod
    def _check_cors(cls, v: list[str]) -> list[str]:
        return _reject_changeme("security.cors_origins", v)


class FeatureSettings(BaseModel):
    """Feature flags and pipeline defaults."""

    # Pin the default extraction bundle (e.g. ``financial-v4.1.0-structural``).
    # Empty string → auto-latest from pipeline_common.list_bundles.
    default_bundle: str = ""

    model_config = {"extra": "forbid"}

    @field_validator("default_bundle")
    @classmethod
    def _check_default_bundle(cls, v: str) -> str:
        return _reject_changeme("features.default_bundle", v)


class LLMSettings(BaseModel):
    """LLM alias selection defaults (ADR-002 Phase 4).

    ``default_alias`` is the fallback used by every LLM-invoking call site
    when the request does not pass an explicit ``llm_alias`` or legacy
    ``model`` parameter. Per-flow overrides let an operator point one
    endpoint at a different alias without touching the others (e.g., use a
    cheaper model for auto-flag than for quality analysis).
    """

    # Leave ``None`` to get the in-repo legacy fallback (``GeminiBackend``
    # default model + DeprecationWarning). Set to an admin-registered
    # alias id for ADR-002-compliant resolution.
    default_alias: str | None = None

    compare_qa_llm: str | None = None
    wtm_llm: str | None = None
    impact_llm: str | None = None
    auto_flag_llm: str | None = None
    auto_discover_tp_llm: str | None = None
    quality_analysis_llm: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator(
        "default_alias",
        "compare_qa_llm",
        "wtm_llm",
        "impact_llm",
        "auto_flag_llm",
        "auto_discover_tp_llm",
        "quality_analysis_llm",
    )
    @classmethod
    def _check_alias(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _reject_changeme("llm.<alias_field>", v)


class GraphRagSettings(BaseModel):
    """GraphRAG retrieval knobs (PRD-004 v5 Phase 5B+).

    ``n_hops_default`` controls the BFS depth from chunk-anchored seed
    entities in ``chunk_first`` mode. 1 mirrors the legacy +1-hop
    behavior; 3 (default) catches multi-hop questions where the answer
    entity is several relationships removed from any chunk-anchored
    entity. Set to 0 to disable graph-side traversal entirely (chunks
    only). Per-request overrides via the API's ``n_hops`` parameter.
    """

    n_hops_default: int = 3


class AppSettings(BaseSettings):
    """Top-level demo runtime configuration.

    Populated by :func:`load_settings`; do not construct directly for
    production use — the loader is what implements the ADR-001 precedence.
    """

    storage: StorageSettings = Field(default_factory=StorageSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    graph_rag: GraphRagSettings = Field(default_factory=GraphRagSettings)

    model_config = SettingsConfigDict(extra="forbid")


# --------------------------- Path resolution ---------------------------


def _repo_root() -> Path:
    """Return the demo repo root (contains config.template.yaml).

    ``src/kgspin_demo_app/config.py`` → two ``parents`` up. Used as the
    template-shipping location and the default CWD fallback for
    ``config.yaml`` resolution.
    """
    return Path(__file__).resolve().parents[2]


def resolve_config_path(explicit: Path | None = None) -> Path:
    """Compute where ``config.yaml`` lives for this process.

    Precedence: explicit arg > ``KGSPIN_DEMO_CONFIG`` env > ``./config.yaml``
    (CWD-relative, per ADR-001 §8).
    """
    if explicit is not None:
        return explicit
    env_override = os.environ.get(CONFIG_PATH_ENV)
    if env_override:
        return Path(env_override)
    return Path.cwd() / CONFIG_FILENAME


def resolve_template_path() -> Path:
    return _repo_root() / TEMPLATE_FILENAME


# --------------------------- Bootstrap ---------------------------


def _scan_changeme_fields(data: dict[str, Any], prefix: str = "") -> list[str]:
    """Return the dotted field names that still hold ``<CHANGE_ME*>`` values."""
    hits: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            hits.extend(_scan_changeme_fields(value, prefix=f"{dotted}."))
        elif isinstance(value, list):
            for item in value:
                if _is_changeme(item):
                    hits.append(dotted)
                    break
        elif _is_changeme(value):
            hits.append(dotted)
    return hits


def bootstrap_first_run(config_path: Path, template_path: Path) -> None:
    """Copy template to config_path and raise ConfigBootstrapError.

    Per ADR-001 §6: creates the file, prints actionable guidance naming the
    placeholder fields the operator must fill, exits non-zero.
    """
    template_path = template_path.resolve()
    if not template_path.exists():
        # Shipping defect — the template is committed, so this is never
        # expected in a real install. Surface it loudly rather than
        # pretending to bootstrap from thin air.
        raise ConfigBootstrapError(
            f"config.template.yaml missing at {template_path}; "
            f"this is an install-integrity problem, not an operator error."
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(template_path, config_path)

    with open(config_path) as f:
        seeded = yaml.safe_load(f) or {}
    placeholders = _scan_changeme_fields(seeded)

    lines = [
        f"Created {config_path} from {template_path.name}.",
        "",
        "Edit the following fields before re-running:",
    ]
    if placeholders:
        for field in placeholders:
            lines.append(f"  - {field}: replace <CHANGE_ME*> placeholder with a real value")
    else:
        lines.append("  - (none — template has no required placeholders; re-run to proceed)")
    lines += [
        "",
        f"Override the config path by setting {CONFIG_PATH_ENV}=<path> if you",
        "want to keep config.yaml elsewhere.",
    ]
    raise ConfigBootstrapError("\n".join(lines))


# --------------------------- Loader ---------------------------


def _set_nested(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Set ``target[path[0]][path[1]]...[path[-1]] = value``, creating dicts along the way."""
    cursor = target
    for segment in path[:-1]:
        existing = cursor.get(segment)
        if not isinstance(existing, dict):
            existing = {}
            cursor[segment] = existing
        cursor = existing
    cursor[path[-1]] = value


def _parse_legacy_value(env_name: str, raw: str) -> Any:
    if env_name in _COMMA_LIST:
        return [p for p in raw.split(",") if p]
    # KGSPIN_CORPUS_ROOT, KGEN_BUNDLES_DIR, KGEN_DEFAULT_BUNDLE → plain string.
    return raw


_warned_envs: set[str] = set()


def _warn_legacy_env_once(env_name: str, dotted: str) -> None:
    if env_name in _warned_envs:
        return
    _warned_envs.add(env_name)
    message = (
        f"{env_name} is deprecated: set '{dotted}' in config.yaml instead. "
        f"The env var still wins for this release; support will be removed "
        f"after the alpha compatibility window (ADR-001 Risk-3)."
    )
    # Emit both a DeprecationWarning (picked up by tests via pytest.warns and
    # by CI running with PYTHONWARNINGS=default) and a stderr line, because
    # Python's default warning filter silences DeprecationWarning outside of
    # __main__ — meaning ordinary operators running the landers wouldn't see
    # anything without this extra line.
    warnings.warn(message, DeprecationWarning, stacklevel=3)
    print(f"DEPRECATION: {message}", file=sys.stderr)


# Env vars that *we* wrote via apply_settings_to_env — not operator-set,
# so skip the deprecation warning and skip overriding config.yaml with them.
_bridge_applied: set[str] = set()


def _apply_legacy_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Mutate ``data`` in place with any legacy-env-var values; warn once each."""
    for env_name, dotted_path in _LEGACY_ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        if env_name in _bridge_applied:
            # We set this ourselves on a previous bootstrap; not an operator
            # override, so don't warn and don't re-layer it over config.yaml.
            continue
        value = _parse_legacy_value(env_name, raw)
        _warn_legacy_env_once(env_name, ".".join(dotted_path))
        _set_nested(data, dotted_path, value)
    return data


def load_settings(
    *,
    config_path: Path | None = None,
    template_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppSettings:
    """Load ``AppSettings`` per ADR-001 precedence.

    Args:
        config_path: override the resolved ``config.yaml`` path (tests).
        template_path: override the template path (tests).
        cli_overrides: nested dict of CLI-origin overrides, applied last
            so they win over every other source.
    """
    resolved_config = resolve_config_path(config_path)
    resolved_template = (template_path or resolve_template_path()).resolve()

    if not resolved_config.exists():
        bootstrap_first_run(resolved_config, resolved_template)

    with open(resolved_config) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{resolved_config}: top-level YAML must be a mapping, got "
            f"{type(data).__name__}"
        )

    _apply_legacy_env_overrides(data)

    if cli_overrides:
        for path_str, value in cli_overrides.items():
            _set_nested(data, tuple(path_str.split(".")), value)

    return AppSettings.model_validate(data)


# --------------------------- Env-var bridge ---------------------------

# These mirror the legacy env var names the rest of the codebase still
# reads (landers/_shared.py, api/server.py, demos/extraction/pipeline_common.py).
# Centralising the list here means entry points are the only place that
# push settings into the existing readers — refactoring those readers to
# take a AppSettings object is a post-alpha cleanup sprint.
_ENV_BRIDGE = {
    ("storage", "corpus_root"): "KGSPIN_CORPUS_ROOT",
    ("storage", "bundles_dir"): "KGEN_BUNDLES_DIR",
    ("features", "default_bundle"): "KGEN_DEFAULT_BUNDLE",
    ("security", "cors_origins"): "CORS_ORIGINS",
}


def apply_settings_to_env(settings: AppSettings) -> None:
    """Push settings values into legacy env vars the rest of the app reads.

    Only writes a var if it is NOT already set in the environment — the
    legacy env var, when already present, won out in :func:`load_settings`
    and was already merged into ``settings``; overwriting here would be
    redundant and would squash operator-set externals that were
    intentionally left out of config.yaml.

    Empty-string values are skipped so the legacy readers keep their own
    sensible defaults (e.g. ``landers._shared.get_corpus_root`` falls back
    to ``~/.kgspin/corpus`` when ``KGSPIN_CORPUS_ROOT`` is unset).
    """
    section_map = {
        "storage": settings.storage,
        "security": settings.security,
        "features": settings.features,
    }
    for (section, field), env_name in _ENV_BRIDGE.items():
        if env_name in os.environ:
            continue
        value = getattr(section_map[section], field)
        serialized = _serialize_env_value(env_name, value)
        if serialized == "":
            # Preserve the "unset" signal so downstream defaults kick in.
            continue
        os.environ[env_name] = serialized
        _bridge_applied.add(env_name)


def _serialize_env_value(env_name: str, value: Any) -> str:
    if env_name in _COMMA_LIST:
        return ",".join(value) if value else ""
    return str(value)


def bootstrap_cli() -> AppSettings:
    """Load settings + bridge to env vars; suitable for CLI ``main()`` use.

    Catches :class:`ConfigBootstrapError` and :class:`pydantic.ValidationError`,
    prints actionable stderr output, and exits non-zero. Returns the loaded
    settings on success so callers can inspect typed fields directly.
    """
    from pydantic import ValidationError  # local import; keeps import-time cheap

    try:
        settings = load_settings()
    except ConfigBootstrapError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from None
    except ValidationError as exc:
        print(
            "config.yaml failed validation. Fix the following field(s) and re-run:",
            file=sys.stderr,
        )
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            print(f"  - {loc}: {err.get('msg')}", file=sys.stderr)
        raise SystemExit(1) from None

    apply_settings_to_env(settings)
    return settings


__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_PATH_ENV",
    "ConfigBootstrapError",
    "AppSettings",
    "FeatureSettings",
    "LLMSettings",
    "SecuritySettings",
    "StorageSettings",
    "TEMPLATE_FILENAME",
    "apply_settings_to_env",
    "bootstrap_cli",
    "bootstrap_first_run",
    "load_settings",
    "resolve_config_path",
    "resolve_template_path",
]
