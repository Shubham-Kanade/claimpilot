"""Deterministic in-process backend for unit tests and ``LLM_MODE=fake`` ($0, no network)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from claimpilot.llm.base import BaseLLM, Ledger
from claimpilot.llm.errors import FakeMissError
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import Completion, TokenUsage

FakeResponse = BaseModel | Mapping[str, Any] | Completion
Responder = Callable[[LLMRequest], FakeResponse]

CHARS_PER_TOKEN = 4  # crude but deterministic; good enough for fake cost numbers


class FakeLLM(BaseLLM):
    """Responses are registered per output model, optionally narrowed to one route.

    A response may be a model instance, a plain dict, a full ``Completion`` (to simulate
    refusals or truncation), or a callable receiving the built ``LLMRequest``. Unregistered
    output models fall back to their all-defaults instance when one exists.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        ledger: Ledger | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(registry, mode="fake", ledger=ledger, env=env)
        self._responders: dict[tuple[str | None, type[BaseModel]], Responder] = {}
        self.requests: list[LLMRequest] = []  # every request seen, for assertions

    def register(
        self,
        output_model: type[BaseModel],
        response: FakeResponse | Responder,
        *,
        route: str | None = None,
    ) -> None:
        responder = response if callable(response) else _constant(response)
        self._responders[(route, output_model)] = responder

    async def complete(self, request: LLMRequest) -> Completion:
        self.requests.append(request)
        response = self._respond(request)
        if isinstance(response, Completion):
            return response
        text = (
            response.model_dump_json()
            if isinstance(response, BaseModel)
            else json.dumps(response, ensure_ascii=False)
        )
        return Completion(
            text=text,
            stop_reason="end_turn",
            model_id=request.body["model"],
            usage=TokenUsage(
                input_tokens=_tokens(request.canonical_json()), output_tokens=_tokens(text)
            ),
        )

    async def count(self, request: LLMRequest) -> int:
        return _tokens(json.dumps(request.count_tokens_body(), sort_keys=True))

    def _respond(self, request: LLMRequest) -> FakeResponse:
        model = request.output_model
        if model is None:
            raise FakeMissError("FakeLLM only serves structured-output requests")
        responder = self._responders.get((request.route, model)) or self._responders.get(
            (None, model)
        )
        if responder is not None:
            return responder(request)
        try:
            return model.model_validate({})
        except ValidationError:
            raise FakeMissError(
                f"no fake response for {model.__name__} on route '{request.route}'; "
                "call FakeLLM.register(...)"
            ) from None


def _constant(response: FakeResponse) -> Responder:
    return lambda _request: response


def _tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)
