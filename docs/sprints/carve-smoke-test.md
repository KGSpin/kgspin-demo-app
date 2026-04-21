# Carve smoke test — kgspin-demo-app + kgspin-demo-config

**Date:** 2026-04-21
**Sprint:** W2-B (kgspin-demo split)
**Scope:** End-to-end verification that the Layer 2 / Layer 3 split works from a clean start.

## Setup

Sibling repos staged at:

- `/Users/apireno/repos/kgspin-demo-config/` (Layer 2, initial commit on `main`).
- `/Users/apireno/repos/kgspin-demo-app/` (Layer 3, initial commit on `main`).

The operator already has a running admin service on port 8750 (unchanged from Wave 1). `admin/config.yaml` was seeded as an exact copy of `admin/config.yaml.example` so the app has a resolvable config file.

## Commands

```bash
cd /Users/apireno/repos/kgspin-demo-app
# env vars inherited from .zshrc (KGSPIN_ADMIN_URL, EDGAR_IDENTITY, etc.)
uv sync --extra test
uv run pytest tests/unit tests/integration -q
./scripts/start-demo.sh > /tmp/start-demo-smoke.log 2>&1 &
```

## Test results

### 1. `uv sync --extra test`

Succeeded. Full dependency install including `pytest`, `pytest-httpx`, `kgspin-core[gemini]`, `kgspin-interface`, `fastapi`, `uvicorn`, `spacy`, etc. No errors.

### 2. `uv run pytest`

```
235 passed, 13 warnings in 50.72s   # unit
5 passed, 2 warnings in 0.86s       # integration
```

Total **240 tests, 0 failures**. All pre-existing warnings are upstream (FastAPI `on_event` deprecation, `websockets.legacy` deprecation, the known `kgspin_demo_app.corpus.mock_provider` deprecation). No new warnings introduced by the carve.

### 3. `./scripts/start-demo.sh`

**Startup log (`/tmp/start-demo-smoke.log`):**

```
[start-demo] KGSPIN_DEMO_CONFIG_PATH resolved → /Users/apireno/repos/kgspin-demo-config
[start-demo] KGSPIN_DEMO_CONFIG → /Users/apireno/repos/kgspin-demo-config/admin/config.yaml
[ensure-admin] Admin already running at http://127.0.0.1:8750 — reusing.
[start-demo] Starting compare demo (Ctrl-C stops admin + demo)...
INFO:__main__:Demo debug log: /Users/apireno/repos/kgspin-demo-app/demos/extraction/demo_debug.log
WARNING:pipeline_common:No financial bundles found at /Users/apireno/repos/kgspin-demo-app/.bundles — demo will boot without a default bundle. Extraction endpoints will fail until bundles are compiled (Sprint 02). Error: No bundles found matching financial-v* in /Users/apireno/repos/kgspin-demo-app/.bundles
INFO:__main__:Sprint 10 mode: extraction reads corpus documents from admin's ResourceRegistryClient (KGSPIN_ADMIN_URL, default http://127.0.0.1:8750). Use `uv run kgspin-demo-lander-*` CLIs or the Refresh Local Corpus UI button to populate it.
INFO:__main__:Admin reports 6 registered pipelines: agentic-analyst, agentic-flash, base, discovery-agentic, emergent, fan-out
WARNING:__main__:Skipping model pre-warm — BUNDLE_PATH is None (no compiled bundles found). Server will boot but extraction endpoints will fail until bundles are compiled.
```

### 4. Cross-repo contract verified

- `KGSPIN_DEMO_CONFIG_PATH` auto-resolved to the sibling `../kgspin-demo-config` (no manual export needed).
- `KGSPIN_DEMO_CONFIG` was auto-exported to `$CONFIG_PATH/admin/config.yaml` because that file exists.
- Admin discovery via `ensure-admin-running.sh` worked — detected the already-running admin and reused it.
- Admin's pipeline registry is reachable (6 pipelines reported).

### 5. Health check

Port 8080 listening (verified via `lsof -i :8080`):

```
COMMAND   PID    USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
Python  82883 apireno    7u  IPv4 0xbc37a669097b4ed6      0t0  TCP *:http-alt (LISTEN)
```

HTTP probes:

```
$ curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8080/
HTTP 200    # compare UI served (HTML payload confirmed, <title>KGSpin Demo</title>)
```

The compare UI HTML payload returned successfully — vis-network CDN reference, header styling, etc., all present.

### 6. Expected warnings (not failures)

- `No financial bundles found at .../.bundles` — this is expected for a fresh repo that hasn't compiled bundles yet. The compare UI still boots; extraction endpoints would fail until bundles are compiled (unchanged behavior from the pre-carve kgspin-demo).
- FastAPI `on_event` deprecations — upstream issue, tracked under demo_compare.py lifespan migration.

## Teardown

```bash
pkill -TERM -f "demo_compare.py"
# Port 8080 clear.
```

## Outcome

**Smoke PASSED.** All four acceptance criteria met:

1. ✅ Find kgspin-demo-config at the sibling path.
2. ✅ Start admin (detected already-running, reused).
3. ✅ Start compare UI on 8080.
4. ✅ Health check succeeds (HTTP 200 on `/`).

No new bugs introduced by the carve. The split is operational end-to-end.
