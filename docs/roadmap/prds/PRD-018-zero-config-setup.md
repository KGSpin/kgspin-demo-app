# PRD-018: Unified Zero-Config Experience (Installer & Setup)

**Status:** In Progress
**Author:** VP of Product
**Created:** 2026-04-13
**Last Updated:** 2026-04-14
**Milestone:** GTM Readiness
**Initiative:** Backlog

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 10 | Affects every user's first 5 minutes with the product |
| **Impact** | 2 | Minimal per-user value but critical for adoption/funnel |
| **Confidence** | 0.9 | Setup scripts are well-understood and patterns are proven |
| **Effort** | M=2 | Requires coordination of local binaries and environment checks |
| **RICE Score** | **9.0** | |

---

## 1. Goal

Provide a streamlined installation and initialization flow that abstracts away the complexity of SurrealDB, local models (GLiNER), and data directories.

## 2. Requirements

### 2.1 The `kgen setup` Command
- **Self-Diagnostic:** Checks for Python version, `uv` installation, and environment variables (ANTHROPIC_API_KEY).
- **Automated Directory Scaffolding:** Creates `output/registry`, `output/logs`, and `data/cache` with one command.
- **Model Prefetching:** Downloads the default `GLiNER` and `MiniLM` models locally so the first extraction is fast.
- **SurrealDB Auto-Binary:** If SurrealDB is missing, the setup script offers to download the static binary locally (no Docker required).

### 2.2 Global Config (`settings.json`)
- One central place for registry paths, database credentials, and default backends.
- Replaces hardcoded strings across scripts.

## 3. The "2-Minute" User Journey
1.  `uv pip install -e .`
2.  `kgen setup` (interactive wizard)
3.  `kgen extract --ticker AMD`
4.  **Success:** Browser opens to `http://localhost:8080` with the graph.

## 4. Implementation
- **CLI Wrapper:** Use `rich` for a beautiful, color-coded setup progress bar.
- **Lockfile:** Generate a `.kgen_ready` lockfile to prevent redundant pre-fetching.

## Changelog

| Date | Change | By |
|------|--------|-----|
| 2026-04-14 | Backfilled RICE score and updated status to In Progress. | VP of Product |
| 2026-04-13 | Created | VP of Product |
