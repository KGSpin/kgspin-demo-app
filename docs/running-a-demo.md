# Running a demo end-to-end

Two tracks: **financial** (SEC + ticker-scoped news) and **clinical**
(ClinicalTrials.gov + trial-derived news). The setup is shared; the
lander commands differ. Every command below is copy-pasteable.

## One-time setup

```bash
git clone https://github.com/apireno/kgspin-demo.git
cd kgspin-demo

cp .env.example .env
# Edit .env — at minimum fill in:
#   EDGAR_IDENTITY="you@example.com"
#   MARKETAUX_API_KEY="..."       (or NEWSAPI_KEY, depending on the track)
#   GEMINI_API_KEY="..."          (optional; enables the LLM columns in compare UI)
#   NEWSAPI_KEY="..."             (required for the clinical track)

uv sync
uv run python -m spacy download en_core_web_sm
```

### Start admin + the compare UI

The one-shot launcher starts admin in the background if it isn't already
responding, then starts the compare-UI server:

```bash
./scripts/start-demo.sh
```

If you prefer two terminals, you can run admin yourself and skip the
launcher:

```bash
# Terminal 1: admin
cd ../kgspin-admin
uv run uvicorn kgspin_admin.http.bootstrap:app --host 127.0.0.1 --port 8750

# Terminal 2: compare UI
cd kgspin-demo
uv run python demos/extraction/demo_compare.py
```

### Register the landers with admin (once per admin instance)

```bash
uv run kgspin-demo-register-fetchers
# Expected stdout:
#   fetcher:sec_edgar
#   fetcher:clinicaltrials_gov
#   fetcher:marketaux
#   fetcher:yahoo_rss
#   fetcher:newsapi
```

The command is idempotent — re-running produces the same 5 records.
See [`docs/landers/README.md`](landers/README.md) for what each fetcher
does.

---

## Track A — financial (JNJ)

### 1. Fetch a 10-K filing

```bash
uv run kgspin-demo-lander-sec --cik JNJ --type 10-K
```

This writes raw HTML under `$KGSPIN_CORPUS_ROOT/sec_edgar/...` and
registers a `CORPUS_DOCUMENT` with admin. Confirm it is registered:

```bash
curl -s "${KGSPIN_ADMIN_URL:-http://127.0.0.1:8750}/resources?kind=corpus_document" \
  | python -m json.tool | head -40
```

### 2. Pull a few ticker-scoped news articles

Pick one news backend (or run both):

```bash
uv run kgspin-demo-lander-marketaux --ticker JNJ --limit 5
uv run kgspin-demo-lander-yahoo-rss --ticker JNJ
```

### 3. Open the Intelligence tab

Open <http://localhost:8080> in your browser. Enter ticker `JNJ` and
click one of the per-slot run buttons. See
[`docs/intelligence-tab.md`](intelligence-tab.md) for what each run
button does and what it costs.

### 4. Re-run or refresh

The UI's Refresh-Corpus buttons call the landers for you — you can
skip step 2 on subsequent demos.

---

## Track B — clinical (NCT trial)

### 1. Fetch a trial record

```bash
uv run kgspin-demo-lander-clinical --nct NCT04368728
```

### 2. Fetch related news

You can let the UI derive the query from the trial's condition +
interventions (recommended — see `services/clinical_query.py`):

- In the Intelligence tab, enter the NCT id and click **Refresh All
  Clinical News**. The UI reads the trial metadata from admin, derives
  a query, and calls the NewsAPI lander with `--domain clinical`.

Or run the lander manually with your own query:

```bash
uv run kgspin-demo-lander-newsapi --domain clinical --query "covid-19 vaccine" --limit 10
```

### 3. Open the Intelligence tab

Same as the financial track — pick a pipeline per slot and run.

---

## Where artifacts live after a run

- **Raw documents:** `$KGSPIN_CORPUS_ROOT/<backend>/...`
  (default `~/.kgspin/corpus`).
- **Document records:** admin's resource registry, kind
  `CORPUS_DOCUMENT`.
- **Extraction output:** in-memory, rendered straight to the UI. Not
  persisted on disk in the current demo.

## Troubleshooting

- **Admin not responding** — the launcher prints a clear error and the
  last 20 lines of the admin log (`/tmp/kgspin-admin.log` by default).
  Check `KGSPIN_ADMIN_URL` and `KGSPIN_ADMIN_PATH`.
- **SEC 403** — your `EDGAR_IDENTITY` is missing or malformed. Use an
  email address.
- **No LLM column in compare UI** — `GEMINI_API_KEY` (or
  `GOOGLE_GENAI_API_KEY`) is unset. The KGSpin columns still run.
- **"Fetcher not registered"** — run `uv run kgspin-demo-register-fetchers`
  before issuing UI Refresh-Corpus requests.
- **A documented command fails** — file a backlog ticket at
  `docs/backlog/BUG-XXX-*.md` or `DOC-GAP-XXX-*.md`. The doc stays
  honest; the source fix happens in a separate sprint.

## Benchmarks (forthcoming)

The extraction benchmark harness is Track 3 of the Alpha MVP plan and
will live under `benchmarks/` once it lands. See
[`docs/initiatives/alpha-mvp-20260419/overarching-plan.md`](initiatives/alpha-mvp-20260419/overarching-plan.md)
for context. The directory does not yet exist.

## Related

- [`docs/landers/README.md`](landers/README.md) — per-lander contract.
- [`docs/intelligence-tab.md`](intelligence-tab.md) — run-button taxonomy,
  latency, cost.
- [`docs/domain-fetchers.md`](domain-fetchers.md) — how domains map to
  landers.
