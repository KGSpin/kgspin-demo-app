# Lander catalog

A "lander" is a small CLI + Python module under `src/kgspin_demo/landers/`
that fetches documents from **one specific backend**, writes the raw bytes
to `$KGSPIN_CORPUS_ROOT`, computes a SHA-256, and registers the result with
the admin service via `kgspin-interface`'s `ResourceRegistryClient`.

Landers are **backend-named**, not domain-named (see
[ADR-004](../architecture/decisions/ADR-004-backend-named-landers.md)). The
same lander can serve multiple domains by virtue of the domain→lander
registry in `src/kgspin_demo/domain_fetchers.py`. The contract every lander
implements lives in `kgspin-interface` — see
[ADR-003](../architecture/decisions/ADR-003-fetcher-abc-and-admin-registry.md).

Each section below is the integration contract for that lander. A partner
should be able to implement a new lander from one of these sections plus
the source of one existing lander, without reverse-engineering the
`kgspin-interface` Python types.

---

## `sec_edgar` — SEC EDGAR filings

- **Backend:** `https://www.sec.gov/cgi-bin/browse-edgar` + per-filing URLs.
- **Domain(s):** `financial`.
- **Credentials:** `EDGAR_IDENTITY` (just an email works; SEC requires consumers
  to identify themselves). `SEC_USER_AGENT` is a legacy alias — ignored when
  `EDGAR_IDENTITY` is set.
- **CLI:** `kgspin-demo-lander-sec --cik <TICKER_OR_CIK> --type <10-K|10-Q|8-K>`.
- **Admin:** writes one `ResourceKind.CORPUS_DOCUMENT` per filing.
- **On-disk output:** raw HTML at
  `$KGSPIN_CORPUS_ROOT/sec_edgar/<cik>/<accession>.html`.
- **`source_extras` keys** (what admin stores alongside the document):

  | Key | Type | Always present? | Example |
  |---|---|---|---|
  | `lander_name` | string | yes | `sec_edgar` |
  | `lander_version` | string | yes | `1.0.0` |
  | `fetch_timestamp_utc` | ISO-8601 string | yes | `2026-04-20T14:32:10.421Z` |
  | `http_status` | int | yes | `200` |
  | `cik` | string | yes | `320193` (or ticker alias) |
  | `accession` | string | yes | `0000320193-24-000123` |
  | `filing_type` | string | yes | `10-K` |
  | `sec_updated` | string | when EDGAR returns it | `2024-11-01T12:00:00Z` |

  (First-class `CorpusDocumentMetadata` fields — `source_url`, `etag`,
  `bytes_written` — are populated separately and are not nested under
  `source_extras`.)

---

## `clinicaltrials_gov` — ClinicalTrials.gov v2

- **Backend:** `https://clinicaltrials.gov/api/v2/studies/<NCT>`.
- **Domain(s):** `clinical`.
- **Credentials:** none required. `CLINICAL_TRIALS_API_KEY` is optional; useful
  only for higher rate limits.
- **CLI:** `kgspin-demo-lander-clinical --nct NCT<########>`.
- **Admin:** writes one `ResourceKind.CORPUS_DOCUMENT` per trial.
- **On-disk output:** raw JSON at
  `$KGSPIN_CORPUS_ROOT/clinicaltrials_gov/<nct>.json`.
- **`source_extras` keys:**

  | Key | Type | Always present? | Example |
  |---|---|---|---|
  | `lander_name` | string | yes | `clinicaltrials_gov` |
  | `lander_version` | string | yes | `1.0.0` |
  | `fetch_timestamp_utc` | ISO-8601 string | yes | `2026-04-20T14:32:10.421Z` |
  | `http_status` | int | yes | `200` |
  | `nct_id` | string | yes | `NCT12345678` |

  The clinical trial's `condition` and `interventions` are not emitted by
  the lander directly — they are read by `services/clinical_query.py` from
  the trial JSON when the Intelligence tab derives a NewsAPI query.

---

## `marketaux` — Marketaux finance news

- **Backend:** `https://api.marketaux.com/v1/news/all`.
- **Domain(s):** `financial`.
- **Credentials:** `MARKETAUX_API_KEY`. Free tier: 100 requests/day.
  Register at https://www.marketaux.com/.
- **CLI:** `kgspin-demo-lander-marketaux --ticker <TICKER> [--limit N]`.
- **Admin:** writes one `ResourceKind.CORPUS_DOCUMENT` per article returned.
- **On-disk output:** one HTML file per article at
  `$KGSPIN_CORPUS_ROOT/marketaux/<article_id>.html`.
- **`source_extras` keys:**

  | Key | Type | Always present? | Example |
  |---|---|---|---|
  | `lander_name` | string | yes | `marketaux` |
  | `lander_version` | string | yes | `1.0.0` |
  | `fetch_timestamp_utc` | ISO-8601 string | yes | — |
  | `http_status` | int | yes (200) | `200` |
  | `article_id` | string | yes | Marketaux article id |
  | `ticker` | string or `null` | yes | `AAPL` |
  | `source_name` | string | yes | `CNBC` |
  | `published_at` | string | yes (may be empty) | `2026-04-19T23:12:00Z` |
  | `title` | string | yes (may be empty) | — |
  | `keywords` | comma-joined string | yes (may be empty) | `earnings,guidance` |
  | `related_tickers` | comma-joined string | yes (may be empty) | `AAPL,MSFT` |

---

## `yahoo_rss` — Yahoo Finance RSS

- **Backend:** `https://feeds.finance.yahoo.com/rss/2.0/headline?s=<TICKER>`.
- **Domain(s):** `financial`.
- **Credentials:** none — public RSS.
- **CLI:** `kgspin-demo-lander-yahoo-rss --ticker <TICKER>`.
- **Admin:** writes one `ResourceKind.CORPUS_DOCUMENT` per headline.
- **On-disk output:** one HTML file per article at
  `$KGSPIN_CORPUS_ROOT/yahoo_rss/<article_id>.html`.
- **`source_extras` keys:**

  | Key | Type | Always present? | Example |
  |---|---|---|---|
  | `lander_name` | string | yes | `yahoo_rss` |
  | `lander_version` | string | yes | `1.0.0` |
  | `fetch_timestamp_utc` | ISO-8601 string | yes | — |
  | `http_status` | int | yes (200) | `200` |
  | `article_id` | string | yes | derived from feed entry |
  | `ticker` | string or `null` | yes | `AAPL` |
  | `source_name` | string | yes | `Yahoo Finance` |
  | `published_at` | string | yes (may be empty) | `2026-04-19T23:12:00Z` |
  | `title` | string | yes (may be empty) | — |

---

## `newsapi` — NewsAPI.org term-scoped news

- **Backend:** `https://newsapi.org/v2/everything?q=<query>`.
- **Domain(s):** `financial` **and** `clinical`. Passed on the CLI via
  `--domain`; the domain is persisted on the document record so downstream
  lookups can filter.
- **Credentials:** `NEWSAPI_KEY`. Free tier available.
- **CLI:** `kgspin-demo-lander-newsapi --domain <financial|clinical> --query <text> [--limit N]`.
- **Admin:** writes one `ResourceKind.CORPUS_DOCUMENT` per article.
- **On-disk output:** one HTML file per article at
  `$KGSPIN_CORPUS_ROOT/newsapi/<article_id>.html`.
- **`source_extras` keys:**

  | Key | Type | Always present? | Example |
  |---|---|---|---|
  | `lander_name` | string | yes | `newsapi` |
  | `lander_version` | string | yes | `1.0.0` |
  | `fetch_timestamp_utc` | ISO-8601 string | yes | — |
  | `http_status` | int | yes (200) | `200` |
  | `article_id` | string | yes | derived from NewsAPI response |
  | `query` | string or `null` | yes | `pembrolizumab` |
  | `source_name` | string | yes (may be empty) | `Reuters` |
  | `author` | string | yes (may be empty) | — |
  | `published_at` | string | yes (may be empty) | — |
  | `title` | string | yes (may be empty) | — |

### Clinical-mode query derivation

When the Intelligence tab calls "Refresh All Clinical News" for a given
NCT trial, the query is not typed by the operator. Instead,
`services/clinical_query.py` reads the trial's most-recent registered
`corpus_document` from admin, extracts the trial's `condition` plus its
top-two intervention names, sanitizes them to `[A-Za-z0-9 _-]{0,100}`, and
passes the resulting string to the NewsAPI lander as `--query`. If the
trial is not registered, the operator must type a query by hand.

---

## Private helpers

These modules are not CLIs — they back the public landers.

| Module | Purpose |
|---|---|
| `_newsapi_client.py` | HTTP client + response shaping for NewsAPI. |
| `_marketaux_client.py` | HTTP client + response shaping for Marketaux. |
| `_yahoo_rss_client.py` | RSS parsing via `feedparser`. |
| `metadata.py` | `build_source_extras()` — the canonical `source_extras` builder; `iso_utc_now()`. |
| `_shared.py` | Corpus-root resolution, `sha256_file()`, size-capped downloads. |
| `_path_safety.py` | Filesystem-path safety checks (prevents path-traversal in article IDs). |
| `_net_safety.py` | Network safety guards (URL allow-listing, size caps). |

## Clinical unstructured-text gap

The scope spec for this repo originally pointed at PubMed / PMC as the
unstructured-text backend for clinical documents. **Today that lander does
not exist** — the clinical path as shipped uses NewsAPI (clinical-mode)
with a query derived from the trial's condition + interventions. See
`docs/backlog/` for the open doc gap on this; fixing it is out of scope
for a doc-only sprint.

## Related

- [ADR-003 — Fetcher ABC + admin registry](../architecture/decisions/ADR-003-fetcher-abc-and-admin-registry.md)
- [ADR-004 — Backend-named landers](../architecture/decisions/ADR-004-backend-named-landers.md)
- [`docs/domain-fetchers.md`](../domain-fetchers.md) — `DOMAIN_FETCHERS` registry.
- [`docs/running-a-demo.md`](../running-a-demo.md) — end-to-end walkthrough
  that exercises each lander.
