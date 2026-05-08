"""Cross-language pin: the JS computeTrainedDiff matches the Python mirror.

The /compare UI runs ``demos/extraction/static/js/diff.js`` in the
browser; the smoke test runs the Python mirror in
``kgspin_demo_app.compare_diff``. They must stay behavior-identical or
the UI's diff panel and the CI smoke can drift in opposite directions.

This test shells out to ``node`` to run the JS function on the same
fixtures the Python tests use and asserts the per-type counts + set
diff match. Skipped if Node isn't on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kgspin_demo_app.compare_diff import compute_trained_diff


_DIFF_JS = (
    Path(__file__).resolve().parents[2]
    / "demos" / "extraction" / "static" / "js" / "diff.js"
)


_FIXTURES = [
    {
        "name": "basic_set_diff",
        "a": [
            {"surface": "Apple Inc.", "type": "COMPANY"},
            {"surface": "iPhone", "type": "PRODUCT"},
            {"surface": "Tim Cook", "type": "PERSON"},
        ],
        "b": [
            {"surface": "Apple Inc.", "type": "COMPANY"},
            {"surface": "iPhone 15", "type": "PRODUCT"},
            {"surface": "Tim Cook", "type": "PERSON"},
        ],
    },
    {
        "name": "same_surface_different_type",
        "a": [{"surface": "Apple", "type": "COMPANY"}],
        "b": [{"surface": "Apple", "type": "PRODUCT"}],
    },
    {
        "name": "normalization",
        "a": [{"surface": "Apple Inc.", "type": "COMPANY"}],
        "b": [{"surface": "apple   inc.", "type": "COMPANY"}],
    },
]


def _run_js(fixture: dict) -> dict:
    script = (
        "const { computeTrainedDiff } = require(process.argv[1]);\n"
        "const { a, b } = JSON.parse(process.argv[2]);\n"
        "const diff = computeTrainedDiff({kg:{entities:a}}, {kg:{entities:b}});\n"
        "process.stdout.write(JSON.stringify(diff));\n"
    )
    payload = json.dumps({"a": fixture["a"], "b": fixture["b"]})
    result = subprocess.run(
        ["node", "-e", script, str(_DIFF_JS), payload],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def _normalize_diff(diff: dict) -> dict:
    """Make order-insensitive comparison: sort the entity lists."""
    def _sort(items):
        return sorted(items, key=lambda d: (d["type"], d["surface"]))
    return {
        "by_type": diff["by_type"],
        "only_in_a": _sort(diff["only_in_a"]),
        "only_in_b": _sort(diff["only_in_b"]),
        "agreed": _sort(diff["agreed"]),
        "total_a": diff["total_a"],
        "total_b": diff["total_b"],
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node not installed")
@pytest.mark.parametrize(
    "fixture", _FIXTURES, ids=lambda f: f["name"],
)
def test_js_python_parity(fixture: dict) -> None:
    js = _normalize_diff(_run_js(fixture))
    py = _normalize_diff(compute_trained_diff(
        {"kg": {"entities": fixture["a"]}},
        {"kg": {"entities": fixture["b"]}},
    ))
    assert js == py, f"JS vs Python diff mismatch for {fixture['name']}"
