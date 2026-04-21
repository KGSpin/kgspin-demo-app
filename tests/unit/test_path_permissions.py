"""Sprint 09 Phase 3 — VP Eng MAJOR: pin directory-permission mode.

VP Eng test-eval feedback: the 0o700 parent-directory mode enforced by
``_shared.default_artifact_path`` + ``_shared.get_corpus_root`` should
not be "verified by smoke" — it needs a deterministic test.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def test_default_artifact_path_creates_parent_dirs_at_0o700(
    tmp_path: Path,
) -> None:
    """Every ancestor directory between ``corpus_root`` and the file
    leaf is chmod'd to ``0o700`` so even an operator umask of ``0o000``
    produces world-unreadable corpus directories."""
    from kgspin_demo_app.landers._shared import default_artifact_path

    corpus_root = tmp_path / "corpus"
    # Ensure root starts without the mode bit we care about
    corpus_root.mkdir(exist_ok=True)

    raw_path = default_artifact_path(
        corpus_root,
        domain="financial",
        source="sec_edgar",
        identifier="TST",
        date="2026-04-17",
        artifact_type="10-K",
        filename="raw.html",
    )

    # Walk every ancestor from the file's immediate parent up to corpus_root,
    # asserting mode 0o700.
    for ancestor in list(raw_path.parents):
        if ancestor == corpus_root or corpus_root in ancestor.parents:
            mode = stat.S_IMODE(os.stat(ancestor).st_mode)
            assert mode == 0o700, \
                f"{ancestor}: expected mode 0o700, got {oct(mode)}"
        if ancestor == corpus_root:
            break


def test_get_corpus_root_tightens_to_0o700(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_corpus_root`` chmods the resolved root to ``0o700`` even
    when it already exists with a looser mode."""
    from kgspin_demo_app.landers._shared import get_corpus_root

    target = tmp_path / "loose-root"
    target.mkdir(mode=0o755)
    monkeypatch.setenv("KGSPIN_CORPUS_ROOT", str(target))

    resolved = get_corpus_root()
    mode = stat.S_IMODE(os.stat(resolved).st_mode)
    assert mode == 0o700, \
        f"corpus root mode expected 0o700, got {oct(mode)}"


def test_default_artifact_path_rejects_traversal(tmp_path: Path) -> None:
    """Sanity — confirmation that path_safety.sanitize_component blocks
    path-traversal attempts in any component."""
    from kgspin_demo_app.landers._path_safety import SecurityError
    from kgspin_demo_app.landers._shared import default_artifact_path

    with pytest.raises(SecurityError):
        default_artifact_path(
            tmp_path / "corpus",
            domain="financial",
            source="sec_edgar",
            identifier="../../../etc/passwd",  # traversal attempt
            date="2026-04-17",
            artifact_type="10-K",
            filename="raw.html",
        )
