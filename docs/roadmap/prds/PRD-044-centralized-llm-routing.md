# PRD-044: Centralized LLM Routing Integration (Demo)

**Status:** Draft
**Author:** VP of Product
**Created:** 2026-04-14
**Last Updated:** 2026-04-14
**Milestone:** Infrastructure Alignment
**Initiative:** INIT-001

---

## Goal

Finalize the transition of `kgspin-demo` to the `kgspin-core` routing layer, specifically focusing on the Q&A comparison logic, and purge deprecated research code.

## Requirements

### 1. Agentic Q&A & Impact Refactor
The legacy Q&A and quality evaluation logic must be migrated to the `kgspin-core` / `kgspin-tuner` service layer.
- **Requirement**: Move `_ask_both` (agentic Q&A) and `_run_impact_quality_analysis` (comparative evaluation) to the `kgspin-tuner` service.
- **Requirement**: Deprecate the local SSE (Server-Sent Event) data structures that perform ad-hoc prompt construction.
- **Acceptance**: `demo_compare.py` delegates Q&A and Quality calls to a `TunerClient`.

### 2. HITL UX Refactor
Buttons and forms that generate golden data or provide feedback must be wired to the Tuner platform.
- **Requirement**: The "Generate Golden Data" (curation) button must trigger a specific backend task in `kgspin-tuner`.
- **Requirement**: User-provided feedback (e.g. "correcting" an entity) must be POSTed directly to the `kgspin-tuner` Evaluation API.
- **Acceptance**: No proprietary curation prompt logic remains in the demo source.

### 3. Research Code Deprecation [DELETE]
- **Requirement**: Delete `run_overnight_experiment.py` (Phase 2), legacy extractors, and ad-hoc curation templates.

## Success Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Deprecated Line Count | >2k | `git diff --stat` after cleanup. |
| Routed Q&A Percentage | 100% | Audit of Q&A traffic logs. |
