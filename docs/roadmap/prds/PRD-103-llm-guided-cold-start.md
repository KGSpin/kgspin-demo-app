# PRD-103: LLM-Guided Cold Start Onboarding

**Status:** Approved  
**Milestone:** Phase 1 (Foundation)  
**Effort:** L (~3-4 sprints)  
**Last Updated:** 2026-04-15  

---

## 1. Goal

Enable a new user to go from "I have these documents" to a working v1 YAML bundle without manually authoring any YAML. An LLM investigates sample documents, proposes an entity hierarchy, and generates a v0 bundle.

## 2. Background

Creating a new YAML bundle is a 4+ hour process. Starting fresh with an LLM-first flow that generates clean, grounded YAML from actual document content sidesteps this barrier.

## 3. RICE Analysis

| Factor | Value | Rationale |
|---|---|---|
| Reach | 10 | Every new user/domain must go through onboarding. |
| Impact | 5 | Transforms 4-hour manual process to 15-minute guided flow. |
| Confidence | 0.7 | LLM schema generation is novel. |
| Effort | 3.0 | Large-scale orchestration with Claude Code skills. |
| **Score** | **11.6** | |

---

## Changelog

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Relocated to kgspin-demo and updated RICE score. | Prod |
