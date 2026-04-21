"""Local Landers — out-of-process data acquisition for the V8 architecture.

Sprint 07 (INIT-010 V8 Control Plane Alignment): each lander is a CLI
script that downloads raw data from a single source (SEC EDGAR,
ClinicalTrials.gov, NewsAPI, FDA RSS, etc.) and writes it to the
standardized ``FileStoreLayout`` on disk. The demo's extraction path
reads ONLY from the file store; it never hits an external API.

Landers are Protocol-FREE — they are scripts, not plugins. They do
not import ``kgspin_core.corpus``, ``kgspin_core.extraction``, or any
entity/relationship types (VP Eng "Domain Leakage" mandate).

See ``docs/sprints/sprint-07/sprint-plan.md`` for the full scope +
``docs/handovers/2026-04-15-sprint-07-jnj-fixture-path.md`` for the
file-store path convention.
"""
