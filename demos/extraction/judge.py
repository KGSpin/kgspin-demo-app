"""LLM-as-judge for blinded multi-hop A/B/C answer ranking (PRD-004 v4 #11).

Single Gemini Flash call with JSON mode, temp=0, blinded labels A/B/C.
The judge prompt never sees pipeline names, KG context, topology scores,
or citations — only the question + the three answer texts.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from kgspin_demo_app.llm_backend import resolve_llm_backend


_JUDGE_PROMPT = """You are an evaluator. Below is a question followed by three candidate
answers labeled A, B, C. Rank them from best to worst using this rubric:
- Most specific (cites concrete entities, numbers, dates)
- Most complete (addresses all parts of the question)
- Least speculative (avoids hedging, "likely", "possibly")

Question: {question}

Answer A: {answer_a}

Answer B: {answer_b}

Answer C: {answer_c}

Respond with JSON only, in this exact shape:
{{
  "ranking": ["X", "Y", "Z"],
  "rationales": {{"A": "one-sentence reason", "B": "one-sentence reason", "C": "one-sentence reason"}}
}}
"""


_JUDGE_MODEL_ALIAS = "gemini_flash"
_VALID_LABELS = ("A", "B", "C")


class JudgeParseError(ValueError):
    """Raised when the judge response cannot be parsed into a JudgeVerdict."""


@dataclass(frozen=True)
class JudgeVerdict:
    ranking: list[str]
    rationales: dict[str, str]

    def to_dict(self) -> dict:
        return asdict(self)


def _build_prompt(question: str, answers: list[str]) -> str:
    if len(answers) != 3:
        raise ValueError(f"judge requires exactly 3 answers, got {len(answers)}")
    return _JUDGE_PROMPT.format(
        question=question,
        answer_a=answers[0],
        answer_b=answers[1],
        answer_c=answers[2],
    )


def _parse_response(raw: str) -> JudgeVerdict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise JudgeParseError(f"judge returned non-JSON response: {e}") from e

    ranking = payload.get("ranking")
    rationales = payload.get("rationales")
    if not isinstance(ranking, list) or sorted(ranking) != list(_VALID_LABELS):
        raise JudgeParseError(
            f"judge ranking must be a permutation of A/B/C, got {ranking!r}"
        )
    if not isinstance(rationales, dict) or set(rationales.keys()) != set(_VALID_LABELS):
        raise JudgeParseError(
            f"judge rationales must have keys A/B/C, got {sorted(rationales) if isinstance(rationales, dict) else type(rationales).__name__}"
        )
    for label in _VALID_LABELS:
        if not isinstance(rationales[label], str) or not rationales[label].strip():
            raise JudgeParseError(f"judge rationale for {label} is empty or non-string")
    return JudgeVerdict(ranking=list(ranking), rationales=dict(rationales))


_TWO_ANSWER_PROMPT = """You are an evaluator. Below is a question followed by two
candidate answers labeled A and B. Rank them best-to-worst using this rubric:
- Most specific (cites concrete entities, numbers, dates)
- Most complete (addresses all parts of the question)
- Least speculative (avoids hedging, "likely", "possibly")
- Best grounded (clearly relies on document evidence)

Question: {question}

Answer A: {answer_a}

Answer B: {answer_b}

Respond with JSON only, in this exact shape:
{{
  "winner": "A" or "B" or "tie",
  "rationale_a": "one-sentence assessment of A",
  "rationale_b": "one-sentence assessment of B",
  "verdict": "one-sentence overall conclusion"
}}
"""


@dataclass(frozen=True)
class TwoAnswerVerdict:
    """Scenario A blinded A/B verdict (PRD-004 v5)."""
    winner: str  # "A" | "B" | "tie"
    rationale_a: str
    rationale_b: str
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_two_answer_response(raw: str) -> TwoAnswerVerdict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise JudgeParseError(f"judge returned non-JSON response: {e}") from e
    winner = payload.get("winner")
    if winner not in ("A", "B", "tie"):
        raise JudgeParseError(
            f"judge winner must be 'A' / 'B' / 'tie', got {winner!r}"
        )
    rationale_a = payload.get("rationale_a", "")
    rationale_b = payload.get("rationale_b", "")
    verdict = payload.get("verdict", "")
    if not all(isinstance(s, str) for s in (rationale_a, rationale_b, verdict)):
        raise JudgeParseError("judge rationales/verdict must be strings")
    return TwoAnswerVerdict(
        winner=winner,
        rationale_a=rationale_a,
        rationale_b=rationale_b,
        verdict=verdict,
    )


def rank_two(
    question: str,
    answer_a: str,
    answer_b: str,
    *,
    backend=None,
) -> TwoAnswerVerdict:
    """PRD-004 v5 Scenario A blinded A/B judge.

    Same temperature-0 + JSON-mode contract as :func:`rank_answers`.
    Retries once on parse / validation failure; second failure raises
    :class:`JudgeParseError`.
    """
    prompt = _TWO_ANSWER_PROMPT.format(
        question=question, answer_a=answer_a, answer_b=answer_b,
    )
    if backend is None:
        backend = resolve_llm_backend(llm_alias=_JUDGE_MODEL_ALIAS, flow="judge")
    last_error: JudgeParseError | None = None
    for _ in range(2):
        result = backend.complete(prompt, temperature=0.0)
        try:
            return _parse_two_answer_response(result.text)
        except JudgeParseError as e:
            last_error = e
            continue
    assert last_error is not None
    raise last_error


def rank_answers(
    question: str,
    answers: list[str],
    *,
    backend=None,
) -> JudgeVerdict:
    """Rank three answers blinded as A/B/C.

    The backend defaults to Gemini Flash via ``resolve_llm_backend``.
    Tests pass a mock backend with a ``complete(prompt, *, ...)`` method
    that returns a duck-typed object with ``.text``.

    Retries once on JSONDecodeError or validation failure; second failure
    raises ``JudgeParseError``.
    """
    prompt = _build_prompt(question, answers)
    if backend is None:
        backend = resolve_llm_backend(llm_alias=_JUDGE_MODEL_ALIAS, flow="judge")

    last_error: JudgeParseError | None = None
    for _ in range(2):
        result = backend.complete(prompt, temperature=0.0)
        try:
            return _parse_response(result.text)
        except JudgeParseError as e:
            last_error = e
            continue
    assert last_error is not None
    raise last_error
