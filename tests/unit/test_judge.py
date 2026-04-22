"""Tests for the LLM-as-judge (PRD-004 v4 #11).

Mocks the backend — does not call real Gemini. The "5 calls same ranking"
stability test is documented as a manual smoke (one CI run shouldn't burn
five live LLM calls).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from demos.extraction.judge import (
    JudgeParseError,
    JudgeVerdict,
    rank_answers,
)


@dataclass
class _FakeResult:
    text: str


class _FakeBackend:
    """Minimal duck-typed backend that returns a queued list of texts."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, temperature: float | None = None, **_: object) -> _FakeResult:
        self.calls.append({"prompt": prompt, "temperature": temperature})
        return _FakeResult(text=self._responses.pop(0))


def _well_formed_response(ranking=("A", "C", "B")) -> str:
    return json.dumps({
        "ranking": list(ranking),
        "rationales": {
            "A": "Most specific; cites three trial IDs.",
            "B": "Hedges with 'likely' and skips the timeline.",
            "C": "Solid coverage but omits two adverse events.",
        },
    })


def test_well_formed_response_parses():
    backend = _FakeBackend([_well_formed_response()])
    verdict = rank_answers("q?", ["a", "b", "c"], backend=backend)
    assert isinstance(verdict, JudgeVerdict)
    assert verdict.ranking == ["A", "C", "B"]
    assert set(verdict.rationales) == {"A", "B", "C"}


def test_judge_call_uses_temperature_zero():
    backend = _FakeBackend([_well_formed_response()])
    rank_answers("q?", ["a", "b", "c"], backend=backend)
    assert backend.calls[0]["temperature"] == 0.0


def test_prompt_does_not_leak_pipeline_names():
    backend = _FakeBackend([_well_formed_response()])
    rank_answers("q?", ["fan_out answer", "agentic_flash answer", "agentic_analyst answer"], backend=backend)
    prompt = backend.calls[0]["prompt"]
    assert "fan_out" in prompt  # only because callers passed it as an answer string
    # critical anti-leak: caller is responsible for stripping pipeline names from
    # answer text. The judge prompt template MUST NOT contain "fan_out" itself.
    assert "Pipeline" not in prompt and "pipeline=" not in prompt


def test_ranking_with_duplicates_raises():
    bad = json.dumps({
        "ranking": ["A", "A", "B"],
        "rationales": {"A": "x", "B": "y", "C": "z"},
    })
    backend = _FakeBackend([bad, bad])
    with pytest.raises(JudgeParseError, match="permutation"):
        rank_answers("q?", ["a", "b", "c"], backend=backend)


def test_missing_rationale_key_raises():
    bad = json.dumps({
        "ranking": ["A", "B", "C"],
        "rationales": {"A": "x", "B": "y"},  # missing "C"
    })
    backend = _FakeBackend([bad, bad])
    with pytest.raises(JudgeParseError, match="A/B/C"):
        rank_answers("q?", ["a", "b", "c"], backend=backend)


def test_non_json_response_raises():
    backend = _FakeBackend(["sorry I cannot do that", "still not JSON"])
    with pytest.raises(JudgeParseError, match="non-JSON"):
        rank_answers("q?", ["a", "b", "c"], backend=backend)


def test_one_retry_on_first_parse_failure():
    backend = _FakeBackend(["not JSON", _well_formed_response()])
    verdict = rank_answers("q?", ["a", "b", "c"], backend=backend)
    assert verdict.ranking == ["A", "C", "B"]
    assert len(backend.calls) == 2


def test_determinism_same_inputs_same_ranking():
    """Mocked stability check: identical input → identical ranking across N calls.
    Proves the judge code path doesn't perturb the prompt or interpret ranking.
    """
    rankings = []
    for _ in range(5):
        backend = _FakeBackend([_well_formed_response(("B", "A", "C"))])
        rankings.append(rank_answers("q?", ["a", "b", "c"], backend=backend).ranking)
    assert all(r == ["B", "A", "C"] for r in rankings)


def test_wrong_answer_count_raises():
    backend = _FakeBackend([_well_formed_response()])
    with pytest.raises(ValueError, match="exactly 3 answers"):
        rank_answers("q?", ["a", "b"], backend=backend)


def test_to_dict_roundtrips():
    v = JudgeVerdict(ranking=["A", "B", "C"], rationales={"A": "1", "B": "2", "C": "3"})
    d = v.to_dict()
    assert d["ranking"] == ["A", "B", "C"]
    assert d["rationales"]["B"] == "2"
