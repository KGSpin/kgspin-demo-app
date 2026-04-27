# Clinical demo-vs-direct extraction discrepancy — findings

**Sprint:** sprint-clinical-demo-vs-direct-20260427
**Branch:** `diagnostic-clinical-demo-vs-direct-20260427` (off `main`)
**Author:** Dev Team (kgspin-demo-app)
**Date:** 2026-04-27
**Type:** Diagnostic-only (no production fixes)

---

## Executive summary

The kgspin-core team's direct fan_out invocation reproduced **15 ents / 11 rels**
on `clinical-v2` against the gold fixture for `NCT00174785`. The demo's
`/api/compare-clinical/NCT00174785` returns **0 ents / 0 rels** for the same
bundle and trial. The CTO asked whether this is admin-stale-bundle (Hyp 1) or
text-slice-mismatch (Hyp 2).

**Verdict: Hypothesis 2 (text-slice mismatch). Hypothesis 1 is falsified.**

The demo and the direct invocation resolve to **the same on-disk
`clinical-v2/bundle.json`** (same file path, same byte content). The
discrepancy is entirely on the input side: the demo defaults to
`corpus_source="live"` and feeds the extractor the **raw 192 KB JSON
record** from the ClinicalTrials.gov V2 API, whereas the kgspin-core
diagnostic fed it **16,959 chars of natural-language publication
abstracts** assembled from the gold fixture. Only ~2.5 % of the live
JSON record is descriptive prose; the rest is JSON syntax, eligibility
criteria, MeSH IDs, and date/status metadata. Anchor-driven fan_out has
nothing to extract from JSON braces, so the post-strip clinical-v2
bundle plus a JSON corpus deterministically yields 0/0.

A flip of the demo's clinical fixture path to feed the same gold-fixture
text the direct diagnostic used would reproduce the 15/11 result. No
admin invalidation is needed.

---

## Method

I did not run any new extractions. I traced the two code paths to a
common API call, compared the two bytes-on-disk inputs they feed in,
and matched both to the existing artifacts:

- Demo path: `demos/extraction/demo_compare.py:_run_clinical_comparison`
  → `_get_bundle("clinical-v2")` (via admin) → `_run_kgenskills(text=corpus_text, …, pipeline_config_ref=fan-out v1)`
  → `corpus_text = _try_corpus_fetch(nct_id).raw_html`.
- Direct path (kgspin-core): `kgspin-core@…/docs/sprints/sprint-clinical-extraction-diagnostic-20260427/run-fan_out.txt`
  → `Extractor` instantiated with the on-disk bundle dir, fed `text`
  built by concatenating `tests/fixtures/gold/clinical/NCT00174785.json`
  `input_documents[*].title + text`.

The kgspin-core `findings.md` already documented their direct-invocation
choices; I cross-referenced its `[INPUT] text_chars=16834` line and
re-derived the gold-text concatenation locally to confirm.

---

## Task 1 — Bundle the demo loads

The demo's clinical comparison endpoint
(`/api/compare-clinical/{doc_id}`, `demos/extraction/demo_compare.py:1770`)
defaults `bundle_name = bundle if bundle else "clinical-v2"` and calls
`_get_bundle(bundle_name)`.

`bundle_resolve._get_bundle("clinical-v2")` → `pipeline_common.resolve_domain_bundle_path("clinical-v2")`
→ `resolve_bundle_path("clinical-v2")`. That issues
`GET http://127.0.0.1:8750/resources?kind=bundle_compiled&domain=clinical`
to admin and returns `Path(<first matching pointer>).parent`.

Live admin (queried 2026-04-27) returns **two** `bundle_compiled` records
for clinical, both with pointer
`/Users/apireno/repos/kgspin-blueprint/references/bundles/compiled/domains/clinical-v2/bundle.json`:

| id | pointer | metadata.bundle_hash | registered_at |
| --- | --- | --- | --- |
| `bundle_compiled:clinical:6e7e5c2393dc` | `/Users/apireno/repos/kgspin-blueprint/.../clinical-v2/bundle.json` | `6e7e5c2393dc…` | 2026-04-21T20:22:10 |
| `bundle_compiled:clinical:f7a1fa8937e7` | (same path) | `f7a1fa8937e7…` | 2026-04-22T11:52:33 |

Both pointers are file paths to the same on-disk file. The current SHA-256
of that file (post Wave-1 strip, blueprint commit `b46ec00`, 2026-04-26)
is `531a77f35de0c1ac34e5dd292bc0bc6255608aa6ec6bf134e6bb12cdc8a83a4e`.

So the **demo loads the post-strip on-disk bundle**, not a stale
admin-cached blob. Admin's stored `metadata.bundle_hash` values are
out-of-date relative to the file's current content (the file was
re-compiled by Wave 1 after both admin registrations), but admin only
hands back a file *pointer* — the loader reads the current bytes off
disk via `ExtractionBundle.load(<dir>)`. There is no admin-side
content cache between the demo and the file.

There is a **per-process in-memory cache** at
`bundle_resolve._bundle_cache[bundle_name]` keyed by `"clinical-v2"`.
If the demo process was started before Wave 1 landed, that cache could
hold a pre-strip in-memory `ExtractionBundle`. However:

- `purge_caches()` (wired to `/api/purge-cache`) clears it.
- The post-Phase-D strict bundle validator rejects pre-strip bundles
  on load (kgspin-core findings §"Step 2b"), so a process that survived
  the strip would have failed loudly at startup or first cache miss
  rather than silently serving stale content.

Bundle identity (demo): file `/Users/apireno/repos/kgspin-blueprint/references/bundles/compiled/domains/clinical-v2/bundle.json`,
SHA-256 `531a77f3…`, post Wave-1 strip.

## Task 2 — Bundle the direct invocation uses

From `kgspin-core@…/docs/sprints/sprint-clinical-extraction-diagnostic-20260427/run-fan_out.txt`:

```
[INIT] strategy=fan_out bundle_dir=/Users/apireno/repos/kgspin-blueprint/references/bundles/compiled/domains/clinical-v2
       pipeline=fan-out.yaml
[BUNDLE] domain=clinical entity_fps=4 rel_fps=7 ent_anchors=6 rel_anchors=7
```

Direct path loaded the bundle from the on-disk dir at
`/Users/apireno/repos/kgspin-blueprint/references/bundles/compiled/domains/clinical-v2`,
i.e. the parent of the same `bundle.json` admin's pointer references.
No admin involvement (the kgspin-core harness instantiates `Extractor`
directly to bypass admin's `registry_client` dependency).

Bundle identity (direct): same on-disk dir, same `bundle.json`,
SHA-256 `531a77f3…`.

## Task 3 — Bundle comparison

**Same bundle.** Demo and direct both load the same physical file with
the same byte content. Hypothesis 1 (admin staleness) is **falsified**
for this discrepancy. The two stale `bundle_hash` entries in admin's
registry (Task 1 table) are a registry-hygiene issue but they do not
affect what the demo loads, because admin's pointer is a path, not a
content blob. They are flagged as a follow-up below but they did not
cause 0/0.

## Task 4 — Text the demo sends to fan_out

The demo's `_run_clinical_comparison` (`demos/extraction/demo_compare.py:7107`)
defaults `corpus_source = "live"` and takes the live branch:

```python
doc_adapter = await asyncio.to_thread(_try_corpus_fetch, nct_id)
corpus_text = doc_adapter.raw_html or ""
```

`_try_corpus_fetch("NCT00174785")` (line 383) resolves to admin's
`corpus_document` registry. Live admin has one entry for `NCT00174785`
pointing at `/Users/apireno/.kgspin/corpus/clinical/clinicaltrials_gov/NCT00174785/2026-04-22/trial/raw.json`,
which is the **raw JSON response from `https://clinicaltrials.gov/api/v2/studies/NCT00174785`**
that the `ClinicalLander` (`src/kgspin_demo_app/landers/clinical.py:101`)
streamed to disk. The adapter (`_adapt_to_sec_doc_shape`, line 176)
decodes the bytes as UTF-8 and stuffs them into the
`SimpleNamespace.raw_html` slot. The clinical compare path then passes
those bytes verbatim into `_run_kgenskills` as `text=corpus_text`.

Concrete characteristics of what the demo feeds the extractor:

- **Path:** `/Users/apireno/.kgspin/corpus/clinical/clinicaltrials_gov/NCT00174785/2026-04-22/trial/raw.json`
- **Length:** 192,427 chars (192 KB) — JSON document, not prose.
- **First 200 chars:**

  ```
  {"protocolSection":{"identificationModule":{"nctId":"NCT00174785","orgStudyIdInfo":{"id":"EFC5555"},"organization":{"fullName":"Sanofi","class":"INDUSTRY"},"briefTitle":"A Trial With Dronedarone to Pr
  ```

- **Last 200 chars:**

  ```
  urans"},{"id":"D006574","term":"Heterocyclic Compounds, 2-Ring"},{"id":"D000072471","term":"Heterocyclic Compounds, Fused-Ring"},{"id":"D006571","term":"Heterocyclic Compounds"}]}},"hasResults":true}
  ```

- **Prose-bearing fields inside the JSON:**

  | Field | Chars |
  | --- | --- |
  | `protocolSection.descriptionModule.briefSummary` | 260 |
  | `protocolSection.descriptionModule.detailedDescription` | 1,089 |
  | `protocolSection.eligibilityModule.eligibilityCriteria` | 3,443 |
  | **Total prose** | **~4,792 / ~192,427** = **2.5 %** |

The remaining ~97.5 % is JSON structure, IDs, dates, status enums,
outcome-measure rows, MeSH-term lists, etc. Anchor-driven fan_out
expects natural-language prose with token-bordered keyword hits and
subject/object dependency patterns. JSON braces, quoted IDs, and
key strings produce few of those. The 4–5 KB of actual prose buried
inside the document is small enough that, after the
`DocumentChunker(max_chunk_size=bundle.max_chunk_size)` splits on the
JSON's (sparse) sentence boundaries, anchors mostly fire on noise
tokens and the candidate triples generated by the CSP scoring stage
fail confidence floors. Net result: 0 entities, 0 relationships.

### What the direct invocation fed in

From `kgspin-core@…/run-fan_out.txt`:

```
[INPUT] text_chars=16834 main_entity='A Placebo-controlled,Double-blind,Parallel Arm Trial …
        Atrial Fibrillation/Atrial Flutter (AF/AFL)'
```

The harness builds `text` by concatenating `input_documents[*].title +
text` from `tests/fixtures/gold/clinical/NCT00174785.json`, i.e. the
gold path's branch in `_run_clinical_comparison` (lines 7202-7208 of
`demo_compare.py`). I re-derived the same concatenation locally:

- **Length (my recompute):** 16,959 chars (kgspin-core printed 16,834
  — within rounding, both runs are about 17 KB).
- **First 200 chars:**

  ```
  --- Publication: Dronedarone provides effective early rhythm control: post-hoc analysis of the ATHENA trial using EAST-AFNET 4 criteria. ---
  Dronedarone provides effective early rhythm control: post-h…
  ```

- **Last 200 chars:**

  ```
  …conclusion, dronedarone demonstrated both rhythm- and rate-controlling properties in ATHENA. These effects are likely to contribute to the reduction of important clinical outcomes observed in this trial.
  ```

This is fluent biomedical prose from 8 publications about ATHENA /
dronedarone. Sentence boundaries are well-formed; subject/object
patterns are clean; keyword anchors have natural surrounding context.
That is why fan_out emits 15/11 against the same bundle.

### Side note: a second demo-vs-direct mismatch (not the bottleneck)

The demo's `_run_kgenskills` call (`demos/extraction/extraction/kgen.py:42`)
passes `main_entity=company_name`, and the clinical compare path
(line 7351) hardcodes `company_name=""`. So the demo runs fan_out with
`main_entity=""`. The kgspin-core direct harness sets `main_entity` to
the trial's full title (the ATHENA full-form). Yet kgspin-core's
extraction emitted only `'?'` strings for the relationship subjects/
objects (their findings §Task 1, "Quality issues observed"), so
`main_entity` is not the dominant signal here — the input prose was.
Still, the demo's empty `main_entity` may be limiting some self-reference
resolutions even if the bundle were re-authored. Flagged as a
follow-up; not the cause of 0/0.

## Task 5 — Findings + remediation proposal

### Verdict

**Hypothesis 2 (text-slice mismatch) — confirmed.** The demo defaults
to `corpus_source="live"`, which feeds fan_out a 192 KB ClinicalTrials.gov
V2 JSON record (~2.5 % prose). The kgspin-core direct diagnostic fed
fan_out 17 KB of natural-language publication abstracts. Same bundle,
radically different inputs, radically different outputs.

**Hypothesis 1 (admin staleness) — falsified.** Admin's pointers are
file paths; the demo loads the current on-disk `clinical-v2/bundle.json`
(SHA `531a77f3…`, post Wave-1 strip). Admin's two stale `bundle_hash`
entries are a registry-hygiene smell but do not affect what bytes the
demo loads.

### What the kgspin-core team's mental model implied

Their note ("my direct fan_out reproduces 15/11 from the same bundle,
while the demo reports 0/0") had a hidden premise: that "the same bundle"
implied "the same input." It does not. The kgspin-core diagnostic
pulled text from the gold fixture; the demo pulls text from
ClinicalTrials.gov via the live provider. Once that gap is closed, the
demo will reproduce 15/11 — but 15/11 is still the low-coverage
ceiling identified by their root-cause analysis (predicate vocabulary
gap), so it is not the right number to ship to a customer.

### Remediation proposal (do not execute this sprint)

Three layered fixes, in order of cost:

1. **Demo-side: align the clinical fixture path with the diagnostic.**
   The cheapest verification is to re-run the demo with
   `?source=gold` on `NCT00174785`. The gold branch
   (`_run_clinical_comparison` lines 7184-7218) builds text by
   concatenating `input_documents[*].title + text` exactly as the
   kgspin-core harness does. If that returns 15/11 (or close), Hyp 2
   is closed and we have a clean reproduction. **No code change
   required for this verification — just a query-string flip.**

2. **Demo-side cleanup if (1) confirms:** decide what the demo's
   "live" clinical input *should* be. Options:
   - **Option A — stay JSON, extract prose first.** Add a JSON-aware
     pre-processor between `_try_corpus_fetch` and `_run_kgenskills`
     that pulls `briefSummary + detailedDescription + eligibilityCriteria
     + outcomeMeasures.descriptions` (and any other prose-bearing
     fields) into a clean string before feeding the extractor. This
     keeps the live source authentic but drops 97 % of the noise.
   - **Option B — bundle preprocessor.** Move the JSON-to-prose step
     into the kgspin-core preprocessor stack (there's already a
     preprocessors directory at `kgspin-core/src/kgspin_core/execution/preprocessors/`)
     so any caller of `clinical-v2 + ClinicalTrials.gov` gets the same
     normalization for free.
   - **Option C — change the corpus.** Have the `clinicaltrials_gov`
     provider also fetch and concatenate linked PubMed publications;
     the publication corpus is what the bundle was *almost* designed
     for and what produces realistic relationship density.

   Recommendation: **B** is the right home long-term (every consumer of
   ClinicalTrials.gov V2 records will need this), but **A** is a
   one-file demo-side patch and is sufficient to unblock the demo
   while the bundle re-author lands. **C** is a separate scope.

3. **Bundle re-author (parallel kgspin-blueprint sprint, already
   identified by kgspin-core).** Even with prose-only input, the
   clinical-v2 bundle's predicate vocabulary covers only 1 of 6 gold
   predicates (`treats`). Without re-authoring, the demo's ceiling on
   the gold input is the 15/11 the diagnostic measured — better than
   0/0 but still mostly noise. Bundle re-author is out of scope here
   per CTO instructions; the kgspin-blueprint team's remediation
   proposal stands.

### Follow-ups (informational, not in scope for this sprint)

- **Admin registry hygiene.** Two `bundle_compiled` records for
  `clinical-v2` exist with stale `metadata.bundle_hash` values. They do
  not change loader behavior, but they will confuse anyone debugging
  via `GET /resources` and they nominally violate the "one current
  pointer per kind/domain/name" expectation. Propose:
  (a) collapse to a single `published` record keyed on the post-strip
  on-disk hash; (b) move the older two to `superseded` status so they
  remain auditable; (c) re-register on every blueprint compile so the
  metadata hash matches the file. This is a kgspin-admin sprint, not
  a demo-app sprint.
- **`main_entity=""` for clinical fan_out.** The clinical compare path
  at `demo_compare.py:7351` passes `company_name=""` into
  `_run_kgenskills`, so `main_entity` is empty. Consider passing
  `trial_title` (already resolved upstream) — it costs nothing and
  may improve self-reference resolution once the bundle / input
  alignment is fixed. This is a one-line follow-up.

---

## One-line summary

Same bundle, very different inputs: the demo feeds fan_out a 192 KB
JSON record (~2.5 % prose) from the live ClinicalTrials.gov provider
while the kgspin-core diagnostic fed it 17 KB of clean publication
prose from the gold fixture. Hyp 2 confirmed; admin staleness ruled
out; bundle re-author still owns the long-term ceiling.
