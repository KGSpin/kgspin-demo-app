"""Benchmark harness runner.

Usage::

    python benchmarks/harness/run.py \\
        --arm b \\
        --retrieval fan_out_from_corpus \\
        --graph benchmarks/reports/<ts>/arm-b/graph.json \\
        --questions benchmarks/questions/financebench-subset.jsonl \\
        --llm-alias gemini_flash \\
        --output benchmarks/reports/<ts>/results.json

Contract:

- ``--arm a`` currently refuses with a pointer to
  ``benchmarks/arms/a/README.md`` (Arm A ships Wave 2).
- ``--arm b`` requires a pre-built graph JSON via ``--graph``; the run
  won't re-extract on every scoring pass (extraction is budget-capped).
- Retrieval is pluggable: add another strategy under
  ``benchmarks/retrieval/`` and wire it into ``_RETRIEVAL_REGISTRY``.

Output: ``results.json`` matches ``benchmarks/schemas/results-v0.json``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# --- Retrieval registry -----------------------------------------------------


def _retrieval_registry() -> dict[str, Callable[..., list[str]]]:
    # Import lazily so --help / --list-retrieval doesn't pay the import cost.
    from benchmarks.retrieval import (
        fan_out_from_corpus,
        fan_out_from_graph,
        semantic_composed,
    )
    return {
        "fan_out_from_corpus": fan_out_from_corpus.retrieve,
        "fan_out_from_graph": fan_out_from_graph.retrieve,
        "semantic_composed": semantic_composed.retrieve,
    }


# --- Answer generation ------------------------------------------------------


ANSWER_SYSTEM = """You answer questions grounded ONLY in the context provided.
If the context does not contain the answer, reply with "INSUFFICIENT_CONTEXT".
Keep answers concise. When a numeric value is requested, answer with the
value + unit."""

ANSWER_USER_TEMPLATE = """Question: {question}

Context:
{context}

Answer the question using only the context above."""


def _generate_answer(
    question: str,
    contexts: list[str],
    *,
    llm_alias: str | None,
    llm_provider: str | None,
    llm_model: str | None,
    mock_llm: bool,
) -> str:
    if mock_llm:
        # Deterministic echo for plumbing smoke tests: return the first
        # context joined with the question tokens.
        first = contexts[0] if contexts else ""
        return f"MOCK_ANSWER[{question[:30]}]::{first[:80]}"

    from kgspin_demo_app.llm_backend import resolve_llm_backend
    backend = resolve_llm_backend(
        llm_alias=llm_alias,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    prompt = ANSWER_USER_TEMPLATE.format(
        question=question,
        context="\n---\n".join(contexts) if contexts else "(no context)",
    )
    try:
        result = backend.complete(
            prompt=prompt,
            system_prompt=ANSWER_SYSTEM,
            max_tokens=256,
            temperature=0.0,
        )
        return result.text.strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("[RUNNER] answer generation failed: %s: %s", type(e).__name__, e)
        return f"GENERATION_ERROR::{type(e).__name__}"


# --- Orchestration ----------------------------------------------------------


def _load_questions(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def run(
    *,
    arm: str,
    retrieval: str,
    graph_path: Path,
    questions_path: Path,
    output_path: Path,
    limit: int | None = None,
    llm_alias: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    mock_llm: bool = False,
    top_k: int = 5,
    metrics_engine: str = "auto",
) -> dict[str, Any]:
    if arm == "a":
        raise RuntimeError(
            "Arm A is not yet wired. See benchmarks/arms/a/README.md — "
            "alpha_runner adapter ships in Wave 2."
        )
    if arm != "b":
        raise ValueError(f"Unknown arm: {arm!r}")

    registry = _retrieval_registry()
    if retrieval not in registry:
        raise ValueError(
            f"Unknown retrieval strategy {retrieval!r}. "
            f"Known: {sorted(registry)}"
        )
    retrieve_fn = registry[retrieval]

    with graph_path.open() as f:
        graph = json.load(f)
    if graph.get("schema_version") != "graph-v0":
        raise ValueError(
            f"Graph at {graph_path} is not graph-v0 "
            f"(saw {graph.get('schema_version')!r}). Re-extract with Arm B."
        )

    questions = _load_questions(questions_path, limit=limit)

    started = datetime.datetime.now(datetime.UTC).isoformat()
    per_question: list[dict[str, Any]] = []
    from benchmarks.harness import metrics as M

    for q in questions:
        qid = q.get("question_id") or q.get("financebench_id") or str(uuid.uuid4())
        question = q.get("question") or q.get("query") or ""
        gold = q.get("gold_answer") or q.get("answer") or ""

        contexts = retrieve_fn(graph, question, top_k=top_k)
        predicted = _generate_answer(
            question,
            contexts,
            llm_alias=llm_alias,
            llm_provider=llm_provider,
            llm_model=llm_model,
            mock_llm=mock_llm,
        )

        metrics = M.score_simple(question, predicted, gold, contexts)
        if metrics_engine in ("auto", "ragas"):
            ragas_scores = M.score_ragas(
                question=question,
                predicted=predicted,
                gold=gold,
                contexts=contexts,
            )
            if ragas_scores is not None:
                metrics.update(ragas_scores)
            elif metrics_engine == "ragas":
                logger.warning(
                    "[RUNNER] --metrics ragas requested but RAGAS is unavailable; "
                    "using simple fallback."
                )

        per_question.append({
            "question_id": qid,
            "question": question,
            "predicted_answer": predicted,
            "gold_answer": gold,
            "retrieved_context": contexts,
            "metrics": metrics,
        })

    finished = datetime.datetime.now(datetime.UTC).isoformat()
    results = {
        "schema_version": "results-v0",
        "run_id": str(uuid.uuid4()),
        "started_at": started,
        "finished_at": finished,
        "arm": arm,
        "retrieval": retrieval,
        "questions_source": str(questions_path),
        "llm": {
            "alias": (llm_alias if not mock_llm else "mock"),
            "provider": llm_provider,
            "model": llm_model,
        },
        "per_question": per_question,
        "aggregates": M.aggregate(per_question),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2))
    logger.info(
        "[RUNNER] wrote %s (%d questions, aggregates=%s)",
        output_path, len(per_question), results["aggregates"],
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KGSpin benchmark harness")
    parser.add_argument("--arm", choices=["a", "b"], required=True)
    parser.add_argument(
        "--retrieval",
        choices=["fan_out_from_corpus", "fan_out_from_graph", "semantic_composed"],
        required=True,
    )
    parser.add_argument("--graph", type=Path, required=True,
                        help="Path to graph-v0 JSON (Arm A or B output).")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="Where to write results.json (results-v0 schema).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap number of questions for smoke runs.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--llm-alias", default=None)
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--mock-llm", action="store_true",
                        help="Use deterministic stub answers (plumbing smoke).")
    parser.add_argument("--metrics", default="auto",
                        choices=["auto", "ragas", "simple"])
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        run(
            arm=args.arm,
            retrieval=args.retrieval,
            graph_path=args.graph,
            questions_path=args.questions,
            output_path=args.output,
            limit=args.limit,
            llm_alias=args.llm_alias,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            mock_llm=args.mock_llm,
            top_k=args.top_k,
            metrics_engine=args.metrics,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[RUNNER] aborted: %s: %s", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
