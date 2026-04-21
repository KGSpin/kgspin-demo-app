"""Unit tests for ``benchmarks.harness`` — metrics, split, and runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.harness import metrics as M
from benchmarks.harness import run as runner
from benchmarks.harness import split


# --- metrics ---------------------------------------------------------------


def test_normalize_answer_strips_articles_and_punct() -> None:
    assert M.normalize_answer("The quick, brown fox!") == "quick brown fox"


def test_exact_match_is_article_insensitive() -> None:
    assert M.exact_match("The Revenue", "revenue") == 1.0
    assert M.exact_match("the revenue", "expenses") == 0.0


def test_token_f1_partial_overlap() -> None:
    assert M.token_f1("apple revenue", "apple") == pytest.approx(2 / 3)
    assert M.token_f1("", "") == 1.0
    assert M.token_f1("apple", "") == 0.0


def test_context_recall_tokens() -> None:
    ctx = ["Apple reported revenue of $10B.", "Unrelated chunk."]
    assert M.context_recall_tokens(ctx, "Apple revenue") == 1.0
    assert M.context_recall_tokens(ctx, "Microsoft Azure") == 0.0


def test_score_simple_keys() -> None:
    out = M.score_simple("q?", "pred", "gold", ["context"])
    assert set(out) == {"em", "f1", "context_recall_tokens"}


def test_aggregate_averages_per_metric() -> None:
    rows = [
        {"metrics": {"f1": 1.0, "em": 1.0}},
        {"metrics": {"f1": 0.0, "em": 0.0}},
        {"metrics": {"f1": 0.5}},  # missing em — shouldn't crash
    ]
    agg = M.aggregate(rows)
    assert agg["f1"] == pytest.approx(0.5)
    assert agg["em"] == pytest.approx(0.5)


# --- split -----------------------------------------------------------------


def test_is_heldout_is_deterministic() -> None:
    assert split.is_heldout("financebench_id_00499") == split.is_heldout("financebench_id_00499")


def test_split_roughly_twenty_percent_heldout() -> None:
    n = 1000
    heldout = sum(1 for i in range(n) if split.is_heldout(f"q_{i:05d}"))
    # Allow ±6% drift around 20% on 1k samples.
    assert 140 <= heldout <= 260


# --- runner integration (mock mode) ----------------------------------------


def test_runner_rejects_arm_a(tmp_path: Path) -> None:
    graph = tmp_path / "g.json"
    graph.write_text(json.dumps({"schema_version": "graph-v0", "arm": "a",
                                 "corpus_id": "c", "chunks": [],
                                 "nodes": [], "edges": []}))
    questions = tmp_path / "q.jsonl"
    questions.write_text("")
    with pytest.raises(RuntimeError, match="Arm A is not yet wired"):
        runner.run(
            arm="a", retrieval="fan_out_from_corpus",
            graph_path=graph, questions_path=questions,
            output_path=tmp_path / "out.json", mock_llm=True,
        )


def test_runner_end_to_end_in_mock_mode(tmp_path: Path) -> None:
    graph = {
        "schema_version": "graph-v0", "arm": "b", "corpus_id": "c",
        "chunks": [{"chunk_id": "c1", "doc_id": "D",
                    "text": "Apple revenue grew 10%."}],
        "nodes": [{"node_id": "n1", "surface_form": "Apple",
                   "node_type": "ORGANIZATION",
                   "provenance": [{"chunk_id": "c1"}]}],
        "edges": [],
    }
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph))

    questions_path = tmp_path / "q.jsonl"
    questions_path.write_text(json.dumps({
        "question_id": "q1",
        "question": "How much did Apple revenue grow?",
        "gold_answer": "10%",
    }) + "\n")

    output = tmp_path / "results.json"
    results = runner.run(
        arm="b", retrieval="fan_out_from_corpus",
        graph_path=graph_path, questions_path=questions_path,
        output_path=output, mock_llm=True,
        metrics_engine="simple",
    )
    assert results["arm"] == "b"
    assert results["retrieval"] == "fan_out_from_corpus"
    assert results["llm"]["alias"] == "mock"
    assert len(results["per_question"]) == 1
    row = results["per_question"][0]
    assert row["question_id"] == "q1"
    assert row["predicted_answer"].startswith("MOCK_ANSWER")
    assert output.is_file()
    saved = json.loads(output.read_text())
    assert saved["schema_version"] == "results-v0"


def test_runner_rejects_wrong_schema(tmp_path: Path) -> None:
    graph_path = tmp_path / "g.json"
    graph_path.write_text(json.dumps({"schema_version": "graph-v999"}))
    questions_path = tmp_path / "q.jsonl"
    questions_path.write_text("")
    with pytest.raises(ValueError, match="graph-v0"):
        runner.run(
            arm="b", retrieval="fan_out_from_corpus",
            graph_path=graph_path, questions_path=questions_path,
            output_path=tmp_path / "o.json", mock_llm=True,
        )
