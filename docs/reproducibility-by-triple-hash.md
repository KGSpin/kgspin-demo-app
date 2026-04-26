# Extraction reproducibility by triple-hash

**Audience:** customers integrating with KGSpin's extraction API.
**Phase 2 INSTALLATION (CTO 2026-04-26).**

Every extraction this platform returns now carries a three-part version
fingerprint — the *triple-hash* — that lets you prove two extractions
came from identical platform configuration and demand the platform
reproduce a past extraction modulo declared model non-determinism.

## The three hashes

| Field on the wire | Customer-facing name | What it pins |
|---|---|---|
| `pipeline_version_hash` | pipeline version | The deployed extractor's git commit + the `kgspin-interface` schema version constants. Changes when the platform deploys new extractor code. |
| `bundle_version_hash` | domain bundle version | The compiled extraction bundle (entity / relationship fingerprints, thresholds, type constraints). Changes when the domain content changes — new patterns, retuned thresholds, new constraints. |
| `installation_version_hash` | deployment configuration version | The operator-managed engine config (chunking budget, parallelism, NLI candidate caps). Changes when the operator tunes how the engine runs against your installation. |

All three are 16-character hex strings. A `None` value means "this slot
was not recorded for this extraction" — typically because the extraction
predates Phase 2 (legacy cached run) or because admin was unreachable
and the platform fell back to library defaults. We never invent a
value to fill the slot.

## Where the hashes appear

### JSON API

Every extraction-returning endpoint includes `extraction_metadata` at
the top level:

```json
{
  "entities": [...],
  "relationships": [...],
  "bundle_version": "v1.2.3",
  "processing_time_ms": 412.7,
  "extraction_metadata": {
    "schema_version": 1,
    "pipeline_version_hash": "00d9b544577a7abc",
    "bundle_version_hash": "5e96a76e7715f5b8",
    "installation_version_hash": "abc123def4567890"
  }
}
```

The block appears on `/extract/relationships`, `/extract/entities`
(with `extraction_metadata: null` since GLiNER entity-only calls don't
touch the orchestrator that mints the triple), and `/extract/establish`.

### MCP tools

The same `extraction_metadata` block appears on the
`extract_relationships`, `extract_entities`, and `establish_relationship`
tool outputs.

### Cached-runs UI

Every cached run's detail JSON carries `extraction_metadata`. Runs
captured before Phase 2 landed render `<pre-Phase-2>` for fields that
were not recorded at extraction time — there is no backfill.

## What the customer should do with these hashes

### Property V — verifiable identity

If you receive two extractions and their triples match, you know they
came from identical pipeline, bundle, and installation configurations.
Differences in extraction output between matching triples are bounded
by declared model non-determinism (see "What stays identical, what may
vary" below).

### Property P — pinnable reproduction

The platform exposes a replay endpoint that re-runs extraction with
the requested triple pinned:

```
POST /extract/replay/relationships
{
  "text": "...",
  "source_document": "...",
  "pipeline_version_hash": "00d9b544577a7abc",
  "bundle_version_hash": "5e96a76e7715f5b8",
  "installation_version_hash": "abc123def4567890"
}
```

If the requested triple matches the deployment's currently-loaded
triple, the endpoint returns the same shape as `/extract/relationships`
plus an echo of the triple in `extraction_metadata`.

If any of the three hashes does not match, the endpoint returns 409
with both the requested and installed triples so you can see what
version this deployment is on:

```json
{
  "detail": {
    "error": "triple_hash_mismatch",
    "message": "...",
    "requested": {
      "pipeline_version_hash": "...",
      "bundle_version_hash": "...",
      "installation_version_hash": "..."
    },
    "installed": {
      "schema_version": 1,
      "pipeline_version_hash": "...",
      "bundle_version_hash": "...",
      "installation_version_hash": "..."
    }
  }
}
```

This is the **match-or-409** replay shape. Per-hash historical replay
(fetch arbitrary bundle / installation by hash and reconstruct that
deployment in-process) is a Phase 2.1 follow-up; today's deployment
can replay against today's loaded triple.

## Worked example — verify and replay

```bash
# 1. Extract from a document.
curl -X POST https://demo.kgspin.example/extract/relationships \
  -H 'Content-Type: application/json' \
  -d '{"text": "Apple acquired Beats in 2014 for $3 billion.", "source_document": "doc-1"}' \
  | tee extract-1.json

# 2. Capture the triple from the response.
jq '.extraction_metadata' extract-1.json
# {
#   "schema_version": 1,
#   "pipeline_version_hash": "00d9b544577a7abc",
#   "bundle_version_hash": "5e96a76e7715f5b8",
#   "installation_version_hash": "abc123def4567890"
# }

# 3. Replay against the captured triple.
curl -X POST https://demo.kgspin.example/extract/replay/relationships \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Apple acquired Beats in 2014 for $3 billion.",
    "source_document": "doc-1",
    "pipeline_version_hash": "00d9b544577a7abc",
    "bundle_version_hash": "5e96a76e7715f5b8",
    "installation_version_hash": "abc123def4567890"
  }' \
  | tee extract-2.json

# 4. Diff the entity / relationship sets — they should be identical.
jq '[.entities[] | {text, type}] | sort' extract-1.json > set-1.json
jq '[.entities[] | {text, type}] | sort' extract-2.json > set-2.json
diff set-1.json set-2.json
```

## What stays identical, what may vary

Even with all three hashes pinned, the platform extracts against
non-deterministic ML models (LLMs, embeddings). Here is what you can
and cannot rely on:

| Property | Reproducible across pinned triples? |
|---|---|
| Entity set (text + entity_type tuples) | Yes |
| Relationship set (subject + predicate + object tuples) | Yes |
| `bundle_version` (flat) and `extraction_metadata` block | Yes |
| Confidence scores | At ~3 decimal places. The 4th decimal can vary due to model floating-point edge cases. |
| `processing_time_ms` | No (wall-clock dependent). |
| Order of entities / relationships in the response | Yes (deterministic post-processing). |

If you need byte-identical reproduction, run on identical hardware and
pin all three hashes; even then, bit-level reproducibility for floating
point on GPU is not guaranteed by the underlying ML stack.

## When `installation_version_hash` is `None`

The platform records `null` when admin was unreachable at extraction
start and the engine fell back to library defaults. We do not invent
a hash to fill the slot. If you see `null` here you should treat the
extraction as not-pinnable for replay until the installation hash is
recorded again — this is rare and surfaces in operator logs.

## Cross-references

- ADR-004 (kgspin-interface): the canonical 3-YAML config architecture.
- `docs/architecture/decisions/ADR-006-three-yaml-config-architecture-rollout.md`:
  this repo's slice of the rollout.
- `docs/cross-repo/2026-04-26-phase-2-installation-notice-received.md`:
  cross-repo acknowledgment of the kgspin-core completion notice.
