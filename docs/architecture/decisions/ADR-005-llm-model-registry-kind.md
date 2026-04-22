# ADR-005: Dedicated `llm_model` registry kind (cross-repo)

**Status:** PROPOSED (cross-repo — requires kgspin-interface + kgspin-admin ack)
**Date:** 2026-04-19
**Deciders:** kgspin-demo Dev Team (author) → routed to CTO for kgspin-interface + kgspin-admin co-sign
**Supersedes:** n/a (additive)
**Superseded by:** n/a
**Related:** [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md), [ADR-004](ADR-004-backend-named-landers.md); kgspin-interface 0.6.0 (`PipelineConfig`, `PipelineConfigMetadata`, `PromptTemplateMetadata`); kgspin-admin Sprint 03-patch-req-006 (pipeline_config / prompt_template endpoints live 2026-04-19)

---

## RICE

| Factor | Value | Rationale |
|---|---|---|
| Reach | 7 | Every demo request that invokes Gemini (compare-demo KG extraction, quality analysis, clinical correlation); every future LLM consumer in the cluster (tuner, admin dashboards, new UIs). |
| Impact | 4 | Wrong-shape dependency is structural — LLM dropdown pricing + context-window data doesn't fit `PluginMetadata`'s generic shape. Right-shape registry unlocks per-model cost reporting, deprecation notices, and cross-provider swaps. |
| Confidence | 0.8 | Provider shapes (Gemini, Anthropic, OpenAI) are well-understood from public docs; one unknown is whether a unified `context_window_tokens` field captures enough for multimodal (vision-language) models. Starting text-only, extend as needed. |
| Effort | M | 2 sprints cross-team: kgspin-interface 0.7.0 cut + kgspin-admin endpoint wire-up + demo consumer code. Most is scaffolding. |
| **Score** | **(7 × 4 × 0.8) / 2 = 11.2** | |

---

## Context

`kgspin-demo` hardcodes the Gemini model catalog in three places:

1. [`GEMINI_MODEL_PRICING`](../../../demos/extraction/demo_compare.py#L401-L406)
   — dict mapping model id → `{price_per_1m_input, price_per_1m_output}`.
2. `VALID_GEMINI_MODELS` set — allow-list for runtime validation.
3. HTML `<option>` elements at
   [`compare.html:1434-1461`](../../../demos/extraction/static/compare.html#L1434-L1461)
   — dropdown.

Sprint 12's audit (driven by CTO directive 2026-04-19) flagged this as
admin-registry drift: adding a new model shouldn't require a demo code
change in three places. The Sprint 12 plan's Task 6 initially proposed
retrofitting LLM models as `ResourceKind.PLUGIN` records with
pricing in `source_extras`.

**VP Eng Phase 1 review on Sprint 12 plan (2026-04-19) issued a
BLOCKER on that approach.** Paraphrased verdict:

> Attempting to force-fit LLM models into the existing
> `PluginMetadata` schema carries a high risk of schema pollution.
> LLM models have specific attributes (context window, modality,
> provider-specific pricing) that differ from general plugins.
>
> Directive: Do not attempt to "try" `PluginMetadata` first if the
> mapping is not 1:1. I prefer an immediate cross-repo ADR for a
> dedicated `llm_model` resource kind. This ensures the registry
> remains type-safe and extensible for future providers (Anthropic,
> OpenAI, etc.) without overloading the plugin definition.

This ADR files that cross-repo proposal.

## Decision

Introduce a dedicated `ResourceKind.LLM_MODEL` + `LLMModelMetadata`
in kgspin-interface 0.7.0, and ship the matching admin endpoints in a
future admin sprint. Demo consumes the read path once both upstreams
land; until then demo's hardcoded fallback stays in place (flagged).

### 1. New resource kind

```python
# kgspin_interface/registry_client.py
class ResourceKind(str, Enum):
    ...
    LLM_MODEL = "llm_model"
```

`id` derivation (per existing `kgspin_interface.ids` convention):

```python
def llm_model_id(provider: str, model_id: str) -> str:
    """e.g. llm_model_id('gemini', 'gemini-2.5-flash') →
    'llm_model:gemini:gemini-2.5-flash'"""
```

### 2. New metadata model

```python
# kgspin_interface/resources.py
class LLMModelMetadata(BaseModel):
    """Admin-indexable metadata for a registered ``llm_model``
    resource. Full provider-spec YAML (rate limits, regional
    endpoints, per-tier quotas, etc.) lives in bytes at
    ``Resource.pointer`` if richer detail is needed later.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["gemini", "anthropic", "openai"]
    # extensible via Literal widening at interface version bump;
    # Sprint 12+ starts with the three shipping providers.
    model_id: str                   # provider's canonical name
    display_name: str               # operator-facing label
    context_window_tokens: int      # e.g. 1_048_576 for Gemini 2.5
    modality: Literal["text", "multimodal"]
    price_per_1m_input_tokens: Decimal
    price_per_1m_output_tokens: Decimal
    deprecation_status: Literal["active", "deprecated", "retired"] = "active"
    notes: str = ""                 # free-form operator notes
```

All fields are safe-for-export (no secrets); `safe_for_export_fields()`
returns the full field set.

### 3. Admin endpoints (to ship in a future kgspin-admin sprint)

- `POST /resources/llm_model` — register (reject-on-duplicate 409
  per admin's RFC-7807 convention).
- `GET /resources?kind=llm_model` — list; supports
  `?provider=gemini` filter and `?status=active` filter.
- `GET /resources/{id}` — get one.
- `PATCH /resources/{id}/status` — deprecation / retirement
  transitions (once admin's ADR-002 Bundle Activation Policy ships
  the generic endpoint, this piggybacks).
- `kgspin-admin sync archetypes` — extended to walk
  `llm_model/*.yaml` (same pattern as pipeline_config +
  prompt_template).

### 4. Archetypes seed files

Initial content for `kgspin-blueprint/llm_model/`:

- `gemini-2.5-flash.yaml`
- `gemini-2.5-flash-lite.yaml`

Future providers add themselves as siblings; no interface or admin
code change needed beyond widening the `provider` Literal at
interface bumps.

### 5. Demo consumer

Once interface 0.7.0 + admin endpoints ship:

- `HttpResourceRegistryClient.list_llm_models(provider=None,
  status="active")` helper.
- `demo_compare.py` replaces `GEMINI_MODEL_PRICING` dict reads with
  the admin call. Per-request pricing calculation uses the registry
  record; dropdown reads the same path.
- HTML `<option>` elements render from a `/api/models` endpoint that
  proxies the admin call (graceful-degrade to hardcoded fallback if
  admin is down — same circuit-breaker pattern as Sprint 12 Task 3's
  pipeline dropdown).

## Alternatives Considered

### Alternative 1: Retrofit `PluginMetadata`

Store LLM models as `ResourceKind.PLUGIN` records with pricing in
`source_extras`, context-window in `source_extras`, etc.

- **Pros:** no interface change; no new admin endpoint; Sprint 12
  could ship a working read path in-sprint.
- **Cons:** (VP Eng directive) schema pollution. `PluginMetadata` is
  generic by design; shoving provider-specific fields into its
  `source_extras` makes querying ("which LLM models support >1M
  context?") an untyped JSON-dig. Breaks Protocol-level guarantees.
  Every future consumer of the plugin registry has to know the
  "these plugins are actually LLMs, handle their source_extras"
  branch.
- **Why rejected:** VP Eng BLOCKER on the Sprint 12 plan review.
  Structural problem that only gets worse with more providers.

### Alternative 2: Demo-owned `llm_models.yaml` in kgspin-blueprint

Skip the admin registry entirely; ship a YAML file in the blueprint
repo that demo reads directly.

- **Pros:** cheapest path — no interface change, no admin endpoint,
  archetypes hosts the file.
- **Cons:** every future LLM consumer (tuner, admin dashboards,
  cross-cluster discovery) has to re-implement the YAML reader.
  Loses the admin's ACTIVE/DEPRECATED status-mutation lifecycle.
  Breaks the Sprint-12-wide goal of "admin is the single source of
  truth for cluster config."
- **Why rejected:** solves demo's immediate problem but punts the
  structural question. If we're making pipelines + prompts +
  bundles admin-driven this sprint, LLM models follow the same
  pattern — just not in the same sprint.

### Alternative 3: Ship `llm_model` kind inside kgspin-demo

Mint the kind inside demo's own code without kgspin-interface sign-off.

- **Pros:** demo ships Sprint 12 without waiting on anyone.
- **Cons:** violates ADR-003's rule that `ResourceKind` is owned by
  the interface package. Every downstream consumer (admin, tuner,
  core) would have to take a demo dependency to use the kind.
- **Why rejected:** cross-repo contracts live in the interface
  package. Non-negotiable.

## Consequences

### Positive

- **Type-safe LLM metadata across the cluster.** Every consumer gets
  `LLMModelMetadata` directly; no `source_extras` dig.
- **Extensible to new providers** via one-line Literal widening at
  interface bumps. Anthropic, OpenAI, local models (Ollama) all plug
  in naturally.
- **Unified registry lifecycle.** `deprecation_status` field lets
  admin (or ops tooling) flag retiring models without demo code
  changes. When Google retires Gemini 1.5, a single archetypes PR
  flips the status; demo reflects it on next refresh.
- **Clean separation of concerns.** `PluginMetadata` stays generic
  for non-LLM plugins (GLiNER, spaCy models, etc.). No cross-type
  pollution.
- **Per-request cost reporting becomes trivial.** Demo's current
  token-cost math in `demo_compare.py` reads per-call pricing from
  the registry record instead of a hardcoded dict.

### Negative

- **Two-sprint cycle before demo can use it.** Interface 0.7.0 ships
  first, then admin endpoints, then demo's read path. Sprint 12
  keeps the hardcoded fallback; actual wire-up slides to Sprint
  13+.
- **Another `ResourceKind` to maintain.** Each new kind adds
  validator code, ID helpers, sync-archetypes handlers, and admin
  routes. Acceptable cost per the VP Eng directive.
- **Migration of existing (hardcoded) pricing data.** Demo's current
  `GEMINI_MODEL_PRICING` values need to be captured faithfully into
  the seed YAMLs. Risk of transcription error; mitigated by a single
  diff review when archetypes adds the files.

## Cross-references

- [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md) —
  DocumentFetcher ABC + admin registry topology (the precedent for
  admin-owned type-safe metadata).
- [ADR-004](ADR-004-backend-named-landers.md) — backend-named
  lander pattern; same spirit applied to LLM providers here.
- kgspin-interface 0.6.0 release notes — defines `PipelineConfig`,
  `PipelineConfigMetadata`, `PromptTemplateMetadata`; ADR-005
  extends the same pattern for LLM models.
- kgspin-admin Sprint 03-patch-req-006 MEMO-TO-DEMO-AND-CORE
  (2026-04-19) — confirmed pipeline_config / prompt_template
  endpoints live; LLM model endpoints roll into a follow-on admin
  sprint once ADR-005 is accepted.
- Sprint 12 plan (demo) — Task 6 scoped to ADR drafting only; Task 6
  wire-up to Sprint 13+ per this ADR's "two-sprint cycle" note.
- CTO directive 2026-04-19 — Sprint 11 → 12 sequencing + ack protocol
  for cross-repo routing.

## Ack protocol

This ADR is cross-repo. It's not ACCEPTED until:

- **kgspin-interface** team acks the `LLMModelMetadata` shape (or
  proposes revisions) and targets the kind + metadata for 0.7.0.
- **kgspin-admin** team acks the endpoint set + `sync archetypes`
  extension for a forthcoming sprint (post-interface-0.7.0).
- **kgspin-blueprint** team acks hosting the `llm_model/` directory
  (raised in the 2026-04-20 blueprint handover memo).
- **CEO** green-lights the cross-repo sequencing (this ADR is the
  trigger for CTO to route to the other repos).

Demo's Sprint 12 does NOT wait on acks — demo ships the ADR, keeps
the hardcoded LLM fallback, and files a Sprint 13 task to wire up
the read path once the upstream pieces land.

— Dev Team (kgspin-demo)
