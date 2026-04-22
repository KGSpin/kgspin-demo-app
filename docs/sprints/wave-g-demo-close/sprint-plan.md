# Wave G — Demo Close — Sprint Plan

**From:** Dev team (kgspin-demo-app)
**To:** CTO
**Date:** 2026-04-22
**Branch:** `wave-g-demo-close` (cut from `main` @ `d24b1bd`)
**Bundled PRDs:** PRD-004 v4 (#9–12) + PRD-055 (#1–5)
**Cross-repo dependency:** `kgspin-core` `wave-g-topological-health` branch (parallel sprint, contract pinned in `/tmp/cto/wave-g-20260422/common-preamble.md`)

---

## TL;DR

One sprint, one branch, six deliverables (A–F). The shape is:

- **Backend** (Python): scenario YAML loader (A), `/api/multihop/run` parallel
  fan-out endpoint (B), Gemini Flash judge (C), Topological Health adapter +
  micro-graph builder for LLM answers (E.python), RAGSearch citation strings (F).
- **Frontend** (JS + HTML): Run-Multi-Hop button + scenario picker + 3-up
  blinded answer grid + judge verdict panel + Reveal-Pipelines toggle (D),
  health badge + drilldown drawer (E.frontend, F.frontend).
- **Tests**: pytest unit tests for judge parser + scenario loader + micro-graph
  builder + Topological Health contract self-test; one Playwright smoke that
  asserts 3-answer + judge render.

The single substantive risk is the cross-repo dependency on
`kgspin-core::compute_health`. We pin the branch from day 1, write our
integration against the contract from the preamble, and ship a self-test that
fails loudly if the dataclass shape ever drifts. If the core PR slips past
our integration phase we surface "—" badges and proceed; the demo narrative
degrades but does not crash.

Targeting **5 commits** on `wave-g-demo-close`, one per logical area
(scenarios+loader, endpoint+judge, health-adapter+micrograph, UI, tests).

---

## 1. Cross-repo coordination

### 1a. The pin

`pyproject.toml` currently sources `kgspin-core` as an editable path dep
(`../kgspin-core`). The Wave G `kgspin-core` team is already on branch
`wave-g-topological-health` (verified — branch exists, no module yet).
Because the path source resolves to whatever HEAD that worktree is on, we
inherit their progress automatically while developing locally.

For CI / container builds we will pin via a brief `[tool.uv.sources]`
override to the branch ref **only if** the editable path dep proves
insufficient; otherwise we keep the path dep and document the branch
expectation in the dev-report. Switch to `main` happens in the post-Wave-G
cleanup once the core PR lands.

### 1b. The contract self-test

`tests/unit/test_topological_health_contract.py`:

- Imports `kgspin_core.graph_topology.health.{TopologicalHealth, compute_health}`.
- Asserts the dataclass fields exist with the exact types from the preamble.
- Asserts `compute_health(empty_kg)` returns `score == -1` with a non-None
  `insufficient_reason`.
- Asserts `compute_health(small_dense_kg)` returns a `score` in `[0, 100]`.

This is the single artifact that proves the cross-repo handshake works.
If `kgspin-core` lands the module before we integrate, the test passes
immediately. If they slip, our integration code path-imports a thin shim
in `src/kgspin_demo_app/services/topology_health.py` that returns a
sentinel `TopologicalHealth(score=-1, insufficient_reason="kgspin-core module not yet available", ...)` so the rest of the UI degrades gracefully.

### 1c. What we are NOT doing in core

Per the assignment, we do not write `compute_health` ourselves. The
parallel `kgspin-core` team owns `graph_topology/health.py` + ADR-012 +
the score formula. Our scope ends at "call it and render the result."

---

## 2. Deliverables

### A. Multi-hop scenario pack (PRD-004 #9)

**File:** `demos/extraction/multihop_scenarios.yaml` (new)

**Shape** — one top-level `scenarios:` list, four entries (2 financial, 2 clinical):

```yaml
scenarios:
  - scenario_id: jnj_acquisitions_litigation
    domain: financial
    question: "Which of J&J's acquired companies since 2020 are now facing active litigation, and what product lines does that touch?"
    expected_hops: 3
    talking_track: |
      Three-hop scenario: J&J → acquired companies (2020+) → litigation events → product mapping.
      KG pipelines should chain the acquisition + litigation edges; LLM pipelines must hold all
      four facts in working memory simultaneously. Watch the topology health gap.
  - scenario_id: <financial #2 — pick from JNJ corpus, 2-hop>
    ...
  - scenario_id: stelara_adverse_events_cohort
    domain: clinical
    question: "Which adverse events reported in Stelara trials also appear in other J&J immunology trials in the same patient age bracket?"
    expected_hops: 3
    talking_track: |
      Three-hop scenario: Stelara → adverse events → other J&J immunology trials → age-bracket filter.
  - scenario_id: <clinical #2 — pick from clinical corpus, 2-hop>
    ...
```

**Loader:** `demos/extraction/scenarios.py` (new, ~40 LOC)
- `load_scenarios() -> list[Scenario]` — returns frozen dataclasses
- `get_scenario(scenario_id: str) -> Scenario` — used by the endpoint
- Caches the YAML at module import (file-system read once)

**Tests:** `tests/unit/test_scenarios.py`
- YAML parses; 4 scenarios present; each has all required fields; `expected_hops >= 2`.

### B. Parallel-run endpoint `/api/multihop/run` (PRD-004 #10)

**Location:** `demos/extraction/demo_compare.py` — new route directly below
`@app.post("/api/compare-qa/{doc_id}")` (line 2236) since the helpers
(`_build_kg_context_string`, `_SLOT_PIPELINE_TO_CACHE_KEY`, `_kg_cache`,
`resolve_llm_backend`) are colocated.

**Request body:**
```json
{
  "doc_id": "JNJ",
  "scenario_id": "jnj_acquisitions_litigation",
  "slot_pipelines": ["fan_out", "agentic_flash", "agentic_analyst"]
}
```

**Response:**
```json
{
  "scenario": {"id": "...", "question": "...", "domain": "...", "talking_track": "..."},
  "answers": [
    {"pipeline": "fan_out", "answer_text": "...", "latency_ms": 1230, "cost_usd": 0.0014, "tokens_used": 580, "topology_health": {<TopologicalHealth dict>}},
    ...
  ],
  "judge": {"ranking": ["A", "C", "B"], "rationales": {"A": "...", "B": "...", "C": "..."}}
}
```

**Implementation sketch:**

1. Validate `doc_id`, look up the scenario by id, validate `len(slot_pipelines) == 3`.
2. Look up cached KG per pipeline via `_kg_cache[doc_id][cache_key]` (cache key fix from `ee8be81` already keys per pipeline_id).
3. Per-pipeline answer task — wraps the `_run_compare_qa`-style prompt + Gemini call, instrumented with `time.perf_counter()` for latency and `tokens * GEMINI_MODEL_PRICING[model]` for cost. Each task returns `(pipeline, answer_text, latency_ms, cost_usd, tokens_used)`.
4. **Parallelism:** `await asyncio.gather(*[asyncio.to_thread(_run_one, p) for p in slot_pipelines])`. The existing `compare-qa` flow uses `time.sleep(1.5)` between calls — this endpoint deliberately does not, because the demo narrative depends on parallel execution. We add a brief comment explaining why, and a per-call timeout (60s) so a hung pipeline does not block the others.
5. For each pipeline result, compute `topology_health = compute_health(kg)` from the cached KG (KG pipelines) or from a micro-graph built from the answer text (LLM pipelines — see deliverable E). The answer's `topology_health` field is the **pipeline's KG score** for KG pipelines and the **micro-graph score parsed from the answer** for LLM pipelines, per PRD-055 #3 ("LLM-side score").
6. Assemble `answer_text`s as `[A, B, C]` in `slot_pipelines` order, hand to the judge (deliverable C). Judge call is awaited synchronously after the gather completes — keeps the response shape simple, viewer-side sequencing handled in the UI.
7. Return JSON.

**Cache miss handling:** if a pipeline has no cached KG, return that
answer's slot with `answer_text: null`, `topology_health: null`,
`error: "no cached extraction for {label}"`. The judge call is skipped if
fewer than 2 answers are valid; UI shows a "rerun the slot panels first"
hint.

**Tests:** integration test in `tests/integration/test_multihop_endpoint.py`
using the existing FastAPI shim pattern from `test_smoke_e2e.py`. Mocks
the Gemini backend (existing `pytest-httpx` setup) and verifies parallel
dispatch (latency assertion: total wall-time ≤ slowest single call + judge
call).

### C. LLM-as-judge (PRD-004 #11)

**File:** `demos/extraction/judge.py` (new, ~80 LOC)

**Public surface:**
```python
@dataclass(frozen=True)
class JudgeVerdict:
    ranking: list[str]              # e.g. ["A", "C", "B"] — best to worst
    rationales: dict[str, str]       # {"A": "one-sentence reason", ...}

def rank_answers(question: str, answers: list[str]) -> JudgeVerdict:
    ...
```

**Prompt template** (literal strings; no f-string injection of pipeline names):

```
You are an evaluator. Below is a question followed by three candidate
answers labeled A, B, C. Rank them from best to worst using this rubric:
- Most specific (cites concrete entities, numbers, dates)
- Most complete (addresses all parts of the question)
- Least speculative (avoids hedging, "likely", "possibly")

Question: {question}

Answer A: {answers[0]}
Answer B: {answers[1]}
Answer C: {answers[2]}

Respond with JSON only:
{
  "ranking": ["X", "Y", "Z"],
  "rationales": {"A": "...", "B": "...", "C": "..."}
}
```

**Backend call:** reuse `resolve_llm_backend(llm_alias="gemini-flash-2.5", flow="judge")`. Set `temperature=0` (the existing backend already supports this knob; if not exposed in `resolve_llm_backend`'s signature, add it as a keyword for the judge code path only — minimal change).

**Parsing:** Gemini's JSON-mode (`response_mime_type: application/json`) is already used in `kgspin-core/agents/backends/gemini.py`. We pass through the same setting. Parser is `json.loads()` with one validation pass: `ranking` must be a permutation of `["A","B","C"]`; `rationales` must have all three keys. On parse failure, retry once with `temperature=0`, then raise `JudgeParseError` (caller in `/api/multihop/run` swallows and returns `judge: null` with an `error` field).

**Anti-leak:** the judge prompt **never** receives pipeline names, KG context, topology scores, or citations. The endpoint code path in B explicitly extracts `[a.answer_text for a in answers]` before the call — pipeline labels stay in the response payload but never enter the judge prompt.

**Tests:** `tests/unit/test_judge.py`
- JSON parse: well-formed response → JudgeVerdict with correct shape.
- JSON parse: ranking with duplicates → ValidationError.
- JSON parse: missing rationale key → ValidationError.
- Determinism: same `(question, answers)` triple → identical ranking across 5 mocked calls (mock returns the same response — proves we do not perturb the prompt). The "real" 5-call temp=0 stability test is documented as a manual-smoke item in the dev-report; we do not burn 5 live Gemini calls per CI run.

### D. Viewer-first comparison UI (PRD-004 #12)

**Header chunk** — compare view header gets a new sub-row:

```html
<div class="multihop-bar" data-region="multihop-controls">
  <select data-action="pick-multihop-scenario" id="multihop-scenario-picker">
    <!-- options injected from /api/multihop/scenarios on init -->
  </select>
  <button data-action="run-multihop">Run Multi-Hop</button>
  <button data-action="reveal-pipelines" hidden>Reveal Pipelines</button>
</div>
```

**Answer grid** — appears below the existing slot panels when a multihop run completes:

```html
<section class="multihop-answers" data-region="multihop-answers" hidden>
  <article class="multihop-answer" data-answer-slot="A">
    <header><span class="answer-label">Answer A</span><span class="pipeline-reveal" hidden></span></header>
    <div class="answer-text"></div>
    <footer>
      <span class="answer-latency">1.2s</span>
      <span class="answer-cost">$0.0014</span>
      <button data-action="open-health-drawer" data-source="multihop-answer" data-answer-slot="A">
        <span class="health-badge"></span>
      </button>
    </footer>
  </article>
  <!-- B, C identical -->
</section>
<aside class="judge-verdict" hidden>...</aside>
```

**Sequencing** — the response is a single JSON payload, but UI staggers render via a tiny state machine in `compare-runner.js`:

1. POST `/api/multihop/run` → on success, render the 3 answer panels (labels = A/B/C, pipelines hidden) and topology badges.
2. After a 250ms beat (gives the viewer a moment to read), render the judge verdict panel below.
3. Reveal Pipelines button becomes visible. Click → un-hide the `.pipeline-reveal` spans showing the actual pipeline label per panel.

**Pipeline-label blinding** — the response from B includes `pipeline` per answer; we **render that into a `data-pipeline` attribute on each panel** but the `.pipeline-reveal` span stays `hidden` until the Reveal click. This ensures Inspect-Element doesn't ruin the pitch unless the viewer goes looking.

**New `data-action` registrations** (per Wave-E delegation pattern, registered in `compare-runner.js`):
- `pick-multihop-scenario` (change handler, `data-change-action`)
- `run-multihop` (click)
- `reveal-pipelines` (click)
- `open-health-drawer` (click) — used by both slot panels and answer panels

**`/api/multihop/scenarios`** — small GET endpoint that returns the YAML
contents (id, question, domain, talking_track) so the picker dropdown is
data-driven rather than hardcoded in HTML. Lives next to the run endpoint.

**JS module placement** — all of the above goes into `compare-runner.js`
(2598 LOC pre-Wave-G, will grow ~250 LOC). No new JS module — the
multi-hop flow is part of the compare experience, splitting it would
cross-cut the `data-action` registry.

### E. Topological Health integration (PRD-055 #1–3)

**E.1 — Backend adapter** (`src/kgspin_demo_app/services/topology_health.py`, new):

```python
def health_for_kg(kg_dict: dict) -> dict:
    """Score a kgspin-shaped dict KG. Returns serialized TopologicalHealth or sentinel."""
    try:
        from kgspin_core.graph_topology.health import compute_health
        from kgspin_core.models.lineage import KnowledgeGraph  # adapter
    except ImportError:
        return _sentinel("kgspin-core graph_topology not yet available")
    kg = _dict_to_kg(kg_dict)
    th = compute_health(kg)
    return asdict(th)
```

The `_dict_to_kg` adapter handles the reconnaissance finding that the
demo passes around dict-shaped KGs (`{entities: [...], relationships:
[...]}`) while `compute_health` takes the typed `KnowledgeGraph`. We
build the typed object from the dict on the demo side. If the typed
class is not exported by core, we wrap with a duck-typed shim object
matching the attributes core's `compute_health` reads.

**E.2 — Slot-panel badge call site:** the existing slot-render path
(in `slots.js`, where the KG is handed to the renderer) makes one fetch
to a new GET endpoint `/api/topology-health/{doc_id}/{pipeline}` which
calls `health_for_kg(_kg_cache[doc_id][cache_key])`. The endpoint is
cheap (pure function over an in-memory dict) and stateless; no caching
needed beyond `_kg_cache`.

**E.3 — Multi-hop answer badge call site:** the `/api/multihop/run`
endpoint calls `health_for_kg` inline per answer (no extra round-trip).

**E.4 — Micro-graph builder for LLM answers** — `src/kgspin_demo_app/services/micrograph.py` (new, ~80 LOC):

```python
def build_micrograph_from_answer(answer_text: str) -> dict:
    """Deterministic NER + verb-extraction → kg-shaped dict.
    Intentionally lightweight; not the full kgspin pipeline (avoids
    circular logic — we'd be grading the LLM with our own extractor)."""
```

**Approach**: 
- NER via spaCy `en_core_web_sm` (already a transitive dep through kgspin-core's GLiNER setup; if not, we add it as a `[demo-app]` extra). Entities = NER spans.
- Relations via verb-extraction: noun-phrase → verb → noun-phrase using spaCy's dependency parse. One pass, no LLM.
- Output shape matches what `_dict_to_kg` consumes.
- This will reliably produce small, sparse graphs from LLM answers — exactly the asymmetry PRD-055 wants to surface.

**Tests:** `tests/unit/test_micrograph.py`
- Empty answer → empty graph (0 nodes, 0 edges).
- One-sentence answer with two named entities and a transitive verb → 2 nodes, 1 edge.
- Long paragraph → at least N entities, deterministic across two calls (same input → same graph).

**E.5 — Health badge UI** (`compare.html` + `compare-runner.js`):
- Compact pill: numeric score + colored dot (red < 40, yellow 40–70, green > 70). "—" for `score == -1`.
- Tooltip on hover: 4-metric one-liners. (Drawer is the deep view; tooltip is the glance view.)
- Sits in the slot-panel header (next to the existing pipeline label) AND in each multi-hop answer panel footer.

**E.6 — Drilldown drawer** — slide-out drawer triggered by `open-health-drawer`:
- Header: "Topological Health: {score}/100"
- Four metric cards, each: name, value, plain-English explanation (≥3 sentences per PRD-055 #2).
- Footer: deliverable F (citation block).
- Close on backdrop click or ESC. Uses the existing modal/drawer infra from `state.js` (Wave E delegated `data-close-on-backdrop`).

### F. RAGSearch citation footer (PRD-055 #5)

In the drilldown drawer footer (single block, no new module):

```html
<footer class="health-drawer-citation">
  <p class="anchor-claim">Graph-RAG wins multi-hop by 26pp in the April 2026 Shanghai NYU benchmark — this score is why.</p>
  <p class="paper-link"><a href="https://arxiv.org/abs/2604.09666" target="_blank" rel="noopener">arXiv:2604.09666</a></p>
  <p class="repo-link"><a href="https://github.com/FanDongzhe123/RAGSearch" target="_blank" rel="noopener">github.com/FanDongzhe123/RAGSearch</a></p>
</footer>
```

---

## 3. Test strategy

| Layer | What | Location |
|---|---|---|
| Unit | Scenario YAML loader (4 scenarios, required fields) | `tests/unit/test_scenarios.py` |
| Unit | Judge JSON parsing + validation + permutation check | `tests/unit/test_judge.py` |
| Unit | Micro-graph builder (deterministic, shape) | `tests/unit/test_micrograph.py` |
| Unit | Topological Health contract self-test | `tests/unit/test_topological_health_contract.py` |
| Integration | `/api/multihop/run` happy path + cache-miss path | `tests/integration/test_multihop_endpoint.py` |
| Manual smoke | JNJ financial scenario across fan_out + agentic_flash + agentic_analyst, judge ranks, Reveal works | dev-report |
| Playwright smoke | Load `/compare`, click Run Multi-Hop, assert 3 answer panels + judge panel render without JS errors | `tests/e2e/test_multihop_smoke.py` (new — first Playwright test in repo, see §6) |

### Playwright bootstrap (one-shot infra)

PRD scope explicitly asks for a Playwright smoke. Wave F dev-report
flagged that promoting the ad-hoc `/tmp/wave-*.mjs` harnesses needed
`pytest-playwright` infra. Wave G adds it minimally:

- `pyproject.toml`: add `pytest-playwright` to dev deps.
- `playwright install chromium` is a one-time per-dev step; document in `tests/e2e/README.md`.
- One test file, one assertion: page loads, click works, expected DOM nodes appear, no `pageerror` events fired (excluding the pre-existing `/api/tickers` 404 noise that Wave E/F flagged).
- Test boots the real demo server (`uvicorn demos.extraction.demo_compare:app`) on a free port using `pytest-asyncio` + a session-scoped fixture. Seed corpus is JNJ (already on disk in `tests/fixtures/`).

This is scoped tight: one test, no broader E2E framework. The Wave F
"big lift" of promoting all 3 ad-hoc smokes stays deferred.

---

## 4. Commit plan

Five commits, one per logical area, in this order so each is independently green:

1. `feat(demo): multi-hop scenario YAML + loader + scenarios endpoint` (A + the GET `/api/multihop/scenarios`)
2. `feat(demo): LLM-as-judge Gemini Flash evaluator with blinded ranking` (C + tests)
3. `feat(demo): topological health adapter + LLM-answer micro-graph builder` (E.1 + E.4 + contract self-test + tests)
4. `feat(demo): /api/multihop/run parallel pipeline fan-out endpoint` (B + integration test) — depends on 1, 2, 3
5. `feat(demo): viewer-first multi-hop UI + topological health badge & drawer` (D + E.5 + E.6 + F + Playwright smoke)

If commit 5 grows past ~600 LOC of JS+HTML we split into 5a (UI scaffolding) + 5b (badge/drawer); keeping it as one keeps the data-action registrations atomic with the markup that uses them (Wave E/F precedent).

---

## 5. Non-goals (explicit re-statement)

Pulled directly from the assignment so future readers don't have to cross-reference:

- No extractor pipeline changes. Scenarios use whatever each pipeline already produces.
- No historical trend storage for Topological Health (PRD-055 nice-to-have #2).
- No judge-model swap UI (PRD-004 v4 nice-to-have).
- No bridge-entity graph colorization (PRD-055 nice-to-have #3).
- No per-strategy spider chart (PRD-055 nice-to-have #1).
- No touching of `ee8be81` cache-key fix.
- No judge-disagreement surfacing (PRD-004 v4 nice-to-have).
- No writing `kgspin_core.graph_topology.health` ourselves — that's the parallel core team's scope.

---

## 6. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `kgspin-core::compute_health` not landed by our integration phase | Medium | Path-editable dep means we see their progress live. Contract self-test catches drift. Sentinel-return shim keeps demo functional with "—" badges if module is missing. |
| Gemini Flash judge returns malformed JSON despite mode setting | Low | One retry at temp=0; on failure, response surfaces `judge: null` with an `error` and the UI shows "Judge unavailable for this run" instead of crashing. |
| Per-pipeline parallel dispatch hits Gemini rate limits | Medium | Three concurrent calls is well under our quota at demo scale. Per-call 60s timeout prevents one hung pipeline from blocking. Demo operator can serial-fallback by running scenarios one at a time. |
| Micro-graph builder produces too-large LLM "graphs" → LLM scores artificially high | Low | Capped at 50 nodes from a single answer; spaCy NER on a typical 300-word answer produces 10–25 entities. The asymmetry holds. |
| spaCy `en_core_web_sm` model not pre-downloaded in dev envs | Medium | Add `python -m spacy download en_core_web_sm` to `tests/e2e/README.md` and to a small bootstrap script `scripts/dev_bootstrap.sh`. |
| Playwright infra is new to the repo, dev-environment friction | Medium | One test, one script in `tests/e2e/README.md`. Skipped by default in CI if `PLAYWRIGHT_E2E=1` not set; runnable on demand. |
| Demo-day flake on the LLM-as-judge call | Low | Judge call is best-effort: failure leaves the 3 answers visible. Operator's pitch still works without the verdict. |

---

## 7. Open questions for CTO (none blocking, deciding ahead unless you stop me)

1. **Scenario authorship** — the assignment names two scenarios explicitly (J&J acquisitions/litigation, Stelara adverse events). The other two ("your choice, hops ≥ 2") I will draft against the JNJ + clinical corpora we already have indexed. **Default decision:** financial #2 = "What therapeutic areas does J&J's R&D pipeline target, and which acquired companies contribute to each?" (2-hop). Clinical #2 = "Which J&J immunology trials in Phase III have completed enrollment, and what was the recruitment timeline?" (2-hop). If you want different ones, say so before commit 1.

2. **Judge model fallback** — assignment says "Gemini 2.5 Flash". If `gemini-flash-2.5` isn't a registered alias in the current `llm_backend.py` registry, **default decision:** use whichever Gemini Flash variant resolves first (`gemini-2.0-flash` or whatever the registry currently maps "flash" to), document the choice in the dev-report. The judge model is a swappable knob; the demo-day operator can override.

3. **Playwright infra** — adding `pytest-playwright` is a real new dev dep. **Default decision:** add it; gate the test on `PLAYWRIGHT_E2E=1` env var so it doesn't run in default `pytest .`. If you want this deferred to a follow-up sprint, say so before commit 5.

4. **`uv.lock` churn** — adding `pytest-playwright` (and possibly `spacy` if it's not already a transitive dep) will bump `uv.lock`. **Default decision:** include the lockfile bump in the same commit as the dep add (commit 5 for playwright, earlier commit if spacy is needed). Standard practice; flagging only because lockfile diffs can be noisy in review.

If I don't hear back, I proceed with the defaults.

---

## 8. What "done" looks like (per CTO completion clause)

- [ ] Sprint plan on disk at this path ✅ (this file)
- [ ] All 6 deliverables (A–F) implemented on `wave-g-demo-close`
- [ ] All unit + integration tests green
- [ ] Topological Health contract self-test passes (proves cross-repo handshake)
- [ ] Playwright smoke green (with `PLAYWRIGHT_E2E=1` set)
- [ ] Manual JNJ smoke confirmed (financial scenario, 3 answers + judge + reveal)
- [ ] Branch pushed to origin
- [ ] Dev-report at `docs/sprints/wave-g-demo-close/dev-report.md` summarizing actuals vs. plan, deferrals, and demo-day operator notes

— Dev team
