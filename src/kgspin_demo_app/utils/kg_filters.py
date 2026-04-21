"""Knowledge graph filtering for display and analysis.

Sprint 90: Filter utility that ensures analysis, heatmaps, and Gemini prompts
operate on the same clean data the user sees in the graph visualization.

Per VP Eng mandate (ADR-001), filter logic lives in core src/ — not the demo
script — so CLI, API, and demo share the same "clean KG" definition.

---

INIT-001 Sprint 02 note: this module was recovered verbatim from the
legacy monolith archive (utils/kg_filters.py, Sprint 90 source). The
only change from the archive is rewriting the single internal import
to resolve `is_garbage_entity` via `kgspin_core.execution.entity_filters`,
where that helper now lives post-refactor. Sprint 01 shipped passthrough
stubs per ADR-001; Sprint 02 replaces those stubs with the real
implementation — see ADR-001 "Conditions for Revisiting" and the
Sprint 02 dev report for context.
"""

from kgspin_core.execution.entity_filters import is_garbage_entity


def filter_kg_for_display(
    kg: dict,
    confidence_floor: float = 0.55,
) -> dict:
    """Filter a KG dict for display/analysis, removing noise the user won't see.

    Removes:
    - Entities below confidence_floor
    - Garbage entities (stopwords, month names, boilerplate — via is_garbage_entity)
    - DOCUMENT entities (structural nodes, not domain knowledge)
    - MENTIONED_IN relationships (structural, not semantic)
    - Relationships referencing any removed entity

    Preserves on retained entities (ADR-005):
    - source_sentence, source_document, similarity, evidence metadata
    - All provenance fields are untouched on entities that survive filtering

    Args:
        kg: Knowledge graph dict with "entities" and "relationships" lists.
        confidence_floor: Minimum entity confidence to include (default: 0.55).

    Returns:
        New dict with filtered "entities" and "relationships" lists.
        Original kg dict is not modified.
    """
    # --- Entity filtering ---
    surviving_entities = []
    # Track surviving entity identities for relationship pruning
    surviving_entity_keys = set()

    for ent in kg.get("entities", []):
        text = ent.get("text", "").strip()
        etype = ent.get("entity_type", "")
        confidence = ent.get("confidence", 0)

        # Skip below confidence floor
        if confidence < confidence_floor:
            continue

        # Skip garbage entities
        if is_garbage_entity(text):
            continue

        # Skip DOCUMENT entities (structural nodes)
        if etype == "DOCUMENT":
            continue

        surviving_entities.append(ent)
        # Use (type, lowercase text) as identity key for relationship pruning
        surviving_entity_keys.add((etype, text.lower()))

    # --- Relationship filtering ---
    surviving_relationships = []

    for rel in kg.get("relationships", []):
        predicate = rel.get("predicate", "")

        # Skip MENTIONED_IN relationships (structural, not semantic)
        if predicate == "MENTIONED_IN":
            continue

        # Skip relationships where subject or object was filtered out
        subj = rel.get("subject", {})
        obj = rel.get("object", {})
        subj_key = (subj.get("entity_type", ""), subj.get("text", "").strip().lower())
        obj_key = (obj.get("entity_type", ""), obj.get("text", "").strip().lower())

        if subj_key not in surviving_entity_keys or obj_key not in surviving_entity_keys:
            continue

        surviving_relationships.append(rel)

    return {
        "entities": surviving_entities,
        "relationships": surviving_relationships,
    }


def compute_schema_compliance(kg: dict, valid_entity_types: set) -> dict:
    """Compute programmatic schema compliance for a KG.

    Sprint 90: Deterministic metric — not LLM-guessed. Counts how many
    entities have types in the valid schema vs. total entities.

    Args:
        kg: Filtered KG dict (should be post-filter_kg_for_display).
        valid_entity_types: Set of valid entity type names from the schema.

    Returns:
        Dict with on_schema, off_schema, total, compliance_pct, off_schema_types.
    """
    entities = kg.get("entities", [])
    if not entities:
        return {
            "on_schema": 0,
            "off_schema": 0,
            "total": 0,
            "compliance_pct": 100.0,
            "off_schema_types": [],
        }

    on_schema = 0
    off_schema = 0
    off_types = set()

    for ent in entities:
        etype = ent.get("entity_type", "UNKNOWN")
        if etype in valid_entity_types:
            on_schema += 1
        else:
            off_schema += 1
            off_types.add(etype)

    total = on_schema + off_schema
    pct = round((on_schema / total) * 100, 1) if total > 0 else 100.0

    return {
        "on_schema": on_schema,
        "off_schema": off_schema,
        "total": total,
        "compliance_pct": pct,
        "off_schema_types": sorted(off_types),
    }
