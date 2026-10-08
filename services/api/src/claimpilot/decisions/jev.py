"""System One engine: Jev by TypeSafe AI over HTTP (``POST /v1/systemone``).

Jev reads text/JSON only and returns typed answers with probabilities in one call (~100 ms to
a second), at $0.042 per million input tokens and free output (pricing page, Sep 2026). Wire
format per docs.typesafe.ai/api: Bearer auth; ``{state, model, questions}`` in,
``{model, answers, usage}`` out; 429/529 are retried with exponential backoff.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from claimpilot.config import Settings
from claimpilot.decisions.types import (
    Answer,
    DecisionAuthError,
    DecisionRequestError,
    DecisionResult,
    DecisionUnavailableError,
    Question,
)
from claimpilot.net import ssl_context

JEV_MODEL = "jev-latest"
JEV_INPUT_USD_PER_MTOK = 0.042  # output tokens are free ("too cheap to meter")
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 4
BASE_BACKOFF_S = 0.5

Sleep = Callable[[float], Awaitable[None]]


class _JevAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    choice: str | None = None
    score: float | None = None
    noul: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = {}


class _JevUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0


class _JevResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answers: dict[str, _JevAnswer]
    usage: _JevUsage = _JevUsage()


class JevEngine:
    name = "jev"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.typesafe.ai",
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        timeout_s: float = 30.0,
    ) -> None:
        self._key = api_key
        self._url = base_url.rstrip("/") + "/v1/systemone"
        self._client = client or httpx.AsyncClient(timeout=timeout_s, verify=ssl_context())
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> JevEngine:
        if settings.jev_api_key is None:
            raise DecisionAuthError("DECISION_ENGINE=jev needs JEV_API_KEY")
        return cls(settings.jev_api_key.get_secret_value(), base_url=settings.jev_base_url)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def decide(
        self, state: Mapping[str, Any] | str, questions: Sequence[Question]
    ) -> DecisionResult:
        body = {
            "state": state if isinstance(state, str) else dict(state),
            "model": JEV_MODEL,
            "questions": {q.key: q.to_jev() for q in questions},
        }
        started = time.perf_counter()
        response = await self._post(body)
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed = _JevResponse.model_validate(response)
        answers = {q.key: self._answer(q, parsed.answers) for q in questions}
        return DecisionResult(
            answers=answers,
            engine=self.name,
            latency_ms=latency_ms,
            input_tokens=parsed.usage.input_tokens,
            output_tokens=parsed.usage.output_tokens,
            cost_usd=parsed.usage.input_tokens * JEV_INPUT_USD_PER_MTOK / 1_000_000,
        )

    async def _post(self, body: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self._key}"}
        last = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            retry_after = 0.0
            try:
                resp = await self._client.post(self._url, json=body, headers=headers)
            except httpx.TransportError as exc:  # timeouts, resets, DNS, TLS
                last = type(exc).__name__
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 401:
                    raise DecisionAuthError("Jev rejected the API key (401)")
                if resp.status_code not in RETRYABLE_STATUS:
                    raise DecisionRequestError(f"Jev {resp.status_code}: {resp.text[:300]}")
                last = f"HTTP {resp.status_code}"
                retry_after = _retry_after(resp)
            if attempt < MAX_ATTEMPTS - 1:
                backoff = BASE_BACKOFF_S * 2**attempt * (0.5 + random.random())
                await self._sleep(max(retry_after, backoff))
        raise DecisionUnavailableError(f"Jev unavailable after {MAX_ATTEMPTS} attempts ({last})")

    def _answer(self, question: Question, answers: dict[str, _JevAnswer]) -> Answer:
        raw = answers.get(question.key)
        if raw is None:
            raise DecisionRequestError(f"Jev returned no answer for '{question.key}'")
        if question.kind == "choice" and raw.choice is not None:
            return Answer(
                key=question.key,
                kind="choice",
                value=raw.choice,
                confidence=_unit(raw.confidence),
                probabilities=raw.probabilities,
                engine=self.name,
            )
        if question.kind == "score" and raw.score is not None:
            return Answer(
                key=question.key,
                kind="score",
                value=raw.score,
                confidence=_unit(raw.confidence),
                probabilities=raw.probabilities,
                engine=self.name,
            )
        if question.kind == "noul" and raw.noul is not None:
            p = _unit(raw.noul)
            return Answer(
                key=question.key,
                kind="noul",
                value=p,
                confidence=max(p, 1 - p),
                engine=self.name,
            )
        raise DecisionRequestError(f"Jev answer for '{question.key}' does not match its type")


def _unit(value: float | None) -> float:
    return min(1.0, max(0.0, 0.0 if value is None else float(value)))


def _retry_after(resp: httpx.Response) -> float:
    """Honour a numeric ``Retry-After`` (capped); otherwise no extra wait here."""
    try:
        return min(float(resp.headers.get("retry-after", 0)), 10.0)
    except ValueError:
        return 0.0
