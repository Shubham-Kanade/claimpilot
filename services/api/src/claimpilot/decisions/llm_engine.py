"""LLM fallback engine: the same questions answered by Claude through ``LLMClient``.

It exists so the system works without Jev (waitlist, outage) and so the two can be benchmarked
on identical questions (ADR-002). The LLM returns a flat, union-free object built from the
questions (ADR-017 limits), with a self-reported confidence per choice/score. Self-reported
confidence is NOT calibrated the way Jev's probabilities are; that difference is exactly what
the Jev-vs-LLM benchmark measures.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cache
from importlib.resources import files
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from claimpilot.decisions.types import Answer, DecisionResult, Question
from claimpilot.llm.client import LLMClient

ROUTE = "decision_fallback"
PROMPT_VERSION = "decide_v1"

_MODELS: dict[tuple[Any, ...], type[BaseModel]] = {}


@cache
def system_prompt(version: str = PROMPT_VERSION) -> str:
    return (files("claimpilot.decisions") / "prompts" / f"{version}.md").read_text("utf-8")


def answer_model(questions: Sequence[Question]) -> type[BaseModel]:
    """A flat, all-required Pydantic model with one field (plus confidence) per question.

    Cached per question set so the same class is returned every time (stable schema, so the
    API's grammar cache and the replay hash both stay valid).
    """
    signature = tuple(q.signature for q in questions)
    if signature not in _MODELS:
        fields: dict[str, Any] = {}
        for q in questions:
            if q.kind == "choice":
                options = Literal[q.options]  # type: ignore[valid-type]  # dynamic options
                fields[q.key] = (options, Field(description=q.instructions))
                fields[f"{q.key}_confidence"] = (float, Field(description=_CONFIDENCE))
            elif q.kind == "noul":
                fields[q.key] = (
                    float,
                    Field(description=f"{q.instructions} Probability from 0 (no) to 1 (yes)."),
                )
            else:
                levels = "; ".join(f"{i} = {text}" for i, text in enumerate(_levels(q)))
                fields[q.key] = (
                    float,
                    Field(description=f"{q.instructions} Level (decimals allowed): {levels}."),
                )
                fields[f"{q.key}_confidence"] = (float, Field(description=_CONFIDENCE))
        _MODELS[signature] = create_model(
            "DecisionAnswers", __config__=ConfigDict(extra="forbid"), **fields
        )
    return _MODELS[signature]


_CONFIDENCE = "Your confidence from 0 to 1 that the answer above is right."


def _levels(question: Question) -> Sequence[str]:
    assert not isinstance(question.criteria, Mapping) and question.criteria is not None
    return question.criteria


def _describe(question: Question) -> str:
    if question.kind == "choice":
        assert isinstance(question.criteria, Mapping)
        lines = "\n".join(
            f"  - {option}: {text}" if text else f"  - {option}"
            for option, text in question.criteria.items()
        )
        return f"- `{question.key}` (choose one): {question.instructions}\n{lines}"
    if question.kind == "noul" and isinstance(question.criteria, Mapping):
        meaning = "\n".join(
            f"  - {answer} means: {text}" for answer, text in question.criteria.items()
        )
        return f"- `{question.key}`: {question.instructions}\n{meaning}"
    return f"- `{question.key}`: {question.instructions}"


class LLMEngine:
    name = "llm"

    def __init__(self, llm: LLMClient, *, route: str = ROUTE) -> None:
        self._llm = llm
        self._route = route

    async def decide(
        self, state: Mapping[str, Any] | str, questions: Sequence[Question]
    ) -> DecisionResult:
        model = answer_model(questions)
        state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        prompt = (
            "<state>\n" + state_text + "\n</state>\n\nQuestions:\n"
            + "\n".join(_describe(q) for q in questions)
        )  # fmt: skip
        result = await self._llm.parse(
            self._route,
            system=system_prompt(),
            content=[{"type": "text", "text": prompt}],
            output_model=model,
            thinking="off",
        )
        raw = result.parsed.model_dump()
        answers = {q.key: self._answer(q, raw) for q in questions}
        usage = result.usage
        return DecisionResult(
            answers=answers,
            engine=self.name,
            latency_ms=result.latency_ms,
            input_tokens=usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=result.cost_usd,
        )

    def _answer(self, question: Question, raw: dict[str, Any]) -> Answer:
        value = raw[question.key]
        if question.kind == "noul":
            p = _unit(value)
            return Answer(
                key=question.key, kind="noul", value=p, confidence=max(p, 1 - p), engine=self.name
            )
        return Answer(
            key=question.key,
            kind=question.kind,
            value=value if question.kind == "choice" else float(value),
            confidence=_unit(raw[f"{question.key}_confidence"]),
            engine=self.name,
        )


def _unit(value: Any) -> float:
    return min(1.0, max(0.0, float(value)))
