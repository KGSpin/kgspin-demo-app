"""Domain → backend-lander mapping for the demo.

Wave A rewrite (CTO Q3 ruling): ``fetchers/registrations.yaml`` in the
sibling ``kgspin-demo-config`` repo is authoritative. This module loads
it lazily via ``KGSPIN_DEMO_CONFIG_PATH`` (set by ``start-demo.sh``) and
exposes the same public API as the pre-Wave-A Python-dict version so
existing call sites keep working.

Keys are backend-named fetcher IDs (matching each lander's ``name``
attribute + admin's FETCHER record id). A fetcher ID may appear under
multiple domains — ADR-004.
"""

from __future__ import annotations

import os as _os
from pathlib import Path as _Path
from typing import Mapping as _Mapping

import yaml as _yaml


_CACHED: dict[str, list[str]] | None = None
_DEFAULT_CONFIG_ROOT = _Path(__file__).resolve().parent.parent.parent.parent / "kgspin-demo-config"


def _resolve_registrations_path() -> _Path:
    """Locate ``fetchers/registrations.yaml`` via ``KGSPIN_DEMO_CONFIG_PATH``.

    ``start-demo.sh`` exports this env var to the sibling demo-config
    repo root; test contexts that omit it fall back to the sibling-
    directory default.
    """
    root = _os.environ.get("KGSPIN_DEMO_CONFIG_PATH", "").strip()
    base = _Path(root) if root else _DEFAULT_CONFIG_ROOT
    return base / "fetchers" / "registrations.yaml"


def _load_registrations() -> dict[str, list[str]]:
    """Load and validate ``registrations.yaml``.

    Expected shape::

        domains:
          <domain>:
            fetchers:
              - <fetcher_id>
              - ...
    """
    path = _resolve_registrations_path()
    try:
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise RuntimeError(
            f"fetcher registrations YAML not found at {path}. Set "
            f"KGSPIN_DEMO_CONFIG_PATH to the kgspin-demo-config repo root, "
            f"or run via scripts/start-demo.sh which sets it for you."
        ) from e
    if not isinstance(raw, _Mapping) or "domains" not in raw:
        raise RuntimeError(
            f"{path}: expected top-level 'domains' mapping; got {type(raw).__name__}."
        )
    domains = raw["domains"]
    if not isinstance(domains, _Mapping):
        raise RuntimeError(f"{path}: 'domains' must be a mapping.")
    out: dict[str, list[str]] = {}
    for domain, entry in domains.items():
        if not isinstance(entry, _Mapping) or "fetchers" not in entry:
            raise RuntimeError(
                f"{path}: 'domains.{domain}' must have a 'fetchers' list."
            )
        fetchers = entry["fetchers"]
        if not isinstance(fetchers, list) or not all(isinstance(f, str) for f in fetchers):
            raise RuntimeError(
                f"{path}: 'domains.{domain}.fetchers' must be a list of strings."
            )
        out[str(domain)] = list(fetchers)
    return out


class _LazyFetchersMapping(_Mapping[str, list[str]]):
    """Dict-compatible facade that loads registrations.yaml on first read.

    Preserves the old ``DOMAIN_FETCHERS`` import pattern while deferring
    I/O until actually used — important for test contexts that stub
    configs before first access.
    """

    def _data(self) -> dict[str, list[str]]:
        global _CACHED
        if _CACHED is None:
            _CACHED = _load_registrations()
        return _CACHED

    def __getitem__(self, key: str) -> list[str]:
        return list(self._data()[key])

    def __iter__(self):
        return iter(self._data())

    def __len__(self) -> int:
        return len(self._data())

    def __contains__(self, key: object) -> bool:
        return key in self._data()

    def get(self, key, default=None):
        data = self._data()
        if key in data:
            return list(data[key])
        return default

    def keys(self):
        return self._data().keys()

    def values(self):
        return [list(v) for v in self._data().values()]

    def items(self):
        return [(k, list(v)) for k, v in self._data().items()]

    def __repr__(self) -> str:
        return f"_LazyFetchersMapping({dict(self._data())!r})"


DOMAIN_FETCHERS: Mapping[str, list[str]] = _LazyFetchersMapping()


def fetchers_for(domain: str) -> list[str]:
    """Return the fetcher IDs that serve ``domain``.

    Returns an empty list for unknown domains — callers decide whether
    that's an error in their context.
    """
    return list(DOMAIN_FETCHERS.get(domain, ()))


def domains_served_by(fetcher_id: str) -> list[str]:
    """Return the domains that reference ``fetcher_id``.

    Order is deterministic (insertion order of ``registrations.yaml``).
    Returns an empty list for unknown fetcher IDs.
    """
    return [d for d, ids in DOMAIN_FETCHERS.items() if fetcher_id in ids]


def reset_cache_for_tests() -> None:
    """Reset the lazy cache. Tests only."""
    global _CACHED
    _CACHED = None
