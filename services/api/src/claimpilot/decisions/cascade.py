"""Confidence-gated cascade: System One first, System Two when it is unsure or unavailable.

Jev answers every question quickly and cheaply. Any choice/score answer below
``min_confidence`` is re-asked on the fallback engine (a stronger reader) for just those
questions. If Jev is unreachable the whole request goes to the fallback, so the pipeline keeps
working through an outage (graceful degradation, same typed result either way).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from claimpilot.decisions.types import (
    DecisionAuthError,
    DecisionEngine,
    DecisionError,
    DecisionResult,
    Question,
)

logger = logging.getLogger(__name__)


class CascadeEngine:
    def __init__(
        self,
        primary: DecisionEngine,
        fallback: DecisionEngine,
        *,
        min_confidence: float = 0.7,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._min_confidence = min_confidence
        self.name = f"{primary.name}+{fallback.name}"

    async def decide(
        self, state: Mapping[str, Any] | str, questions: Sequence[Question]
    ) -> DecisionResult:
        try:
            first = await self._primary.decide(state, questions)
        except DecisionAuthError:
            raise  # a bad key is a configuration error, not an outage: surface it
        except DecisionError as exc:
            logger.warning("%s unavailable (%s); using %s", self._primary.name, exc, self.name)
            return await self._fallback.decide(state, questions)

        unsure = [
            q
            for q in questions
            if q.kind != "noul" and first.answers[q.key].confidence < self._min_confidence
        ]
        if not unsure:
            return first

        second = await self._fallback.decide(state, unsure)
        answers = {**first.answers, **second.answers}
        return DecisionResult(
            answers=answers,
            engine=self.name,
            latency_ms=first.latency_ms + second.latency_ms,
            input_tokens=first.input_tokens + second.input_tokens,
            output_tokens=first.output_tokens + second.output_tokens,
            cost_usd=first.cost_usd + second.cost_usd,
            escalated=tuple(q.key for q in unsure),
        )
