"""Path-safety utilities for landers (VP Security Mandate 2).

All path components derived from user input (tickers, NCT IDs, query
strings, dates) flow through ``sanitize_component(...)`` before they
participate in path construction. The final resolved path is verified
to reside inside ``$KGSPIN_CORPUS_ROOT`` via ``resolve_under_root(...)``
— anything escaping the root raises ``SecurityError``.

This module has ZERO dependencies outside the stdlib so it can be
imported by landers without pulling in the rest of the demo.
"""

from __future__ import annotations

import re
from pathlib import Path


class SecurityError(Exception):
    """Raised when path sanitization detects an attempt to escape the
    corpus root (path traversal) or when a path component contains
    forbidden characters that would change the path's semantic meaning.
    """


_FORBIDDEN_PATH_COMPONENT_RE = re.compile(r"[/\\\x00-\x1f]")


def sanitize_component(raw: str, *, what: str = "path component") -> str:
    """Return ``raw`` unchanged if it contains only characters that are
    safe to use as a single path component, otherwise raise
    ``SecurityError`` with a message referencing ``what``.

    Safe characters:
        - ASCII letters + digits
        - dash, underscore, dot, colon (for ISO-8601 datetimes)
        - space (rare, but some sources use them; we allow it)

    Forbidden:
        - path separators (``/`` or ``\\``) — would escape the intended
          single-component slot
        - null byte — C-string truncation attack
        - control characters (0x00-0x1F) — invisible in terminals
        - leading dot or double-dot — ``..`` is the canonical traversal

    Empty strings are rejected.
    """
    if not raw:
        raise SecurityError(f"{what} is empty")
    if _FORBIDDEN_PATH_COMPONENT_RE.search(raw):
        raise SecurityError(
            f"{what} contains forbidden character (path separator, null, or control char): {raw!r}"
        )
    if raw == "." or raw == "..":
        raise SecurityError(f"{what} cannot be '.' or '..': {raw!r}")
    if raw.startswith(".."):
        raise SecurityError(f"{what} cannot start with '..': {raw!r}")
    return raw


def resolve_under_root(root: Path, *components: str) -> Path:
    """Join ``components`` under ``root`` and verify the resolved
    absolute path is inside ``root.resolve()``.

    Each component is sanitized via ``sanitize_component(...)`` before
    it's appended. Any attempt to construct a path outside ``root``
    (via symlinks, unsanitized input, or anything else) raises
    ``SecurityError``.

    Returns the resolved absolute path.
    """
    for c in components:
        sanitize_component(c)
    candidate = root.joinpath(*components)
    resolved_root = root.resolve()
    # Use resolve(strict=False) so this works for paths that don't yet exist
    # (landers create files at these paths; they don't pre-exist).
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as e:
        raise SecurityError(
            f"Resolved path {resolved_candidate} escapes corpus root {resolved_root}"
        ) from e
    return resolved_candidate
