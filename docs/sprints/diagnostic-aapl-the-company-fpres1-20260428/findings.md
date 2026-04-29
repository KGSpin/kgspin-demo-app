# Diagnostic — AAPL "the Company" Extracted as Distinct Entity (FP-RES-1)

**Sprint:** `diagnostic-aapl-the-company-fpres1-20260428`
**Date:** 2026-04-28
**Repo:** kgspin-demo-app (branch `diagnostic-aapl-the-company-fpres1-20260428`)
**Type:** Diagnostic / RCA — findings only (root cause lives cross-repo)
**Symptom:** AAPL SEC extraction emits "Company" / "the Company" as a
discrete `ORG` entity instead of merging via self-ref into "Apple Inc.".
JNJ's same flow merges correctly. (FP-RES-1 in the new rubric taxonomy.)

---

## Verdict

**H1 (main_entity not propagated): NOT confirmed.**
**H2 (Title-case mismatch in current bundle): NOT confirmed.**
**H3 (painter does not fire in fan_out mode): PARTIALLY confirmed —
the painter is bypassed, but fan_out has its own coref pass that
*should* cover it.**

**Actual root cause: a slug-extraction asymmetry, not a coref-map or
propagation bug.**

The fan_out greedy / noun-phrase extractor only collects **capitalized**
tokens. In Apple's 10-K, the canonical self-reference phrase is `the
Company` (lowercase article). The extractor strips the leading `the`
and produces a single-token slug `Company`. The bundle-built
`coref_map` is keyed by the *full phrase* (`the company`), so
`coref_map.get("company")` returns `None` and the slug is emitted as
its own ORG node.

JNJ's filings primarily self-reference with sentence-start `We` /
`Our`, which the extractor *does* capture (capitalized at sentence
start), and the coref_map *does* contain `we` / `our` keys → JNJ
resolves cleanly. This is why the bug is corpus-shaped, not
ticker-coded.

---

## Trace

### main_entity propagation (H1)

Demo → core hand-off is symmetric for AAPL and JNJ:

- `KNOWN_TICKERS` (`demos/extraction/pipeline_common.py:27-39`)
  - `JNJ → "Johnson & Johnson"`
  - `AAPL → "Apple Inc."`
- `info["name"]` flows into `_run_kgenskills(...)` at
  `demos/extraction/demo_compare.py:8201` (intel SEC path) and 5625
  (compare-tab SEC path).
- `_run_kgenskills` (`demos/extraction/extraction/kgen.py:39-51`) calls
  `extractor.run_pipeline(main_entity=company_name, ...)` — no
  per-ticker special-casing.
- `KnowledgeGraphExtractor.run_pipeline` dispatches to
  `FanOutExtractor.extract` for `fan_out` (the demo's hardcoded
  strategy at `_pipeline_ref_from_strategy("fan_out")`).
- `FanOutExtractor.extract` (kgspin-core
  `execution/extractors/fan_out.py:62`) calls
  `kge._build_coref_map(main_entity)` and passes the result to
  `kge._fan_out_pass(...)`.

**No drop, no override, no per-ticker branch.** H1 is not the bug.

### Bundle tokens (H2 case mismatch)

Active default bundle is `financial-v0` (post commit `32514bf`,
2026-04-28). Verified its compiled `bundle.json`:

```
self_reference_tokens: ['the company', 'the registrant', 'the firm',
                        'we', 'our', 'our business']
domain: financial
```

All-lowercase, as required by the post-cleanup validator
(`_anti_contamination.validate_coreference_cleanup`). The fan_out
lookup is `coref_map.get(slug.raw_text.lower())`
(`fan_out_orchestrator.py:594, 600, 685`), so case is normalized at
both ends. **H2 is not the bug** for the v0 bundle.

(The historical demo-local `bundles/domains/financial-v22d.yaml` has
TitleCase tokens — `"the Company"`, `"the Registrant"` — and *would*
have hit a case-mismatch in earlier deploys, but that bundle is no
longer the default after the v0 switch.)

### Painter / fan_out coverage (H3)

`_build_chunk_anchor_matrix` (kg_orchestrator.py:938-1022) — the
character-range painter the CTO note references — has exactly **one**
call site: `_extract_relationships_glirel` at `kg_orchestrator.py:4969`.
That method is the GLiREL Head, owned by `KnowledgeGraphExtractor.extract`
(the H-Module / GLiREL pipeline), not by `FanOutExtractor.extract`.

Conclusion: the painter is **never invoked on the fan_out path**. H3 is
correct as a structural observation.

But fan_out has its own resolution logic at
`fan_out_orchestrator.py:582-604` (relationship endpoints) and
`684-687` (entity-dict emission). Both do
`coref_map.get(text.lower())`. So **for the fan_out path the painter
would be redundant** — coref resolution still happens, just at the
slug/triple level instead of the character-anchor level.

The asymmetry that *does* matter: `mine_filer_declaration`
(`_filer_declaration.py:223`), which scans 10-K declaration sentences
like *"References in this Annual Report to ‘we’, ‘our’, ‘us’, the
‘Company’ ... refer to <FILER> and its subsidiaries"* and harvests
the declared aliases (including bare `Company`), is also only called
from `KnowledgeGraphExtractor.extract` (`kg_orchestrator.py:2497`). The
`FanOutExtractor` path skips it. So even when AAPL's 10-K explicitly
declares `Company` as an alias for `Apple Inc.`, fan_out never sees that.

### Slug extraction (the actual bug)

Active config (`financial-v0` bundle):
- `fan_out_entity_mode = "greedy"`
- `greedy_slug_mode = False`

With `greedy_slug_mode=False`, `_discover_entities_for_sentence`
(`slug_fan_out.py:671-686`) falls back from `_greedy_extract_entities`
to `_extract_noun_phrases` (slug_fan_out.py:607-640):

```python
pattern = r"\b([A-Z][a-zA-Z''\-]*(?:\.?\s+(?:[A-Z][a-zA-Z''\-]*\.?|[A-Z]\.?))*)"
```

This regex matches strictly capitalized noun phrases. In `the
Company`, only `Company` matches; the lowercase `the` is dropped.

Result: `slug.raw_text = "Company"`. At `fan_out_orchestrator.py:685`:

```python
resolved = coref_map.get(slug.raw_text.lower())  # → coref_map.get("company")
```

`coref_map` keys are `the company`, `the registrant`, `the firm`,
`we`, `our`, `our business`, plus possessive variants of `Apple Inc.`.
**No bare `company` key.** Lookup returns `None`. `Company` is emitted
as its own `ORGANIZATION/COMPANY` node.

JNJ's filings primarily self-reference with `We` / `Our` at sentence
start, which the regex does capture (capitalized) and which `coref_map`
*does* contain → resolves to `Johnson & Johnson`. AAPL's filings
disproportionately use `the Company` as their canonical self-ref →
the bug is shaped by the AAPL corpus, not by anything AAPL-specific in
code.

---

## Recommended fix

The smallest, most surgical fix is **kgspin-blueprint** (cross-repo —
out of scope for this sprint, filed as follow-up):

Add `company` (single token) to `self_reference_tokens` in
`references/bundles/domains/financial/financial-v0.yaml`. Recompile
and re-sync admin.

```yaml
self_reference_tokens:
- the company
- company             # NEW — covers slug-extractor "the" stripping
- the registrant
- registrant          # NEW — symmetry; cheap
- the firm
- firm                # NEW — symmetry
- we
- our
- our business
```

This is a 3-line edit + recompile. It fixes AAPL without touching any
extractor logic and without the brittleness of relying on the painter.
Risk: low — bare `company` could in theory bind a non-self-ref mention
of "Company" (e.g. a competitor casually called "the Company" in
narrative), but in 10-K register `the Company` is regulated terminology
that always means the filer.

**Alternative (kgspin-core, also cross-repo)**: have the fan_out coref
pass try a `the ` prefix before falling back. One-liner at
`fan_out_orchestrator.py:594, 600, 685`:

```python
resolved = coref_map.get(text.lower()) or coref_map.get("the " + text.lower())
```

This is more general (handles `firm`, `registrant`, etc.
automatically) but lives in core. Both options are viable; the bundle
edit is lighter weight.

**Adjacent gap worth flagging**: the fan_out path skips
`mine_filer_declaration` entirely. Lifting that mining call out of
`KnowledgeGraphExtractor.extract` and into a shared pre-step that both
fan_out and the H-Module pipelines run would solve the broader class
of "alias declared in document body but missed by the pipeline" bugs
(of which AAPL is one instance). That's a Wave-level refactor, not
this sprint's scope.

---

## In-scope changes

**None applied.** The actual fix lives in:

- `kgspin-blueprint` (bundle YAML edit + recompile), or
- `kgspin-core` (`fan_out_orchestrator.py` lookup widening)

Both are out of scope per the sprint brief. This diagnostic emits
findings only; the fix is filed as a cross-repo follow-up for the next
hotfix cycle.

---

## Files referenced

- `demos/extraction/pipeline_common.py:27-48` — KNOWN_TICKERS, COREFERENCE_TOKENS
- `demos/extraction/demo_compare.py:8201, 5625` — SEC extraction call sites
- `demos/extraction/extraction/kgen.py:12-60` — `_run_kgenskills` façade
- `kgspin-core/src/kgspin_core/execution/extractor.py` (façade re-exports)
- `kgspin-core/src/kgspin_core/execution/extractors/fan_out.py:30-86`
- `kgspin-core/src/kgspin_core/execution/kg_orchestrator.py:938-1022` (painter)
- `kgspin-core/src/kgspin_core/execution/kg_orchestrator.py:2272-2296` (`_build_coref_map`)
- `kgspin-core/src/kgspin_core/execution/kg_orchestrator.py:2497-2508` (`mine_filer_declaration` invocation — H-Module path only)
- `kgspin-core/src/kgspin_core/execution/fan_out_orchestrator.py:582-604, 684-687` (fan_out coref resolution)
- `kgspin-core/src/kgspin_core/execution/slug_fan_out.py:607-640` (`_extract_noun_phrases` — capitalized-only regex)
- `kgspin-blueprint/references/bundles/compiled/domains/financial-v0/bundle.json` (active self_reference_tokens)
