# PRD-005: Domain Onboarding Wizard

**Status:** In Progress
**Effort:** S (1 week)
**Dependencies:** PRD-001 (kgspin-core), PRD-001b (kgspin-api)
**Last Updated:** 2026-04-19

---

## 1. Goal

Build a three-step wizard at `/onboard` that demonstrates how fast a new domain can be brought online: define YAML patterns → compile to semantic fingerprints → test extraction on sample text.

## 2. Background

"How long does it take to add our domain?" The onboarding wizard answers this live in the demo:
1. Define/Modify YAML.
2. Compile (200ms).
3. Test Extraction on sample text.

This converts the design partner from observer to participant.

## 3. Requirements

### Must Have
- **Step 1 — Define**: YAML editor (Monaco).
- **Step 2 — Compile**: Call `POST /compile` in kgspin-core.
- **Step 3 — Test**: Side-by-side extraction with mini-graph (Cytoscape.js).

## 4. RICE Analysis
| Factor | Value | Rationale |
|---|---|---|
| Reach | 8 | Primary acquisition tool for the demo; post-Sprint 11 DOMAIN_FETCHERS adds a one-line path but audience estimate calibrated down. |
| Impact | 5 | Crucial for "minutes, not months" value prop. |
| Confidence | 1.0 | **Proven** per VP Prod Phase 3 Sprint 12 verdict — admin-driven pipeline + bundle dropdowns shipped; operator-self-service path exercised end-to-end. |
| Effort | 0.5 | **XS** — Sprint 12 admin-driven config + VP Prod Phase 1 post-sprint action ("Effort score for PRD-005 tasks to drop from S to XS"). |
| **Score** | **80.0** | |

---

## Changelog

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Relocated to kgspin-demo and updated RICE score. | Prod |
| 2026-04-19 | Sprint 11: Status Draft → In Progress; RICE Reach 10 → 8, Effort confirmed S. Per VP Prod 2026-04-17 consultation — DOMAIN_FETCHERS makes adding a news source for a new domain a one-line edit, directly advancing this PRD. | Dev |
| 2026-04-19 | Sprint 12 Phase 1: RICE Effort 1.0 → 0.5 (S → XS); Score 36.0 → 72.0. Per VP Prod Phase 1 mandatory post-sprint action — Sprint 12 admin-driven pipeline + bundle dropdowns complete the "operator self-service" arc. Adding a new domain is now "drop YAML into kgspin-archetypes + run `kgspin-admin sync archetypes`" with zero demo code change. Status unchanged (In Progress). | Dev |
| 2026-04-19 | Sprint 12 Phase 3: RICE Confidence 0.9 → 1.0 (proven); Score 72.0 → 80.0. Per VP Prod Phase 3 verdict (ACCEPTED WITH FOLLOW-UPS) — operator-self-service path exercised end-to-end via Sprint 12 admin-driven dropdowns + circuit-breaker + Mock-vs-Core gate. | Dev |
