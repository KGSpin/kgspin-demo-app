# VP Reviews — PRD-004 v5 Phase 5A Sprint Plan

**Sprint:** `sprint-prd-004-v5-phase-5a-20260428`
**Date:** 2026-04-28

This file records the VP-Eng and VP-Prod review verdicts on the sprint plan. v1 returned APPROVED-WITH-MAJOR-ITEMS from both reviewers. The plan was revised to v2 addressing every blocker and major; v2 was re-reviewed and approved.

---

## VP-Eng — v1 review (APPROVED WITH MAJOR ITEMS)

**Top 3 blocker concerns:**
1. Deliverable E LOC under-budgeted (~1,065 actual vs 650 planned).
2. `build_rag_corpus.py` calling `_run_kgenskills` — the actual entry point — needs an explicit public-helper extraction.
3. Bundle→string serialization for paper-mirror is unspecified.

**Top 5 major concerns:**
1. Commit 9 too large — split deletion + addition halves.
2. Gold schema needs `key_fields` per scenario for deterministic F1.
3. SSE for Scenario B mentioned in risks but not budgeted in deliverable H.
4. `sentence-transformers` CI burden — add `FakeEmbedder` for unit tests.
5. `pane_outputs` should be dict not list for forward-compat with 5B.

**Minor nits:** `kgspin_core_sha` in manifest fingerprint; F1 tests in commit 8; `beautifulsoup4` verification gated to commit A.

## VP-Eng — v2 re-review (APPROVED)

All 3 blockers, all 5 majors, and all 3 minor nits adequately addressed with concrete artifacts. LOC re-baseline for E is honest; wall-clock plan absorbs it without breaking the 16h cap. `run_fan_out_extraction` public-API extraction is the right call. Bundle serialization is unambiguous and test-covered. `key_fields` makes F1 deterministic. SSE is the correct mechanism for the 30-60s wait. `pane_outputs` as dict is the forward-compat shape 5B needs. Commit 9 split keeps reviews tractable.

> "Ship it. CTO can authorize EXECUTE."

---

## VP-Prod — v1 review (APPROVED WITH MAJOR ITEMS)

**Top 3 blocker concerns:**
1. Phase 5B placeholder pane will read as "we ran out of time" — needs reframing per PRD §3 #10 ("hidden by default", "Show advanced" toggle).
2. Per-graph "why" tab → /compare deep-link is under-specified for demo continuity (no preview, no auto-run).
3. F1 recovery narrative is missing for cases when paper-mirror underperforms.

**Top 5 major concerns:**
1. Clinical scope cut is technically correct but a CEO-pitch risk; recommend hedging with 1 clinical template + 1 gold key.
2. A1/A2/A3 toggle is jargon-heavy for first-time AI engineer evaluators.
3. Two tickers will land as thin under demo conditions — recommend showing all 7 in dropdown.
4. V4 multi-hop UI deletion is too aggressive — verify per-graph view actually exists before deleting v4 backend.
5. Self-reflection on/off default is product-blocking.

## VP-Prod — v2 re-review (APPROVED)

All 3 blockers and all 5 majors resolved. Two-pane default with "Show advanced" toggle reads as deliberate product framing rather than missing work. Clinical hedge (Stelara/JNJ) keeps the CEO pitch credible. F1 framing as "illustrative, n=11, directional check" plus per-scenario `narrative_recovery` strings means an ugly F1 becomes a presenter talking point rather than a stumble. Per-graph deep-link with autorun stitches the two demo surfaces together cleanly.

> "With v2, Phase 5A tells a strong story. … Ship it."

**Soft watches (not blocking):**
- Demo operators may inadvertently run Scenario B against "qualitative-only" tickers during a live pitch. Gold-badge framing mitigates.
- Deliverable I (~700 LOC frontend) is parallel-tractable per §8 but tight; flag for execution-phase awareness.

---

## CTO sign-off

Both VPs APPROVED v2. Awaiting CTO authorization to begin EXECUTE.
