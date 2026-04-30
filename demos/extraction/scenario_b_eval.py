"""F1 scoring for Scenario B vs hand-authored gold.

PRD-004 v5 Phase 5A — deliverable H. Set-of-tuples F1 keyed on each
template's ``key_fields``.

Pipeline:
  1. Each pane's ``final_answer`` is a free-text answer.
  2. ``extract_structured_from_answer`` LLM-parses the answer into a
     list of dicts whose keys are the template's ``key_fields``.
     (Bypassed in tests via ``parsed_override``; production uses
     ``gemini_flash``.)
  3. Each parsed dict + each gold structured row → tuple of
     ``(value_for_key_field_1, value_for_key_field_2, ...)`` after
     case-fold + whitespace-norm.
  4. Set-of-tuples F1: precision = |gold ∩ pred| / |pred|, recall =
     |gold ∩ pred| / |gold|, F1 = 2*p*r / (p+r).

UI labels the score "Illustrative F1, n=11, see methodology" — this
is a directional check, not a benchmark. Per VP-Prod blocker #3.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


_NORM_WS = re.compile(r"\s+")


def _normalize_value(v) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = _NORM_WS.sub(" ", s)
    # Strip common terminators (".", "(illustrative)", etc.) so dollar-amounts
    # round-trip cleanly when the gold uses paranthetical hedges.
    s = re.sub(r"\(.*?\)", "", s).strip()
    s = s.rstrip(".")
    return s


def _row_to_tuple(row: dict, key_fields: list[str]) -> tuple:
    return tuple(_normalize_value(row.get(k)) for k in key_fields)


def _set_of_tuples(rows: list[dict], key_fields: list[str]) -> set[tuple]:
    out: set[tuple] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.add(_row_to_tuple(row, key_fields))
    return out


# ---------------------------------------------------------------------------
# F1 scorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class F1Score:
    f1: float
    precision: float
    recall: float
    f1_confidence: str  # "lenient" | "judge_assisted" | "partial"
    n_gold: int
    n_pred: int
    n_overlap: int


def score_set_of_tuples(
    pred_rows: list[dict],
    gold_rows: list[dict],
    key_fields: list[str],
    *,
    gold_confidence: str = "high",
) -> F1Score:
    """Set-of-tuples F1 on key_fields, lenient string match."""
    pred_set = _set_of_tuples(pred_rows, key_fields)
    gold_set = _set_of_tuples(gold_rows, key_fields)

    overlap = pred_set & gold_set
    n_pred = len(pred_set)
    n_gold = len(gold_set)
    n_over = len(overlap)

    if n_pred == 0 and n_gold == 0:
        # Both empty → trivial 1.0 (a "no qualifying rows" gold matched
        # by an empty prediction).
        return F1Score(
            f1=1.0, precision=1.0, recall=1.0,
            f1_confidence="lenient", n_gold=0, n_pred=0, n_overlap=0,
        )
    if n_pred == 0 or n_gold == 0:
        return F1Score(
            f1=0.0, precision=0.0, recall=0.0,
            f1_confidence="lenient", n_gold=n_gold, n_pred=n_pred, n_overlap=0,
        )

    precision = n_over / n_pred
    recall = n_over / n_gold
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    # When gold is partial, downgrade confidence — UI shows "partial"
    # next to the score so demo presenters know this is a hedge.
    f1_confidence = "partial" if gold_confidence in ("partial", "low", "draft") else "lenient"

    return F1Score(
        f1=f1, precision=precision, recall=recall,
        f1_confidence=f1_confidence,
        n_gold=n_gold, n_pred=n_pred, n_overlap=n_over,
    )


# ---------------------------------------------------------------------------
# Free-text → structured answer parser (LLM-assisted)
# ---------------------------------------------------------------------------


_EXTRACTION_PROMPT = """You are a structured-answer extractor. The user answered a
question; your job is to pull out the structured rows the answer
implies, in the schema requested.

Question: {question}

Answer:
{answer}

Output STRICTLY a JSON object with one key "structured" whose value is
a list of dicts. Each dict must have exactly these keys: {key_fields}.
If the answer indicates "none qualify" or has no structured rows,
return {{"structured": []}}.

JSON only, no commentary."""


def extract_structured_from_answer(
    question: str,
    answer: str,
    key_fields: list[str],
    *,
    llm_complete=None,
) -> list[dict]:
    """LLM-parse a free-text answer into structured rows.

    Production binds ``llm_complete`` to a sync ``gemini_flash``
    backend's ``complete``. Tests pass a callable that returns canned
    JSON.

    On any parse failure, returns ``[]`` so F1 falls through to
    no-overlap rather than raising.
    """
    if llm_complete is None:
        from kgspin_demo_app.llm_backend import resolve_llm_backend
        backend = resolve_llm_backend(llm_alias="gemini_flash", flow="scenario_b_extract")
        llm_complete = lambda p: backend.complete(p, temperature=0.0).text  # noqa: E731

    prompt = _EXTRACTION_PROMPT.format(
        question=question, answer=answer, key_fields=key_fields,
    )
    try:
        raw = llm_complete(prompt)
    except Exception as exc:
        logger.warning("structured-extraction LLM call failed: %s", exc)
        return []

    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            logger.warning("structured-extraction LLM output not parseable as JSON")
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, dict):
        return []
    rows = parsed.get("structured")
    return [r for r in (rows or []) if isinstance(r, dict)]


__all__ = [
    "F1Score",
    "score_set_of_tuples",
    "extract_structured_from_answer",
]
