# kgspin-demo-app

**Reference customer application** — Layer 3 in the KGSpin deployment model. Hosts the compare UI, FastAPI extraction endpoints, landers, MCP server, and benchmarks.

## Layer model

| Layer | Repo | Purpose |
|-------|------|---------|
| 1 — Blueprint | `kgspin-blueprint` | Upstream-curated pipelines, bundles, LLM alias catalog |
| 2 — Config | `kgspin-demo-config` | Per-instance overrides + fetcher registrations + admin config |
| **3 — App (this repo)** | `kgspin-demo-app` | Long-running services + UI + benchmarks |

See `kgspin-blueprint` ADR-003 for the full pattern.

## Who this is for

- **Partners evaluating KGSpin** — clone, set five env vars, run the demo in under ten minutes.
- **Internal demo staff** — drive partner demos with confidence about what each run costs and how long it takes.
- **Lander authors** — add a new data source without reading the UI code.

## Prerequisites

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) for environment + dependency management.
- Sibling checkouts of `kgspin-interface` and `kgspin-core` (wired as editable path deps in `pyproject.toml`).
- A running **admin service** reachable at `$KGSPIN_ADMIN_URL` (see `kgspin-admin`; `scripts/start-demo.sh` starts it for you if absent).
- A sibling `kgspin-demo-config` clone for the Layer 2 config (or set `KGSPIN_DEMO_CONFIG_PATH`).
- At least one LLM API key for the LLM columns in the compare UI (Gemini is the only currently wired backend).

## First run

```bash
# 1. Clone this repo + the config repo as siblings.
cd ~/repos
git clone https://github.com/apireno/kgspin-demo-app.git
git clone https://github.com/apireno/kgspin-demo-config.git

# 2. Prepare Layer 2 config.
cd kgspin-demo-config
cp admin/config.yaml.example admin/config.yaml   # edit if needed

# 3. Install deps + launch.
cd ../kgspin-demo-app
cp .env.example .env      # fill in the required secrets below
uv sync
uv run python -m spacy download en_core_web_sm   # optional, for zero-token pipelines
./scripts/start-demo.sh
```

Open <http://127.0.0.1:8080/intelligence.html> for the Intelligence tab.

## Cross-repo contract

`kgspin-demo-app` reads its structural config from **`kgspin-demo-config`** at runtime.

| Env var | Purpose | Default |
|---|---|---|
| `KGSPIN_DEMO_CONFIG_PATH` | Root of the sibling `kgspin-demo-config` clone. | `../kgspin-demo-config` |
| `KGSPIN_DEMO_CONFIG` | Explicit path to the config.yaml file. | `$KGSPIN_DEMO_CONFIG_PATH/admin/config.yaml` |

On start, `scripts/start-demo.sh` logs the resolved `KGSPIN_DEMO_CONFIG_PATH` and the chosen `KGSPIN_DEMO_CONFIG`. If neither resolves to a real file, the app falls back to first-run bootstrap from `config.template.yaml` in this repo.

## Environment variables (secrets)

| Name | Purpose | Required? |
|---|---|---|
| `KGSPIN_ADMIN_URL` | Admin service base URL. Default `http://127.0.0.1:8750`. | Yes (if using admin flows). |
| `KGSPIN_DEMO_CONFIG_PATH` | Layer 2 config repo root. Default `../kgspin-demo-config`. | Optional. |
| `KGSPIN_DEMO_CONFIG` | Override path to `config.yaml`. | Optional. |
| `EDGAR_IDENTITY` | SEC EDGAR User-Agent identity (an email). | Required for SEC lander. |
| `MARKETAUX_API_KEY` | Marketaux news API key. | Required for Marketaux lander. |
| `NEWSAPI_KEY` | NewsAPI.org API key. | Required for NewsAPI lander. |
| `CLINICAL_TRIALS_API_KEY` | ClinicalTrials.gov v2 API key. | Optional. |
| `GEMINI_API_KEY` | Gemini API key for LLM columns. | Optional (LLM column will error without it). |
| `KGEN_API_KEY` | Shared-secret header for the bundle API. | Optional. |
| `PORT` | Port the compare UI binds to. Default `8080`. | Optional. |
| `KGSPIN_ADMIN_PATH` | Path to kgspin-admin repo root (launcher). Default `../kgspin-admin`. | Optional. |
| `KGSPIN_ADMIN_LOG` | Log file for background admin. Default `/tmp/kgspin-admin.log`. | Optional. |

Copy `.env.example` to `.env` and fill in the required variables before running the demo.

## Project structure

```
src/kgspin_demo_app/
  api/                 FastAPI extraction endpoints (SaaS API surface)
  cli/                 CLI entry points (register_fetchers, ...)
  corpus/              Corpus providers (mock + live)
  landers/             Data landers (sec, clinical, marketaux, yahoo_rss, newsapi)
  services/            Admin registry reader, clinical gold, entity resolution
  utils/               kg_filters + shared helpers
  config.py            AppSettings — reads Layer 2 config.yaml
  domain_fetchers.py   Domain → fetcher ID mapping (ADR-004)
  llm_backend.py       LLM resolver (ADR-002 Phase 4)
  mcp_server.py        MCP server
  registry_http.py     Admin HTTP client
demos/extraction/      Compare UI (FastAPI + static HTML)
benchmarks/            Extraction benchmark harness
scripts/               start-demo.sh, ensure-admin-running.sh
tests/                 unit + integration + manual
```

## Running tests

```bash
uv sync --extra test
uv run pytest
```

## Related

- Upstream blueprint: `kgspin-blueprint`
- Instance config: `kgspin-demo-config`
- Admin registry: `kgspin-admin`
- Core libs: `kgspin-core`, `kgspin-interface`
- Customer reproducibility property: [`docs/reproducibility-by-triple-hash.md`](docs/reproducibility-by-triple-hash.md)
