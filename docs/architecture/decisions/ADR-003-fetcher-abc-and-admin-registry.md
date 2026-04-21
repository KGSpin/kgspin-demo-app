# ADR-003: Demo landers adopt `DocumentFetcher` ABC + admin HTTP registry

**Status:** ACCEPTED
**Date:** 2026-04-17
**Deciders:** Dev Team (per Sprint 09 plan, on REQ-007 coordination memo from kgspin-admin)
**Supersedes:** [ADR-002](ADR-002-demo-mock-corpus-provider.md)
**Superseded by:** n/a

**RICE (Sprint 09 ADR header — per VP Eng MAJOR directive):**

| Factor | Value | Rationale |
|---|---|---|
| Reach | 10 | Affects 100% of demo's data ingestion (all 4 landers) + the registry integration point for every corpus_document flowing downstream. |
| Impact | 4 | Critical — unblocks core's REQ-005 disk-walk retirement, admin's Sprint 04 F1 dashboard, and tuner's upcoming REQ-008. |
| Confidence | 0.8 | Interface 0.5.0+ is tagged; admin's HTTP routes tested (22 tests in `test_routes_resources.py`); live integration pending Sprint 09 Task 9 smoke. |
| Effort | M | 2 sprints cross-team (demo Sprint 09 + admin Sprint 03/04 consolidation). |
| **Score** | **(10 × 4 × 0.8) / 2 = 16.0** | |

---

## Context

Three upstream cutovers landed before Sprint 09:

- **REQ-004 → `kgspin-interface` 0.5.0** (re-verified on disk as 0.6.0
  — see `compatibility-report.md`): retired `FileStoreLayout`;
  shipped `DocumentFetcher` ABC, `FetchResult`, `Pointer` discriminated
  union, `ResourceRegistryClient` Protocol, `FetcherError` /
  `FetcherNotFoundError` hierarchy, and canonical id helpers
  (`kgspin_interface.ids`).
- **REQ-005 → `kgspin-core` Sprint 17**: extraction reads corpus
  **only** via `ResourceRegistryClient.list(CORPUS_DOCUMENT, ...)`.
  Disk-walking was removed. Any corpus that isn't registered is
  invisible to extraction.
- **Admin HTTP registry shipped** (kgspin-admin Sprint 02): 22
  passing tests in `kgspin-admin/tests/http/test_routes_resources.py`
  cover the full route surface. Demo reads these to align request
  bodies (Task 2 cross-read surfaced two shape bugs that were fixed
  in-sprint).

Without Sprint 09 closing, demo's landers continued writing to a
local stand-in `FileStoreLayout` that nothing downstream reads —
documents were invisible to core's extraction. REQ-007 is the
coordination memo driving the cutover.

## Decision

The demo repo adopts five related architectural choices, all
interlocking:

### 1. Landers are `DocumentFetcher` subclasses (not `CorpusProvider`)

Each of the 4 lander files gets a class (`SecLander`,
`ClinicalLander`, `YahooNewsLander`, `HealthNewsLander`) inheriting
from `kgspin_interface.DocumentFetcher`. Each class declares:

- `name: str` — the canonical `fetcher_id` (used for resource id derivation)
- `version = "2.0.0"` — demo lander semver
- `contract_version = DOCUMENT_FETCHER_CONTRACT_VERSION` — the
  interface-defined ABC contract revision
- `fetch(self, domain, source, identifier, **kwargs) -> FetchResult`

Kwargs carry per-source state (`user_agent`, `api_key`, `article`,
`output_root`, `date`) without expanding the ABC signature — plan
Task 3 §2.

### 2. Admin is the registry, accessed via a local HTTP adapter

`src/kgspin_demo/registry_http.py` hosts
`HttpResourceRegistryClient` — a 170-LOC adapter satisfying the
`ResourceRegistryClient` Protocol (`@runtime_checkable`). Implements
5 methods demo uses (`register_corpus_document`, `register_fetcher`,
`list`, `get`, `resolve_pointer`) and stubs the other 6 with
`NotImplementedError` to stay Protocol-conformant.

### 3. Deployment topology: standalone CLI / cron / docker one-shot

Landers run OUT of the admin process. Current invocation pattern
(`python -m kgspin_demo.landers.<name> ...`) survives the migration;
no library-client coupling between demo and admin. This was the
decisive factor eliminating the library-client alternative during
sprint planning.

### 4. Storage default: local disk under `0o700` parent dirs

`$KGSPIN_CORPUS_ROOT/<domain>/<source>/<identifier>/<date>/<artifact_type>/raw.<ext>`

- `$KGSPIN_CORPUS_ROOT` defaults to `~/.kgspin/corpus/`
- Parent tree chmod'd to `0o700` so corpus contents aren't
  world-readable even under a permissive umask (VP Sec audit)
- Per-filename leaf inside each artifact dir
- Path helper centralized at `_shared.default_artifact_path(...)`

S3Pointer support deferred. `Pointer` is a discriminated union in
the interface, so adding S3 later is a backwards-compatible swap of
the lander's `output_root` resolver — not a contract change.

### 5. `mock_provider.py` rewritten as deprecated DocumentFetcher

Per VP Eng MAJOR: do not delete `mock_provider.py` even if no
in-repo importer exists; cross-repo consumers (tuner? core tests?)
may still depend on it. `MockDocumentFetcher(DocumentFetcher)` now
provides a fixture-backed `fetch()`. A `DeprecationWarning` fires at
module import time. Removal scheduled for a post-Sprint-09 Hardening
sprint once ecosystem validation confirms no cross-repo consumers.

---

## Security Debt — local-loopback assumption

Per VP Sec HIGH: this sprint's auth posture is **explicitly labeled
Security Debt**, not a production-grade solution.

### Current posture (Sprint 09)

- **`X-Actor` header is identification, not authentication.** Admin
  trusts whatever value any caller on the loopback interface sends.
  Two demo CLIs send `fetcher:<name>`; `register-fetchers` sends
  `demo:packager`. Admin's registered-by provenance records the
  string verbatim.
- **HTTPS enforced for non-loopback hosts** (VP Sec MEDIUM) —
  `HttpResourceRegistryClient.__init__` raises `RuntimeError` if
  `KGSPIN_ADMIN_URL` uses `http://` with a non-loopback hostname.
  No certificate-verification policy is specified beyond Python
  defaults.
- **Loopback `http://` is the documented demo default**
  (`http://127.0.0.1:8750`).

### Acceptable environments

This posture is **only acceptable** when:

1. admin is bound to `127.0.0.1` / `::1`, AND
2. demo + admin run on the same host, OR
3. they run in a trusted private network emulating loopback (e.g.,
   same-pod sidecars in Kubernetes).

### Pre-conditions for production-ish deployment

A separate **admin-side ADR** must land first, defining:
- mTLS between demo/admin, OR
- HMAC-signed `X-Actor` headers, OR
- equivalent out-of-band authentication

This ADR does **not** block Sprint 09 on that work — deferred per
CEO direction (referenced in REQ-007 §Auth posture) — but the debt
is **logged in writing** here so it isn't lost in chat scrollback.

### `python-module` scheme convention

The `CustomPointer(scheme="python-module", value="<module:class>")`
used for `FETCHER` resources is a new cluster convention introduced
in Sprint 09 Task 2. Admin's `_validate_custom_pointer` permits
unknown schemes with a warning today; admin Dev should register a
handler for `python-module` during their Sprint 03/04 consolidation
so the warning stops firing and consumers can locate lander
implementations programmatically.

---

## Consequences

### Positive

- Every demo-fetched document is visible to core's extraction via
  admin's `list(CORPUS_DOCUMENT)` — REQ-005 intent restored.
- Admin's Sprint 04 F1 dashboard has 4 new fetcher records to
  visualize the moment Sprint 09 ships.
- tuner's upcoming REQ-008 can mechanically copy demo's HTTP
  adapter pattern (only the `actor` string changes).
- Sprint 09 surfaced a Protocol under-specification
  (`register_fetcher` body-shape divergence from admin) that
  interface team should address in a future release — documented
  in the dev report.

### Negative

- Landers can no longer run entirely offline — registration step
  requires admin reachable at `$KGSPIN_ADMIN_URL`. A
  `--skip-registry` flag exists internally for tests; operators
  setting it are on their own to get docs into admin later (via
  `kgspin-admin migrate filestore-import`).
- `mock_provider` is kept as technical debt until post-Sprint-09
  Hardening sprint confirms no external consumers.
- The cluster has a soft inconsistency: `kgspin-demo` pins
  `kgspin-interface>=0.5.0,<1.0`; `kgspin-tuner` pins
  `>=0.5.0,<0.7.0` (per CTO note on Day 1). A future cluster-wide
  consolidation can align these.

### Neutral / unchanged

- Pipeline / extractor / bundle-compile paths are untouched by
  Sprint 09 — fetch-and-register only.
- GLiREL upstream breakage (Sprint 04 carry-over) still applies;
  unrelated to Sprint 09.
- kgspin-core bundle schema v2 migration gap (Sprint 07/08
  carry-over) still applies; demo can't fully boot a compiled
  bundle yet. Unrelated to Sprint 09.

---

## References

- REQ-007 (kgspin-admin coord memo → kgspin-demo 2026-04-15)
- REQ-005 (kgspin-core Sprint 17 — disk-walk retirement)
- REQ-004 (kgspin-interface 0.5.0 — DocumentFetcher ABC)
- Sprint 09 plan: `docs/sprints/sprint-09/sprint-plan.md`
- Compatibility Report: `docs/sprints/sprint-09/compatibility-report.md`
- Superseded: [ADR-002](ADR-002-demo-mock-corpus-provider.md)
- Admin HTTP route shapes: `kgspin-admin/tests/http/test_routes_resources.py`
