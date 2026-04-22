# KGSpin Demo Roadmap

**Owner:** VP of Product
**Last Updated:** 2026-04-19 (Sprint 12 close)
**Current Sprint:** 12
**Current Phase:** Visual Proof & Value Proposition

---

## Status Summary

The demolition of research code is complete. We are now focusing on a high-fidelity visual workbench and side-by-side comparisons that prove the "KGSpin Performance Delta" to enterprise design partners.

## Prioritization Model

Items within each horizon are prioritized by **RICE score** (Reach x Impact x Confidence / Effort). Hard technical dependencies override RICE ordering — a lower-scored prerequisite ships first.

| Factor | Scale | Description |
|--------|-------|-------------|
| Reach | 1-10 | Users/workflows affected |
| Impact | 1-5 | Per-user value (1=minimal, 3=significant, 5=transformative) |
| Confidence | 0.5-1.0 | Evidence strength (0.5=speculation, 0.8=solid, 1.0=proven) |
| Effort | T-shirt → sprints | XS=0.5, S=1, M=2, L=3, XL=5 |

---

## Strategic Horizons

### Horizon 1: Visual Proof (Active)
Establishing the core visualization and provenance lineage while ensuring a seamless developer environment.

| # | Milestone | PRD | RICE | Dependency | Status |
|---|-----------|-----|------|-----------|--------|
| 1 | Centralized LLM Routing | [PRD-044](prds/PRD-044-centralized-llm-routing.md) | 21.6 | None | Draft |
| 2 | KG Explorer Frontend | [PRD-002](prds/PRD-002-kg-explorer.md) | 8.0 | None | In Progress |
| 3 | Zero-Config Setup | [PRD-018](prds/PRD-018-zero-config-setup.md) | 9.0 | None | In Progress |
| 4 | Developer SDK & MCP Server | [PRD-025](prds/PRD-025-sdk-mcp-server.md) | 12.8 | None | In Progress |

### Horizon 2: Feedback & Comparison (Planned)
Connecting human-in-the-loop signals to the core tuning loop and proving the KG value proposition vs. LLMs.

| # | Milestone | PRD | RICE | Dependency | Status |
|---|-----------|-----|------|-----------|--------|
| 1 | KG vs LLM Comparison Demo | [PRD-004](prds/PRD-004-kg-vs-llm-comparison.md) | 18.0 | PRD-002 | Approved |
| 2 | Cross-domain News & Clinical Correlation | [PRD-007](prds/PRD-007-cross-domain-clinical-expansion.md) | 10.5 | ADR-004 | In Progress |
| 3 | Demo Diagnostics & Assessment | [PRD-048](prds/PRD-048-restructure-demo-metrics.md) | 14.4 | PRD-004 | Deferred |
| 4 | Analysis Section Redesign | [PRD-039](prds/PRD-039-analysis-matrix.md) | 14.0 | PRD-004 | Draft |
| 5 | Admin Console | [PRD-003](prds/PRD-003-admin-console.md) | 12.6 | PRD-002 | Approved |
| 6 | Interactive Graph Validations | [PRD-042](prds/PRD-042-interactive-graph-validations.md) | 12.0 | PRD-002 | Approved |
| 7 | Agentic Quality Refactor | [PRD-045 (Tuner)](https://github.com/kgspin/kgspin-tuner/docs/roadmap/prds/PRD-045-agentic-quality-refactor.md) | 9.6 | PRD-044 | Draft |
| 8 | HITL Feedback UI | [PRD-041](prds/PRD-041-hitl-feedback-ui.md) | 4.6 | PRD-042 | Draft |

### Horizon 3: Ecosystem & Scale (Exploratory)
Expanding the reach through on-boarding simplified and proven technical lineage.

| # | Milestone | PRD | RICE | Dependency | Status |
|---|-----------|-----|------|-----------|--------|
| 1 | Domain Onboarding Wizard | [PRD-005](prds/PRD-005-domain-onboarding.md) | 80.0 | None | In Progress |
| 2 | LLM-Guided Cold Start | [PRD-103](prds/PRD-103-llm-guided-cold-start.md) | 11.6 | PRD-005 | Approved |
| 3 | "Definition Moment" Viz | [PRD-054](prds/PRD-054-definition-moment-viz.md) | 12.8 | PRD-002 | Draft |
| 4 | Resolution Lineage | [PRD-053](prds/PRD-053-resolution-lineage.md) | 10.0 | PRD-054 | Draft |
| 5 | Topological Connectivity Sieve | [PRD-043](prds/PRD-043-topological-seed-anchor-sieve.md) | 3.6 | [RELOCATE] | Approved |

---

## Completed Milestones (Archived)

| # | Milestone | PRD | RICE | Sprint | Notes |
|---|-----------|-----|------|--------|-------|
| 1 | Core Extraction Engine | — | — | 00 | Initial port from KGenSkills completed |
| 2 | Research Code Purge | — | — | 01 | Deprecated ad-hoc curation scripts and direct Gemini calls |

---

## Success Metrics

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Interaction Latency | < 200ms | TBD | On Track |
| Demo Completion Rate | 100% | TBD | On Track |
| Setup Success Rate | > 95% | TBD | On Track |

---

## Technology Decisions

| Decision | ADR | Rationale (one line) |
|----------|-----|---------------------|
| MCP for Agents | ADR-025 | Standardizing how agents interact with the graph |
| SurrealDB Embedded | ADR-018 | Removing Docker dependency for demos |

---

## Changelog

| Date | Change | By |
|------|--------|-----|
| 2026-04-15 | Harmonized roadmap: resolved PRD collisions, backfilled RICE scores, and re-indexed modular scope. | Prod |
| 2026-04-14 | Drafted PRD-044 and PRD-045 for LLM routing and quality refactor; updated roadmap. | VP of Product |
| 2026-04-14 | Audited all PRDs, backfilled RICE scores, and restructured roadmap to template v2. | VP of Product |
