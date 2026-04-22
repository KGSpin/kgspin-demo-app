# PRD-041: HITL Feedback UI Elements

**Status:** Draft
**Owner:** VP of Product, CEO
**Date:** 2026-03-03
**RICE Score:** 4.6 (Reach: 5, Impact: 4, Confidence: 0.7, Effort: 3)


## High-Level Vision
Following PRD-042 (Interactive Graph Validations), the React/Cytoscape frontend must support seamless Human-In-The-Loop feedback collection. This UI converts the demonstrator from a read-only viewer into an active data acquisition tool.

## Features & UX Requirements

### 1. Trigger Mechanism & Submission Forms
- We will leverage the existing Cytoscape Node/Edge details modal.
- When an entity or relationship is clicked, the modal will display its lineage, evidence, and confidence score.
- **Action Links:** A "Flag as Incorrect" (for KGS graphs) or "Save to Gold Dataset" (for LLM graphs) button will be appended to the bottom.
- **False Positive Form (KGS):** Clicking the link replaces the modal content with a quick dropdown for rejection reason (e.g., "Incorrect Entity Type", "Hallucinated Relationship").
- **False Negative Form (LLM):** Clicking the link reveals a rigorous validation form to prevent the ingestion of hallucinated LLM data:
  1. The user selects the two entities (Subject/Object) involved.
  2. The user selects the Relationship from a dropdown strictly populated by the active Bundle's predefined schema (no free-text inputs).
  3. The user must highlight/approve the **verbatim evidence sentence** from a scrollable source-document text box embedded in the modal.

### 2. Visual Clarity & Hover States
- Graphs comparing LLM vs. KGS can be extremely dense. 
- When a user hovers over the "Flag" link in the details modal, the corresponding edge in the canvas must visually highlight (glow effect, thickness increase) while fading surrounding edges to 30% opacity. This ensures the user knows exactly what relationship they are validating.

### 3. Immediate Visual State Updates
- **Instant Satisfaction:** When a user successfully submits a flag/validation form, the Cytoscape canvas must update instantly. 
- If flagged as a **False Positive** (Incorrect), the edge turns **Red**, indicating its flagged status.
- If flagged as a **False Negative** (Missed but correct), the edge turns **Gold**, indicating its inclusion in the Tuning Loop dataset.

### 4. Retraction
- If a user clicks on an already-flagged (Red or Gold) relationship, the details modal will show its current flagged state.
- **Action Link:** The modal will offer a "Retract Flag" button, immediately returning the edge to its default visual state in the UI and executing a delete operation against the feedback API.

### 5. AI-Assisted False Positive Suggestion (The "Little LLM Agent")
- To speed up HITL validation, a background agent will scan the extraction graph and suggest potential False Positives for the user to review.
- **Requirement:** This agent MUST use the `semantic_definition` field from the active Bundle for every entity/relationship type.
- **Logic:** The agent compares the extraction evidence against the `semantic_definition`. If a triple violates the definition (e.g., a "PERSON" extraction that clearly refers to a "COMMODITY" based on the definition), it flags it as a "Suggested FP."
- **UX:** Suggested FPs appear as **Dashed Orange** edges in the canvas, with a "Review Suggestion" button in the details modal.

## Success Metrics
- Users can flag an edge within 2 clicks from the details modal.
- Hover highlighting explicitly maps the modal form to the canvas edge.
- Visual state updates are fully responsive and synchronous with the API response.
