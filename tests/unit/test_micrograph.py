"""Tests for the LLM-answer micro-graph builder (PRD-055 #3)."""
from kgspin_demo_app.services.micrograph import build_micrograph_from_answer


def test_empty_answer_is_empty_graph():
    g = build_micrograph_from_answer("")
    assert g == {"entities": [], "relationships": []}


def test_whitespace_only_answer_is_empty_graph():
    g = build_micrograph_from_answer("   \n  \t  ")
    assert g == {"entities": [], "relationships": []}


def test_single_named_entity_yields_one_node():
    g = build_micrograph_from_answer("Johnson & Johnson is a healthcare company.")
    assert any(e["text"].lower().startswith("johnson") for e in g["entities"])


def test_two_entities_with_verb_yield_edge():
    g = build_micrograph_from_answer("Johnson & Johnson acquired Abiomed in 2022.")
    texts = {e["text"].lower() for e in g["entities"]}
    assert any("johnson" in t for t in texts)
    assert any("abiomed" in t for t in texts)
    assert any(r["predicate"] in {"acquire", "acquired"} for r in g["relationships"])


def test_deterministic_same_input_same_graph():
    text = "Pfizer launched a Phase III trial for Paxlovid in 2024 with the FDA."
    g1 = build_micrograph_from_answer(text)
    g2 = build_micrograph_from_answer(text)
    assert g1 == g2


def test_long_paragraph_caps_at_max_nodes():
    # Generate many distinct named entities
    sentences = [
        f"Company{i} acquired Company{i+1} in 202{i % 10}." for i in range(200)
    ]
    g = build_micrograph_from_answer(" ".join(sentences))
    # Cap is 50 nodes
    assert len(g["entities"]) <= 50


def test_relationship_endpoints_reference_known_entities():
    g = build_micrograph_from_answer("Johnson & Johnson reported revenue from Stelara in 2023.")
    entity_texts = {e["text"] for e in g["entities"]}
    for rel in g["relationships"]:
        assert rel["subject"]["text"] in entity_texts
        assert rel["object"]["text"] in entity_texts


def test_kg_dict_shape_matches_demo_helpers():
    """The micro-graph must be consumable by `_build_kg_context_string`
    and `health_for_kg`, both of which expect entities[].text/entity_type
    and relationships[].subject.text/object.text/predicate.
    """
    g = build_micrograph_from_answer("Apple announced the iPhone in 2007.")
    for e in g["entities"]:
        assert "text" in e
        assert "entity_type" in e
    for r in g["relationships"]:
        assert "subject" in r and "text" in r["subject"]
        assert "object" in r and "text" in r["object"]
        assert "predicate" in r
