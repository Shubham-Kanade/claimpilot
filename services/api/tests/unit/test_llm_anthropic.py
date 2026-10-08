"""AnthropicLLM against the real SDK with a stub HTTP transport (no network, no key)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from claimpilot.config import Settings
from claimpilot.llm.anthropic_llm import AnthropicLLM
from claimpilot.llm.errors import (
    LLMAPIError,
    LLMAuthError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequestError,
    LLMTruncatedError,
    LLMUnavailableError,
    map_sdk_error,
)
from claimpilot.llm.params import REFUSAL_FALLBACK_BETA
from claimpilot.llm.registry import ModelRegistry

SYSTEM = "You extract receipt totals."
Handler = Callable[[httpx2.Request], httpx2.Response]


class ReceiptTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: float


def message(
    *,
    text: str | None = '{"total": 120.5}',
    stop_reason: str = "end_turn",
    model: str = "claude-haiku-5-5",
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_details": stop_details,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 150,
            "output_tokens": 20,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": None,
        },
    }


class StubAPI:
    """Records requests and answers with a canned response."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else message()
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body, headers={"request-id": "req_123"})

    @property
    def last_json(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def make_llm(registry: ModelRegistry, handler: Handler) -> AnthropicLLM:
    client = anthropic.AsyncAnthropic(
        api_key="sk-test-not-a-real-key",  # pragma: allowlist secret
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicLLM(registry, client)


async def parse(llm: AnthropicLLM, route: str = "extraction"):
    return await llm.parse(
        route, system=SYSTEM, content="Total: Rs 120.50", output_model=ReceiptTotal
    )


async def test_success_parses_output_and_usage(models_registry):
    api = StubAPI()
    result = await parse(make_llm(models_registry, api))
    assert result.parsed == ReceiptTotal(total=120.5)
    assert result.mode == "live"
    assert result.usage.model_dump() == {
        "input_tokens": 150,
        "output_tokens": 20,
        "cache_read_tokens": 1000,
        "cache_write_tokens": 0,
    }
    assert result.cost_usd == pytest.approx(
        models_registry.estimate_cost(
            "haiku", input_tokens=150, output_tokens=20, cache_read_tokens=1000
        )
    )
    assert result.latency_ms >= 0


async def test_sends_the_shim_body_on_the_ga_endpoint(models_registry):
    api = StubAPI()
    llm = make_llm(models_registry, api)
    await parse(llm)
    request = api.requests[0]
    assert request.url.path == "/v1/messages"
    assert "anthropic-beta" not in request.headers
    expected = llm.build(
        "extraction", system=SYSTEM, content="Total: Rs 120.50", output_model=ReceiptTotal
    )
    assert api.last_json == expected.body
    assert "sk-test" not in request.content.decode()  # the key travels only in a header


async def test_fallback_models_use_the_beta_endpoint_with_header(models_registry):
    api = StubAPI(body=message(model="claude-sonnet-5-5"))
    await parse(make_llm(models_registry, api), route="agent_chat")
    request = api.requests[0]
    assert REFUSAL_FALLBACK_BETA in request.headers["anthropic-beta"]
    assert api.last_json["fallbacks"] == "default"


async def test_refusal_raises_typed_error_with_category(models_registry):
    api = StubAPI(
        body=message(
            text=None,
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": None},
        )
    )
    with pytest.raises(LLMRefusalError) as excinfo:
        await parse(make_llm(models_registry, api))
    assert excinfo.value.category == "cyber"
    assert excinfo.value.call is not None
    assert excinfo.value.call.usage.input_tokens == 150


async def test_max_tokens_raises_truncated_not_a_pydantic_error(models_registry):
    api = StubAPI(body=message(text='{"total": 12', stop_reason="max_tokens"))
    with pytest.raises(LLMTruncatedError):
        await parse(make_llm(models_registry, api))


@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [
        (400, "invalid_request_error", LLMRequestError),
        (401, "authentication_error", LLMAuthError),
        (402, "billing_error", LLMAuthError),
        (403, "permission_error", LLMAuthError),
        (404, "not_found_error", LLMRequestError),
        (413, "request_too_large", LLMRequestError),
        (429, "rate_limit_error", LLMRateLimitError),
        (500, "api_error", LLMUnavailableError),
        (529, "overloaded_error", LLMUnavailableError),
        (409, "conflict_error", LLMAPIError),
    ],
)
async def test_http_errors_map_to_typed_errors(models_registry, status, error_type, expected):
    body = {"type": "error", "error": {"type": error_type, "message": "nope"}}
    with pytest.raises(expected) as excinfo:
        await parse(make_llm(models_registry, StubAPI(status=status, body=body)))
    err = excinfo.value
    assert type(err) is expected
    assert err.status_code == status
    assert err.request_id == "req_123"
    assert err.retryable == (status in (429, 500, 529))
    assert err.call is not None
    assert err.call.error and error_type in err.call.error


async def test_connection_errors_are_retryable_unavailable(models_registry):
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom", request=request)

    with pytest.raises(LLMUnavailableError) as excinfo:
        await parse(make_llm(models_registry, broken))
    assert excinfo.value.retryable


def test_unknown_sdk_errors_map_to_base_api_error():
    err = map_sdk_error(
        anthropic.APIError("weird", request=httpx2.Request("POST", "http://x"), body=None)
    )
    assert type(err) is LLMAPIError


async def test_count_tokens_uses_the_count_endpoint(models_registry):
    api = StubAPI(body={"input_tokens": 77})
    llm = make_llm(models_registry, api)
    tokens = await llm.count_tokens(
        "agent_chat", system=SYSTEM, content="hi", output_model=ReceiptTotal
    )
    assert tokens == 77
    assert api.requests[0].url.path == "/v1/messages/count_tokens"
    sent = api.last_json
    assert "fallbacks" not in sent
    assert "max_tokens" not in sent
    assert "effort" not in sent["output_config"]


async def test_count_tokens_errors_are_mapped(models_registry):
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    llm = make_llm(models_registry, StubAPI(status=429, body=body))
    with pytest.raises(LLMRateLimitError):
        await llm.count_tokens("extraction", system=SYSTEM, content="hi")


def test_from_settings_requires_a_key(models_registry):
    with pytest.raises(LLMAuthError, match="ANTHROPIC_API_KEY"):
        AnthropicLLM.from_settings(
            Settings(llm_mode="live", anthropic_api_key=None), models_registry
        )


def test_from_settings_refuses_outside_live_mode(models_registry):
    # Spend guard: even with a key, a non-live mode can never build the paid client.
    with pytest.raises(LLMAuthError, match="spend guard"):
        AnthropicLLM.from_settings(
            Settings(llm_mode="replay", anthropic_api_key=SecretStr("sk-test")), models_registry
        )


def test_from_settings_builds_a_client(models_registry):
    llm = AnthropicLLM.from_settings(
        Settings(llm_mode="live", anthropic_api_key=SecretStr("sk-test")), models_registry
    )
    assert llm.mode == "live"
