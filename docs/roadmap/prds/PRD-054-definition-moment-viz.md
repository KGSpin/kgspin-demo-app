# PRD-054: "Definition Moment" Visualization (The Provenance Explorer)

**Status:** Draft
**Milestone:** Phase 2 (Visual Intelligence & Context)
**RICE Score:** 12.8 (Reach: 4, Impact: 4, Confidence: 0.8, Effort: 1)
**Effort:** S (1 week)

**Dependencies:** PRD-052 (Dynamic Entity Assignment)
**Last Updated:** 2026-03-12

---

## Goal
Surface the "Definition Moments" (the assignments that link titles to names) in the KG Explorer UI so that users can trust and audit relationships extracted from role-based descriptors.

## Background
When a user sees a relationship like `(The CEO) --leads--> (ABC Corp)` resolved in the graph as `(Jane Doe) --leads--> (ABC Corp)`, they need to know *why* the system thinks Jane Doe is the CEO. Without visual evidence of the assignment, the system looks like a "black box."

## Functional Requirements

### 1. Dual-Evidence Highlighting
- When a relationship extracted via a Slug is clicked:
    - **Primary Evidence**: Highlight the sentence expressing the relationship (e.g., "The CEO lead the board meeting").
    - **Assignment Evidence**: Highlight the "Definition Moment" sentence (e.g., "Jane Doe was appointed CEO last March").
- Both highlights must be distinct (e.g., blue for relationship, green for assignment).

### 2. Assignment Overlays
- In the Entity Panel, show an "Aliases & Roles" section that lists all Slugs resolved to that entity within the current document scope.
- Each Slug entry should have a "Source" button that scrolls the document viewer to the exact Definition Moment.

### 3. Collision Warnings
- If a relationship was resolved despite a collision risk (low confidence), display a warning icon on the edge in the graph.
- Clicking the warning should show the conflicting definitions (e.g., "Potential Collision: Both Jane Doe and John Smith are referred to as 'The Principal' in this section").

## Success Metrics
- **UX Audit Time**: Reduce the time it takes a human to verify a resolved relationship by 40%.
- **Trust Score**: Increase qualitative user trust in role-based extraction results during design partner demos.

— Prod
