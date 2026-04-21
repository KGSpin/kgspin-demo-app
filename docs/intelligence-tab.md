# Intelligence tab

The Intelligence tab is the web UI at `demos/extraction/static/compare.html`,
served by `demos/extraction/demo_compare.py`. It compares extraction across
three side-by-side slots, with Refresh-Corpus buttons to pull fresh
documents through the landers.

## Starting the UI

```bash
./scripts/start-demo.sh
```

The launcher starts admin (if not already responding at `$KGSPIN_ADMIN_URL`)
and then the compare-UI FastAPI server. The UI binds to `http://localhost:8080`
by default; override via the `PORT` environment variable.

If you want to run it manually:

```bash
uv run python demos/extraction/demo_compare.py
```

The server reads the registered corpus documents from admin via
`HttpResourceRegistryClient`; it does not scan the filesystem.

## Layout

- **Ticker input** at the top. Drives SEC + Marketaux + Yahoo fetches.
- **Three extraction slots**, each with its own pipeline picker, graph
  visualization (vis-network), and timeline of extraction steps.
- **Refresh-Corpus buttons** per backend — they trigger the landers and
  stream progress back via SSE.

## Run-button taxonomy (per-slot pipelines)

Each slot picks one of the five pipelines below. Names and colors match
`static/pipelines-help.html`.

| Pipeline | Capability | Tokens | Backend | Cost | Expected latency | Use when |
|---|---|---|---|---|---|---|
| **Rapid Discovery** (`discovery_rapid`) | Discovery | 0 | KGenSkills (spaCy NER + greedy harvester) | Free | ~1–3s / chunk | You want the fastest zero-token "who is in this document" answer. |
| **Deep Discovery** (`discovery_deep`) | Discovery | 0 | KGenSkills (GLiNER + neural relation heads) | Free | ~5–10s / chunk | High-density actor inventory with validated links — the canonical zero-token baseline. Default for slot 2. |
| **Signal Fan-out** (`fan_out`) | Fan-out | 0 | KGenSkills (relation-first) | Free | ~5–10s / chunk | You want events and relationships first, then the actors that participate. Default for slot 1. |
| **Agentic Flash** (`agentic_flash`) | Agentic | LLM | Gemini (single prompt) | Per-call LLM spend | ~30–60s / document | Baseline "what does a raw LLM pull out" comparison. Uses generic NER types. |
| **Agentic Analyst** (`agentic_analyst`) | Agentic | LLM | Modular (schema-aware chunked LLM with cross-chunk carryover) | Highest LLM spend | ~1–2 min / document | Highest-fidelity LLM extraction. Uses the bundle's rich entity types and maintains cross-chunk context. Default for slot 3. |

**Partner-demo note:** the zero-token pipelines (Rapid Discovery, Deep
Discovery, Signal Fan-out) cost nothing per run and always run locally. The
Agentic pipelines charge against your LLM provider per run. The UI color-codes
them (green = zero-token, red/orange = LLM) — never run a second Agentic
pass on a 10-K chunk while demoing if you are not prepared to pay for it.

Latencies above are orders-of-magnitude for a typical SEC 10-K chunk on a
developer laptop; exact numbers depend on hardware, chunk count, and the
bundle.

For the full pipeline descriptions (strategy names, YAML sources, backend
notes) open `http://localhost:8080/static/pipelines-help.html` in your
browser; the UI links to it from each slot.

## Refresh-Corpus endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/refresh-corpus/sec/{ticker}` | SEC EDGAR 10-K / 10-Q / 8-K fetch. |
| `GET /api/refresh-corpus/clinical/{nct}` | ClinicalTrials.gov v2 trial fetch. |
| `GET /api/refresh-corpus/marketaux/{ticker}` | Ticker-scoped Marketaux news. |
| `GET /api/refresh-corpus/yahoo-rss/{ticker}` | Ticker-scoped Yahoo RSS news. |
| `GET /api/refresh-corpus/newsapi?query=&domain=&nct=&limit=` | Term-scoped NewsAPI news. If `domain=clinical` and `nct=NCT<########>` is given but `query` is empty, the server derives a query from the trial's metadata (see `services/clinical_query.py`). |
| `GET /api/refresh-corpus/news/{domain}` | Fan-out: fires every news lander for a domain in parallel. `domain=financial` requires `?ticker=`; `domain=clinical` requires `?nct=`. |

Each endpoint returns a Server-Sent Events stream — interleaved
per-subprocess progress plus a final `step_complete` or `error` event.

## Admin wiring

The UI does not touch the filesystem directly. All corpus reads go through
`HttpResourceRegistryClient` (from `kgspin-interface`) pointed at
`$KGSPIN_ADMIN_URL`. If admin is down at page load time, the UI shows a
"click Refresh" hint instead of blank graphs.

## Related

- [`docs/landers/README.md`](landers/README.md) — what each Refresh
  button fetches.
- [`docs/running-a-demo.md`](running-a-demo.md) — full walkthrough.
- `demos/extraction/static/pipelines-help.html` — canonical in-UI pipeline
  descriptions.
