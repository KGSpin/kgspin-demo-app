"""Trained-vs-heuristic diff helper tests.

These pin the canonical comparison semantics used by both the
client-side ``demos/extraction/static/js/diff.js`` and the server-side
:mod:`kgspin_demo_app.compare_diff`. The two implementations must stay
behavior-identical; this suite exercises the four cases the sprint plan
calls out:

1. Basic happy-path set diff.
2. Per-type count delta arithmetic.
3. Same surface in different entity types is NOT counted as agreed.
4. Casefold + whitespace normalization (``Apple Inc.`` ≡ ``apple   inc.``).
"""

from __future__ import annotations

from kgspin_demo_app.compare_diff import compute_trained_diff


def _slot(entities: list[dict]) -> dict:
    return {"kg": {"entities": entities}}


def test_basic_set_diff() -> None:
    a = _slot([
        {"surface": "Apple Inc.", "type": "COMPANY"},
        {"surface": "iPhone", "type": "PRODUCT"},
        {"surface": "Tim Cook", "type": "PERSON"},
    ])
    b = _slot([
        {"surface": "Apple Inc.", "type": "COMPANY"},
        {"surface": "iPhone 15", "type": "PRODUCT"},
        {"surface": "Tim Cook", "type": "PERSON"},
    ])

    diff = compute_trained_diff(a, b)

    agreed_norms = {(d["type"], d["surface"]) for d in diff["agreed"]}
    only_a_norms = {(d["type"], d["surface"]) for d in diff["only_in_a"]}
    only_b_norms = {(d["type"], d["surface"]) for d in diff["only_in_b"]}

    assert ("COMPANY", "Apple Inc.") in agreed_norms
    assert ("PERSON", "Tim Cook") in agreed_norms
    assert ("PRODUCT", "iPhone") in only_a_norms
    assert ("PRODUCT", "iPhone 15") in only_b_norms
    assert diff["total_a"] == 3
    assert diff["total_b"] == 3


def test_per_type_count_delta() -> None:
    a = _slot([
        {"surface": "Apple", "type": "COMPANY"},
        {"surface": "Microsoft", "type": "COMPANY"},
        {"surface": "iPhone", "type": "PRODUCT"},
    ])
    b = _slot([
        {"surface": "Apple", "type": "COMPANY"},
        {"surface": "Microsoft", "type": "COMPANY"},
        {"surface": "Alphabet", "type": "COMPANY"},
        {"surface": "Mac", "type": "PRODUCT"},
    ])

    diff = compute_trained_diff(a, b)

    assert diff["by_type"]["COMPANY"] == {
        "a_count": 2, "b_count": 3, "delta": 1,
    }
    assert diff["by_type"]["PRODUCT"] == {
        "a_count": 1, "b_count": 1, "delta": 0,
    }


def test_same_surface_different_type_not_agreed() -> None:
    """``Apple`` as COMPANY and ``Apple`` as PRODUCT must NOT be folded
    into the same agreed bucket — type is part of the diff key."""
    a = _slot([{"surface": "Apple", "type": "COMPANY"}])
    b = _slot([{"surface": "Apple", "type": "PRODUCT"}])

    diff = compute_trained_diff(a, b)

    assert diff["agreed"] == []
    assert {(d["type"], d["surface"]) for d in diff["only_in_a"]} == {
        ("COMPANY", "Apple"),
    }
    assert {(d["type"], d["surface"]) for d in diff["only_in_b"]} == {
        ("PRODUCT", "Apple"),
    }


def test_normalization_casefold_whitespace() -> None:
    """``Apple Inc.`` and ``apple   inc.`` collapse to the same key."""
    a = _slot([{"surface": "Apple Inc.", "type": "COMPANY"}])
    b = _slot([{"surface": "apple   inc.", "type": "COMPANY"}])

    diff = compute_trained_diff(a, b)

    assert len(diff["agreed"]) == 1
    assert diff["agreed"][0]["type"] == "COMPANY"
    assert diff["only_in_a"] == []
    assert diff["only_in_b"] == []


def test_alternate_surface_field_names() -> None:
    """The JS source accepts ``surface``/``name``/``text``/``surface_form`` —
    the Python mirror does too."""
    a = _slot([
        {"name": "Apple", "type": "COMPANY"},
        {"text": "iPhone", "type": "PRODUCT"},
    ])
    b = _slot([
        {"surface_form": "Apple", "type": "COMPANY"},
        {"surface": "iPhone", "type": "PRODUCT"},
    ])

    diff = compute_trained_diff(a, b)

    assert len(diff["agreed"]) == 2
    assert diff["only_in_a"] == []
    assert diff["only_in_b"] == []
