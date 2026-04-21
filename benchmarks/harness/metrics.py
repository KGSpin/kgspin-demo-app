"""Metrics for the benchmark harness.

Primary: `RAGAS <https://docs.ragas.io>`_ (`ragas>=0.1`). RAGAS ships
multi-hop-relevant metrics — ``faithfulness``, ``answer_relevancy``,
``context_precision``, ``context_recall`` — that score the composite
retrieval + generation pipeline end-to-end.

Fallback: lightweight EM + token-F1 computed inline. Used when:

- RAGAS isn't installed (it is NOT a hard dependency of kgspin-demo —
  the harness is optional infrastructure).
- RAGAS fails to load the required evaluator LLM (e.g. no API key in
  a CI smoke run).
- The caller passes ``--metrics simple`` explicitly.

DeepEval was evaluated as a fallback primary. Decision noted in the
sprint 20 dev-report and in ``benchmarks/README.md``: RAGAS is
primary because its multi-hop metrics outperform DeepEval's out-of-the-
box on the FinanceBench multi-hop subset in the pilot on sprint 16.
DeepEval stays as a secondary option; plumb it in only if RAGAS
under-performs on a future question set.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

WORD_RE = re.compile(r"\w+")
_ARTICLES = {"a", "an", "the"}


def normalize_answer(text: str) -> str:
    """Normalize for string-match metrics: lowercase, strip articles + punct."""
    if not text:
        return ""
    tokens = [t.lower() for t in WORD_RE.findall(text)]
    tokens = [t for t in tokens if t not in _ARTICLES]
    return " ".join(tokens)


def exact_match(pred: str, gold: str) -> float:
    return 1.0 if normalize_answer(pred) == normalize_answer(gold) else 0.0


def token_f1(pred: str, gold: str) -> float:
    pt = set(normalize_answer(pred).split())
    gt = set(normalize_answer(gold).split())
    if not pt and not gt:
        return 1.0
    if not pt or not gt:
        return 0.0
    common = pt & gt
    if not common:
        return 0.0
    precision = len(common) / len(pt)
    recall = len(common) / len(gt)
    return 2 * precision * recall / (precision + recall)


def context_recall_tokens(contexts: list[str], gold: str) -> float:
    """Token-level recall of gold answer across retrieved contexts.

    Approximation of ``context_recall`` used when RAGAS isn't available.
    """
    gold_tokens = set(normalize_answer(gold).split())
    if not gold_tokens:
        return 0.0
    ctx_tokens: set[str] = set()
    for c in contexts:
        ctx_tokens |= set(normalize_answer(c).split())
    return len(gold_tokens & ctx_tokens) / len(gold_tokens)


def score_simple(
    question: str,
    predicted: str,
    gold: str,
    contexts: list[str],
) -> dict[str, float]:
    """Deterministic fallback scores — no LLM calls, no API keys."""
    return {
        "em": exact_match(predicted, gold),
        "f1": token_f1(predicted, gold),
        "context_recall_tokens": context_recall_tokens(contexts, gold),
    }


def score_ragas(
    *,
    question: str,
    predicted: str,
    gold: str,
    contexts: list[str],
) -> dict[str, float] | None:
    """Score one Q/A row with RAGAS. Returns ``None`` if unavailable.

    Per-row scoring keeps the harness row-streamable so huge question
    sets don't require loading the whole dataframe into memory. Callers
    that want batched eval should aggregate the returned dicts.
    """
    try:
        from datasets import Dataset  # noqa: F401 — imported lazily
        from ragas import evaluate  # type: ignore
        from ragas.metrics import (  # type: ignore
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[METRICS] RAGAS unavailable (%s); falling back.", e)
        return None

    try:
        from datasets import Dataset
        ds = Dataset.from_dict({
            "question": [question],
            "answer": [predicted],
            "contexts": [contexts or [""]],
            "ground_truth": [gold],
        })
        result = evaluate(
            ds,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        )
        row = result.to_pandas().iloc[0].to_dict()
        # RAGAS returns np.float — cast to float for JSON-friendliness
        return {
            "faithfulness": float(row.get("faithfulness", 0.0) or 0.0),
            "answer_relevancy": float(row.get("answer_relevancy", 0.0) or 0.0),
            "context_precision": float(row.get("context_precision", 0.0) or 0.0),
            "context_recall": float(row.get("context_recall", 0.0) or 0.0),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[METRICS] RAGAS evaluate failed (%s); falling back.", e)
        return None


def aggregate(per_question: list[dict[str, Any]]) -> dict[str, float]:
    """Mean every numeric metric across per-question rows."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in per_question:
        for k, v in (row.get("metrics") or {}).items():
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            sums[k] = sums.get(k, 0.0) + v
            counts[k] = counts.get(k, 0) + 1
    return {k: sums[k] / counts[k] for k in sums if counts[k] > 0}
