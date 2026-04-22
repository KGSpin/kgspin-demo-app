# PRD-053: Resolution Lineage (Auditability for Symbolic Slugs)

**Status:** Draft
**Priority:** High
**Milestone:** Phase 3 (Persistence & Scale)
**Dependencies:** PRD-052 (The Assignment Challenge)
**Last Updated:** 2026-03-13

---

## 1. Goal
Provide 100% audibility for the resolution of role-based descriptors (Slugs) to proper nouns. Every resolved entity that originated as a descriptor must have a clear lineage path showing the "Definition Moment" that justified the mapping.

## 2. Background
In PRD-052, we introduced "Symbolic Slugs" to increase recruitment by resolving terms like "The CEO" to "John Doe". However, without lineage data, the final Knowledge Graph appears to have hallucinated relationships when the source text only contains descriptors. To maintain trust, we must persist the "Map" used during extraction.

## 3. Data Model: The Slug Lineage Record
The `ExtractionResult` must include a `resolution_lineage` dictionary mapping Slug IDs to their resolution evidence.

### Record Structure
```json
{
  "slug_id": "slug-p-ex-001",
  "descriptor_text": "worldwide vice president",
  "resolved_name": "John Doe",
  "resolution_type": "assignment_verb | adjacent_context | global_propagation",
  "evidence": {
    "sentence": "John Doe serves as worldwide vice president.",
    "position": {
      "start": 1250,
      "end": 1285,
      "sentence_index": 42
    },
    "assignment_verb": "serves as",
    "confidence": 0.95
  }
}
```

## 4. Functional Requirements

### 4.1 Persistence of the Slug Map
The extraction engine must save the active "Slug Map" for every document at the end of the extraction lifecycle. This map must include even those slugs that were *not* used in a relationship if they were part of a valid assignment.

### 4.2 Positional Tracking
Lineage records must include character-level offsets (`start`, `end`) for both the descriptor occurrence and the "Definition Moment" (the assignment sentence). 

### 4.3 Attribution (The "Why")
Every resolution must cite its cause:
- **Assignment Verb**: Mapping was triggered by a specific verb defined in the bundle (e.g., "is", "named").
- **Adjacent Context**: Mapping was inferred from sentence proximity (Pass 1).
- **Global Table**: Mapping was inherited from a previous document-level resolution (Pass 2).

### 4.4 Traceability in the Graph
Final triples containing resolved entities should include a `source_is_slug: true` flag and a pointer to the `slug_id` in the lineage records.

## 5. Success Metrics
- **Audit Accuracy**: 100% of resolved relationships can be traced back to a specific sentence in the source text.
- **Verification Speed**: Human-in-the-loop reviewers can verify a "Slug Match" in <5 seconds using the provided evidence sentence and offsets.

---
— Prod
