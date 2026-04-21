# ADR-002: Demo-local `DemoMockProvider` for offline corpus ingestion

> **DEPRECATED — see [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md).** The `CorpusProvider` Protocol this ADR targeted no longer exists; demo has been migrated to `kgspin_interface.DocumentFetcher` + admin's HTTP registry (Sprint 09 REQ-007, Sprint 10 Task 4). Kept for historical context only.

**Status:** DEPRECATED — superseded by ADR-003 (Sprint 09 Task 8) and Sprint 10 Task 4 delivery
**Date:** 2026-04-13
**Deciders:** Dev Team (per Sprint 03 plan v3 for INIT-001, on core team direction 2026-04-13)
**Supersedes:** n/a
**Superseded by:** [ADR-003](ADR-003-fetcher-abc-and-admin-registry.md)
**RICE (product):** R=4, I=4, C=0.95, E=S → **≈ 12–16**

> **Supersession note (2026-04-17):** The ``CorpusProvider`` Protocol
> this ADR targeted was removed from ``kgspin-core`` in REQ-005
> (Sprint 17). Demo migrated to ``kgspin_interface.DocumentFetcher``
> + admin's HTTP registry in Sprint 09 (REQ-007). The
> ``DemoMockProvider`` implementation described below has been
> rewritten as ``MockDocumentFetcher(DocumentFetcher)`` with a
> ``DeprecationWarning`` at import time, kept for cross-repo
> compatibility during Sprint 09 ecosystem validation. See ADR-003
> for the new architecture.

---

## Context

`kgspin-core` INIT-005 shipped the `CorpusProvider` Protocol, the `BaseCorpusProvider` mixin, the `CorpusDocument` / `CorpusMetadata` dataclasses, the `@register_corpus_provider` registry, and the `kgspin corpus list/fetch/search` CLI commands. It deliberately did NOT ship any concrete providers (EDGAR, ClinicalTrials.gov, RSS, etc.) — those live in plugin repos per the INIT-004 cross-team execution plan. Plugin teams are unblocked and the canonical `EdgarProvider` from `kgspin-plugin-financial` is expected to ship in ~1-2 days as Sprint B of that repo's work.

The `kgspin-demo` compare UI wants to consume the Protocol API today — during Sprint 03 — so:

1. The data-ingestion path shares the same forward-compatible interface as the strategy dispatch rewiring in Sprint 03 Task 2 (`run_pipeline(strategy=...)`).
2. The demo works offline (no network, no API keys, no plugin deps) for local dev + CI.
3. The transition to real plugin providers is a non-event for the demo: one identifier swap per call site, no structural rewrites.

The core team explicitly directed us **away** from an EDGAR-specific shim:

> "Don't build an EDGAR-specific shim. It's throwaway work. Instead write a domain-agnostic `DemoMockProvider` in `kgspin-demo/tests/fixtures/mock_corpus.py` that loads test documents from a local directory by identifier. Same pattern as the `_SmokeMockProvider` in `kgspin-core/scripts/smoke/cli-smoke-check.sh`. ~15 lines. This becomes your CI fixture AND your offline-demo provider — you get both for the price of one."
>
> — kgspin-core Dev Team, 2026-04-13

## Decision

**Vendor a domain-agnostic `DemoMockProvider` at `src/kgspin_demo/corpus/mock_provider.py`, registered as `"demo_mock"` in the INIT-005 registry, that loads fixture documents from `tests/fixtures/corpus/{identifier}.{html,txt}`.**

The provider:

- Inherits from `kgspin_core.corpus.BaseCorpusProvider` (gets `fetch_async`, `list_available_async`, content-hash helper, optional filesystem cache for free).
- Declares `provider_id = "demo_mock"` and `domain = "mock"` — non-canonical values that can never collide with a real plugin provider.
- Implements `fetch(identifier)` by locating `{fixture_root}/{identifier}.html` or `{fixture_root}/{identifier}.txt` (HTML preferred) and returning a populated `CorpusDocument`.
- Implements `list_available(query, limit)` by enumerating fixture files via a depth-1 glob, respecting the `limit` argument.
- Is domain-agnostic: knows nothing about SEC filings, clinical trials, or RSS items. The identifier is the file stem; the caller provides its own domain semantics (if any).

Implementation footprint: ~120 lines including docstrings, none of it SEC-specific, none of it dependent on `kgspin-plugin-financial`.

### Fixture layout

```
kgspin-demo/
  tests/
    fixtures/
      corpus/
        JNJ.html           ← symlink to demos/extraction/.data/.../JNJ_10-K.html
        <future>.html
        <future>.txt
```

`JNJ.html` is a symlink into the existing EDGAR cache path so the mock serves the same bytes the pre-Sprint 03 direct `EdgarDataSource` path used. Future tickers / documents can be added as additional fixture files without any code change.

## Rationale — why domain-agnostic mock, not EDGAR shim

The plan v2 for Sprint 03 originally proposed an `EdgarDemoProvider` that wrapped `kgspin_plugin_financial.data_sources.edgar.EdgarDataSource` behind the Protocol interface, registered as `"edgar-demo"`. The core team rejected this for the following reasons, all of which stand:

### 1. Throwaway work

The EDGAR shim would be deleted the moment `kgspin-plugin-financial` ships its canonical `EdgarProvider`. The ADR for the shim would be retired along with it. One to two days of code would have a lifespan of one to two days. The `DemoMockProvider` has no expiration date — it remains useful as a CI fixture, as the offline-demo path, and as a template for any synthetic test case in any domain.

### 2. Domain leakage

The shim would be an `EdgarDataSource` wrapped in `CorpusProvider` clothing. Any failure mode in the shim (fetch retries, auth, rate limiting, filing date parsing) would pull `EdgarDataSource`-specific debugging knowledge into the demo repo — exactly the kind of cross-repo coupling that the open-core refactor worked hard to remove. The `DemoMockProvider` has zero coupling to any plugin.

### 3. The Protocol consumer API is already well-proven by `_SmokeMockProvider`

Upstream `kgspin-core/scripts/smoke/cli-smoke-check.sh` has a 15-line `_SmokeMockProvider` that exercises the full Protocol surface without touching any real data source. The `DemoMockProvider` is the file-backed version of that same pattern. Using a known-good upstream template eliminates the "does my Protocol consumer work?" question before we write a single line of concrete plugin code.

## Alternatives Considered

### Option A — EDGAR-specific shim (rejected by core team)

Would have worked but is throwaway. See section above. Core team vetoed.

### Option B — Wait for the plugin team's real `EdgarProvider`

Would block Sprint 03 for 1-2 days on cross-repo work that is out of our control. Would also mean the demo doesn't demonstrate the Protocol consumer pattern at all during the window when it matters most (the period between INIT-005 landing and plugin providers landing). The whole value of the Protocol-first architecture is that consumers aren't gated on concrete implementations.

### Option C — Direct `EdgarDataSource` import (skip the Protocol)

Defeats the point of INIT-005 entirely. The demo compare UI is the canonical consumer for the new Protocol; if the demo skips it, the Protocol has zero validated consumers at the time of merge.

### Option D — Upstream a stub provider to `kgspin-plugin-financial`

Cross-repo work, not the demo team's responsibility, and would block on the plugin team's review cycle. Same slowdown as Option B.

## Consequences

### Positive

- Sprint 03 closes on its own branch without any cross-repo coordination.
- The demo's compare UI validates the INIT-005 consumer API today.
- The mock provides permanent CI value: any future demo-side test that needs a fixture document can drop a file into `tests/fixtures/corpus/` and the mock serves it.
- The cut-over to real plugin providers is a one-line change per call site (`create_provider("demo_mock")` → `create_provider("edgar")`) or a clean domain-dropdown that routes by `provider.domain` tag.
- The mock imposes zero new dependencies on the demo venv.

### Negative

- The mock's `DemoMockProvider` does NOT appear in `kgspin corpus list` output. That CLI lives in kgspin-core and only sees providers registered at core-package import time. Demo-local registrations (triggered by `import kgspin_demo.corpus`) are invisible to the CLI because the CLI never imports demo code. This is a deliberate tradeoff, not a bug: a core CLI shouldn't reach into downstream packages. Documented here so future devs don't treat it as a bug to file upstream.
- The mock has `domain = "mock"`, not `"finance"`. The demo's compare UI currently assumes the financial domain anyway, so this is cosmetic. If a future domain-dropdown UI routes providers by `domain` tag, the mock may need to advertise `domain` more specifically (e.g., via a constructor arg or a subclass per fixture set).

### Neutral

- Fixture maintenance is now a demo-team concern. Adding a new ticker requires dropping a file into `tests/fixtures/corpus/` — trivial, but a chore.

## Conditions for Revisiting / Deletion

**DO NOT delete this ADR or the mock provider when `EdgarProvider` ships.** The mock is not scaffolding; it is a CI asset and an offline-demo path that remains useful indefinitely. What DOES change when `EdgarProvider` ships:

1. The demo compare UI's `create_provider("demo_mock")` lookup gains a peer `create_provider("edgar")` lookup, OR
2. A domain-dropdown UI routes between them by `provider.domain` tag.

Either way, `demo_mock` stays registered as the always-available mock. The mock becomes secondary when the canonical `EdgarProvider` is present, not obsolete.

**DO delete the mock** only if:

- All CI tests that depend on fixture files have been migrated to a different Protocol consumer pattern, AND
- The demo no longer has any offline use case, AND
- No developer ever wants to run the compare UI without a network connection.

None of those conditions are foreseeable.

## Implementation Notes

- `src/kgspin_demo/corpus/__init__.py` performs side-effect registration by importing `mock_provider`.
- `demo_compare.py` imports `kgspin_demo.corpus` at module load time so the registration happens before any extraction request.
- The fixture root defaults to `kgspin-demo/tests/fixtures/corpus/` (resolved via `__file__`). Constructor accepts an override for tests that want a different fixture set.
- `content_hash` is computed by `BaseCorpusProvider` / `CorpusDocument.__post_init__` — SHA-256 of the `text` field, no normalization, per INIT-005 VP Eng directive.
- For HTML fixtures, the mock populates both `text` and `raw_html` with the file content so (a) the content hash is meaningful and unique per file, and (b) downstream HTML parsers can use `raw_html` while downstream plain-text consumers can use `text`.
