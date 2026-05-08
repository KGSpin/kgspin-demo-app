# BUG-001 — kgspin-core ADR-038 shipped without a downstream-consumer migration note

**Filed:** 2026-05-07
**Owner ask:** kgspin-core team
**Severity:** P2 (cross-repo hygiene, not user-facing)
**Trigger:** live demo broke mid-presentation with `ImportError: cannot import name 'resolve_preprocessors' from 'kgspin_core.execution.preprocessors'` from `demos/extraction/demo_compare.py:_parse_and_chunk`.

## What happened

kgspin-core Sprint 14 (commit `e951c84`, 2026-05-02) implemented ADR-038 by deleting the legacy `kgspin_core.execution.preprocessors.resolve_preprocessors` stub and replacing it with the new `PreprocessorPipeline` API in `kgspin_core.preprocessing`. The change landed without a migration note in the kgspin-core handover memo or a downstream-consumer notification, and `kgspin-demo-app` was the sole stale call site. The break did not surface until a CEO demo because no integration smoke crosses the kgspin-core ↔ kgspin-demo-app boundary.

A second, related gap surfaced once we migrated the call site: the financial-v0.1 bundle's `preprocessors:` entries are dicts of shape `{name, phase, file_types}` — they lack the `version` field the new strict resolver requires (`_coerce_preprocessor_specs` in `pipeline.py`). The new API logs `ignoring malformed preprocessor entry … expected PluginSpec or {'name': ..., 'version': ...}` for every entry and silently returns an empty pipeline. So the demo now runs (no ImportError), but at parity with the *legacy stubbed-`[]` extraction quality* — not the actual ADR-038 promise. The bundle YAML in kgspin-blueprint needs a coordinated update; the kgspin-core team owns the schema contract.

## Asks

1. **Migration note convention:** add an "Affected downstream consumers" section to ADR sprint dev reports when an ADR removes/renames a public symbol. Include a one-line code-action recipe (e.g. "demo callers: `from kgspin_core.preprocessing import build_pipeline_from_bundle, build_preprocessor_context`").
2. **Release-note convention:** consider tagging breaking-API sprints with a top-of-handover banner ("⚠️ removes `resolve_preprocessors`; downstream callers must migrate to `PreprocessorPipeline`") so consumers grep their own repos before the next release.
3. **Bundle-schema migration coordination:** ADR-038 §3 ("Resolution failure is a hard error") plus the new strict `{name, version}` requirement implies kgspin-blueprint's `financial-v0.x` bundle YAML needs a `version:` field on every preprocessor entry. Without that, every consumer of every bundle silently no-ops the preprocessor chain. Right now the only thing keeping the demo alive is the soft `logger.warning(...)` permissive-on-malformed-entry path; one config tightening upstream and the demo would have hard-failed at construction. File a tracking issue against kgspin-blueprint or fold it into the Sprint 14 follow-up.

## Local hot-fix

Migrated `demos/extraction/demo_compare.py:_parse_and_chunk` to the ADR-038 API on hotfix branch `hotfix/adr-038-preprocessor-migration`, merged to `main` 2026-05-07. Regression test pinned at `tests/extraction/test_parse_and_chunk_adr038.py`. Demo unblocked; preprocessor chain is silent-empty until the bundle YAML is updated, but extraction quality is at-parity with the pre-ADR-038 stub behaviour, so the demo's behaviour did not regress.
