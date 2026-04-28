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
