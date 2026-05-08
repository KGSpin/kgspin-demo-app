"""Static-asset test: LLM_FAILURE_COPY exposes a 'missing_domain_model' key.

graph.js maps SSE error ``reason`` keys to the slot-failure overlay's
title + help text. The fan_out_trained sprint added the
``missing_domain_model`` reason; this test pins that the JS asset
still has the matching copy entry so the typed error from
kgspin-core's MissingDomainModelError doesn't fall through to the
generic ``extraction_failed`` placeholder.
"""

from __future__ import annotations

import re
from pathlib import Path


_GRAPH_JS = (
    Path(__file__).resolve().parents[2]
    / "demos" / "extraction" / "static" / "js" / "graph.js"
)


def test_missing_domain_model_copy_present() -> None:
    text = _GRAPH_JS.read_text(encoding="utf-8")
    assert "'missing_domain_model'" in text or '"missing_domain_model"' in text, (
        "graph.js LLM_FAILURE_COPY missing 'missing_domain_model' key"
    )

    # Find the entry block and assert it has both title + help, both non-empty.
    pattern = re.compile(
        r"['\"]missing_domain_model['\"]\s*:\s*\{(?P<body>[^}]+)\}",
        re.DOTALL,
    )
    m = pattern.search(text)
    assert m, "missing_domain_model entry not parseable"
    body = m.group("body")
    title_match = re.search(r"title\s*:\s*['\"]([^'\"]+)['\"]", body)
    help_match = re.search(r"help\s*:\s*['\"]([^'\"]+)['\"]", body)
    assert title_match and title_match.group(1).strip(), (
        "missing_domain_model.title is empty"
    )
    assert help_match and help_match.group(1).strip(), (
        "missing_domain_model.help is empty"
    )
