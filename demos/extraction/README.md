# Extraction Demos

Two demos showcasing KGenSkills / KGSpin extraction capabilities.

- `demo_compare.py` — side-by-side comparison of deterministic
  KGenSkills extraction vs. pure Gemini-LLM extraction on SEC 10-K
  filings. Sprint 10 wired its corpus reads through the admin HTTP
  registry; Sprint 11 updated the Refresh-Corpus UI to use the new
  backend-named landers.
- `demo_ticker.py` — older CLI tool that extracts and links KGs
  across multiple data sources for one company. Predates the Sprint
  09 V8 architecture; retained for reference.

---

## Sprint 11 lander set (canonical — ADR-004)

Five CLI lander binaries ship with the demo. Each is named for its
**backend** (not the domain it serves). `domain` is a runtime arg on
`fetch()`, so the same lander can serve multiple domains when
`DOMAIN_FETCHERS` lists it under more than one.

| Backend lander (CLI) | Module | Backend | Domain(s) served |
|---|---|---|---|
| `kgspin-demo-lander-sec` | `kgspin_demo.landers.sec` | SEC EDGAR | `financial` |
| `kgspin-demo-lander-clinical` | `kgspin_demo.landers.clinical` | ClinicalTrials.gov v2 | `clinical` |
| `kgspin-demo-lander-marketaux` | `kgspin_demo.landers.marketaux` | Marketaux `/v1/news/all` | `financial` |
| `kgspin-demo-lander-yahoo-rss` | `kgspin_demo.landers.yahoo_rss` | Yahoo Finance public RSS | `financial` |
| `kgspin-demo-lander-newsapi` | `kgspin_demo.landers.newsapi` | NewsAPI.org `/v2/everything` | `financial` AND `clinical` |

**Removed (Sprint 12 cleanup):**
- `kgspin-demo-lander-yahoo` — removed. Use `kgspin-demo-lander-yahoo-rss`
  (real Yahoo Finance RSS backend).
- `kgspin-demo-lander-health-news` — removed in Sprint 11. Use
  `kgspin-demo-lander-newsapi --domain clinical --query <query>`.

---

## Environment variables

| Variable | Required for | Default | Notes |
|---|---|---|---|
| `EDGAR_IDENTITY` | SEC lander | — | Just an email works, e.g. `alessandro@pireno.com`. Pre-existing convention used across the demo repo. |
| `SEC_USER_AGENT` | SEC lander (fallback) | — | Optional — if `EDGAR_IDENTITY` is set, this is ignored. Kept for legacy operator scripts. |
| `MARKETAUX_API_KEY` | Marketaux lander | — | Free tier: 100 req/day, no credit card. Sign up at https://www.marketaux.com/. |
| `NEWSAPI_KEY` | NewsAPI lander | — | Free tier available. Sign up at https://newsapi.org/register. |
| `CLINICAL_TRIALS_API_KEY` | Clinical lander | — | Optional; ClinicalTrials.gov's v2 API is public. |
| `KGSPIN_CORPUS_ROOT` | all landers | `~/.kgspin/corpus` | Where landed artifacts live on disk. |
| `KGSPIN_ADMIN_URL` | all landers + register-fetchers | `http://127.0.0.1:8750` | Required for the lander CLIs to register with admin. Use `--skip-registry` (internal flag) to bypass in local-only testing. |
| `GEMINI_API_KEY` | `demo_compare.py` Gemini pipeline | — | If unset, KGenSkills still runs; the Gemini column shows an error. |

### Typical `~/.zshrc` block

```bash
export EDGAR_IDENTITY="alessandro@pireno.com"
export MARKETAUX_API_KEY="your-marketaux-key"
export NEWSAPI_KEY="your-newsapi-key"
export KGSPIN_ADMIN_URL="http://127.0.0.1:8750"
export GEMINI_API_KEY="your-gemini-key"
# Optional — defaults to ~/.kgspin/corpus
# export KGSPIN_CORPUS_ROOT="$HOME/.kgspin/corpus"
```

---

## Registering the landers with admin (once per environment)

Before landers can write corpus documents that extraction will see,
register them with admin:

```bash
# Set KGSPIN_ADMIN_URL, then:
uv run kgspin-demo-register-fetchers

# Expected output (to stdout — one line per registered fetcher id):
#   fetcher:sec_edgar
#   fetcher:clinicaltrials_gov
#   fetcher:marketaux
#   fetcher:yahoo_rss
#   fetcher:newsapi
```

Idempotent — re-running produces the same 5 records (admin collapses
duplicate-registration `409 Conflict` responses).

### Retiring the Sprint 09 IDs (operator ack-gated)

Sprint 09 left 3 stale fetcher IDs in admin's registry:
`edgar` (replaced by `sec_edgar`), `newsapi_financial`, and
`newsapi_health`. Sprint 11 ships `--deprecate-old` to flip them to
`status=DEPRECATED`:

```bash
uv run kgspin-demo-register-fetchers --deprecate-old -v
```

The helper logs one structured stderr line per ID. **Against admin
today (confirmed by admin team on 2026-04-19):** all three IDs return
`405 Method Not Allowed` because admin rejects `PATCH/PUT/DELETE` on
resources until admin Sprint 03 ships ADR-002 (Bundle Activation
Policy). Expected output:

```
[DEPRECATE_OLD] id=edgar status=405 note=status mutation pending admin Sprint 03 (ADR-002 Bundle Activation Policy); admin currently rejects PATCH/PUT/DELETE on resources
[DEPRECATE_OLD] id=newsapi_financial status=405 note=status mutation pending admin Sprint 03 (ADR-002 Bundle Activation Policy); admin currently rejects PATCH/PUT/DELETE on resources
[DEPRECATE_OLD] id=newsapi_health status=405 note=status mutation pending admin Sprint 03 (ADR-002 Bundle Activation Policy); admin currently rejects PATCH/PUT/DELETE on resources
```

The helper graceful-degrades per ID — no crash, no partial state. The
stale records stay `ACTIVE` until admin's Sprint 03 lands; at that
point re-running `--deprecate-old` will see `status=200 note=marked
DEPRECATED` instead, with no demo-side rework needed.

---

## 1. KGenSkills vs LLM Comparison (`demo_compare.py`)

Web-based side-by-side comparison of KGenSkills (deterministic
semantic fingerprinting) vs. pure Gemini LLM extraction on the same
SEC 10-K filing.

### Quick Start

```bash
# Install demo dependencies (one-time)
uv pip install -e ".[demo,gliner]"

# Export env vars (see table above) — at minimum EDGAR_IDENTITY +
# GEMINI_API_KEY. For news-backed Ticker Intelligence also export
# MARKETAUX_API_KEY and/or NEWSAPI_KEY.

# One-shot launcher — starts admin (if not already running) + demo:
./scripts/start-demo.sh

# Open http://localhost:8080 in your browser. Ctrl-C stops both.
```

### One-shot launcher: `scripts/start-demo.sh`

Checks whether admin is responding at `$KGSPIN_ADMIN_URL`. If yes,
reuses it. If no, background-starts admin from
`$KGSPIN_ADMIN_PATH` (default: sibling `../kgspin-admin`), waits for
it to come up, then launches the demo. Admin logs go to
`/tmp/kgspin-admin.log`. A single Ctrl-C stops both processes.

First-run only, after admin is up:

```bash
uv run kgspin-demo-register-fetchers    # registers the 5 backend-named landers
```

If you prefer to run admin + demo in separate terminals, skip the
launcher and do it the long way:

```bash
# Terminal 1:
cd ../kgspin-admin && uv run uvicorn kgspin_admin.http.bootstrap:app --host 127.0.0.1 --port 8750
# Terminal 2:
uv run kgspin-demo-register-fetchers    # once
uv run python demos/extraction/demo_compare.py
```

### Refresh-Corpus endpoints (Sprint 11)

The Refresh UI backs onto these endpoints. The SSE streams interleave
per-subprocess progress + a final `step_complete` / `error` event.

| Endpoint | Purpose |
|---|---|
| `GET /api/refresh-corpus/sec/{ticker}` | SEC 10-K / 10-Q / 8-K fetch |
| `GET /api/refresh-corpus/clinical/{nct}` | ClinicalTrials.gov v2 fetch |
| `GET /api/refresh-corpus/marketaux/{ticker}` | Ticker-scoped finance news (Marketaux) |
| `GET /api/refresh-corpus/yahoo-rss/{ticker}` | Ticker-scoped finance news (Yahoo RSS) |
| `GET /api/refresh-corpus/newsapi?query=&domain=&nct=&limit=` | Term-scoped news; pass `domain=financial` or `domain=clinical`. If `domain=clinical` + `nct=NCT<########>` is supplied and `query` is empty, the server derives a query from the trial's `condition` + top-2 interventions. |
| `GET /api/refresh-corpus/news/{domain}` | **Primary VP-Prod button.** Fires every news lander for the domain in parallel. `domain=financial` requires `ticker=`; `domain=clinical` requires `nct=`. |

### Sample Results (JNJ, 20 chunks)

| Metric | KGenSkills | Gemini LLM |
|--------|-----------|------------|
| Entities | ~490 | ~240 |
| Relationships | ~28 | ~31 |
| Tokens | 0 | ~47,000 |
| Time | ~8s | ~95s |

### Architecture

```
Browser (compare.html)
    |  GET /api/compare/{ticker}  (SSE stream)
    v
FastAPI server (demo_compare.py)
    |-- Resolve ticker
    |-- Read registered corpus documents via admin's ResourceRegistryClient
    |-- (Missing corpus → "click Refresh" hint)
    |-- KGenSkills: GLiNER H-Module + L-Module fingerprints (0 tokens)
    |-- Gemini: Per-chunk LLM extraction (tracks tokens)
    |-- Quality analysis: Gemini compares both KGs
    v
Browser renders timeline + dual vis.js graphs + analysis
```

### Files

| File | Description |
|------|-------------|
| `demo_compare.py` | FastAPI server with SSE orchestration |
| `gemini_extractor.py` | Pure-Gemini KG extraction module |
| `static/compare.html` | Single-file web UI (dark theme, vis.js) |

---

## 2. Multi-Corpus Extraction (`demo_ticker.py`) — legacy

CLI tool predating the Sprint 09 V8 architecture. Retained for
reference; the Refresh UI in `demo_compare.py` is the supported path.

### Quick Start

```bash
# Basic usage (fetches all corpora)
uv run python demos/extraction/demo_ticker.py --ticker JNJ

# Re-run with cached data (no network needed)
uv run python demos/extraction/demo_ticker.py --ticker JNJ --skip-fetch
```

---

## Supported Tickers (quick-lookup table)

| Ticker | Company | Domain |
|--------|---------|--------|
| JNJ | Johnson & Johnson | Healthcare |
| PFE | Pfizer Inc. | Healthcare |
| UNH | UnitedHealth Group | Healthcare |
| ABT | Abbott Laboratories | Healthcare |
| AAPL | Apple Inc. | Technology |
| AMD | Advanced Micro Devices | Technology |
| NVDA | NVIDIA Corporation | Technology |
| MSFT | Microsoft Corporation | Technology |
| JPM | JPMorgan Chase & Co. | Financial |
| GS | Goldman Sachs | Financial |
| BRK-B | Berkshire Hathaway | Financial |

Any other ticker also works — unknown ones resolve via EDGAR.

---

## Data Sources & Credits

| Source | Usage | Auth |
|--------|-------|------|
| [SEC EDGAR](https://www.sec.gov/edgar) | 10-K / 10-Q / 8-K filings | `EDGAR_IDENTITY` (just an email works) |
| [ClinicalTrials.gov v2](https://clinicaltrials.gov/) | NCT trial records | Public — optional `CLINICAL_TRIALS_API_KEY` |
| [Marketaux](https://www.marketaux.com/) | Ticker-scoped financial news | `MARKETAUX_API_KEY` (free tier, 100 req/day) |
| [Yahoo Finance RSS](https://feeds.finance.yahoo.com/) | Ticker-scoped headlines | Public RSS — no auth |
| [NewsAPI.org](https://newsapi.org/) | Term-scoped general news | `NEWSAPI_KEY` (free tier available) |

---

## Related docs

- [ADR-004 — Backend-named Landers](../../docs/architecture/decisions/ADR-004-backend-named-landers.md)
- [Sprint 11 plan](../../docs/sprints/sprint-11/sprint-plan.md)
- [Admin handover memo (2026-04-17)](../../docs/handovers/2026-04-17-admin-lander-id-migration.md)
- [PRD-007 — Cross-Domain Clinical Expansion](../../docs/roadmap/prds/PRD-007-cross-domain-clinical-expansion.md)
