"""FakeLLM and the shared BaseLLM flow (stop-reason checks, cost, ledger hand-off)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from claimpilot.llm.errors import (
    FakeMissError,
    LLMOutputError,
    LLMRefusalError,
    LLMTruncatedError,
)
from claimpilot.llm.fake import FakeLLM
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import CallInfo, Completion, TokenUsage

SYSTEM = "You extract receipt totals."


class ReceiptTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: float


class Defaults(BaseModel):
    note: str = "n/a"


class MemoryLedger:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[CallInfo] = []
        self.fail = fail

    async def record(self, call: CallInfo) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.calls.append(call)


@pytest.fixture
def ledger() -> MemoryLedger:
    return MemoryLedger()


@pytest.fixture
def fake(models_registry: ModelRegistry, ledger: MemoryLedger) -> FakeLLM:
    return FakeLLM(models_registry, ledger=ledger, env={})


def completion(**kwargs) -> Completion:
    return Completion(**{"text": None, "model_id": "claude-haiku-5-5", **kwargs})


async def test_registered_instance_is_returned_with_accounting(fake, ledger, models_registry):
    fake.register(ReceiptTotal, ReceiptTotal(total=120.5))
    result = await fake.parse(
        "extraction", system=SYSTEM, content="Total: Rs 120.50", output_model=ReceiptTotal
    )
    assert result.parsed == ReceiptTotal(total=120.5)
    assert (result.route, result.model_key, result.model_id) == (
        "extraction",
        "haiku",
        "claude-haiku-5-5",
    )
    assert result.mode == "fake"
    assert result.effort == "low"
    assert result.stop_reason == "end_turn"
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.cost_usd == pytest.approx(
        models_registry.estimate_cost(
            "haiku",
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )
    )
    assert result.request_hash == fake.requests[0].request_hash
    assert ledger.calls == [result]


async def test_responses_are_deterministic(fake):
    fake.register(ReceiptTotal, {"total": 1.0})
    a = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    b = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert (a.parsed, a.usage, a.request_hash) == (b.parsed, b.usage, b.request_hash)


async def test_route_specific_registration_wins(fake):
    fake.register(ReceiptTotal, {"total": 1.0})
    fake.register(ReceiptTotal, {"total": 2.0}, route="extraction_retry")
    retry = await fake.parse(
        "extraction_retry", system=SYSTEM, content="x", output_model=ReceiptTotal
    )
    first = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert (retry.parsed.total, first.parsed.total) == (2.0, 1.0)
    assert retry.model_key == "sonnet"


async def test_callable_responder_sees_the_built_request(fake):
    def responder(request: LLMRequest) -> ReceiptTotal:
        text = request.body["messages"][0]["content"][0]["text"]
        return ReceiptTotal(total=float(text.split()[-1]))

    fake.register(ReceiptTotal, responder)
    result = await fake.parse(
        "extraction",
        system=SYSTEM,
        content=[{"type": "text", "text": "Total 42.5"}],
        output_model=ReceiptTotal,
    )
    assert result.parsed.total == 42.5


async def test_unregistered_model_with_defaults_is_served(fake):
    result = await fake.parse("extraction", system=SYSTEM, content="x", output_model=Defaults)
    assert result.parsed == Defaults()


async def test_unregistered_model_without_defaults_is_a_helpful_miss(fake, ledger):
    with pytest.raises(FakeMissError, match=r"FakeLLM\.register"):
        await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert ledger.calls[0].error is not None
    assert ledger.calls[0].cost_usd == 0


async def test_requests_without_output_model_are_rejected(fake, models_registry):
    request = fake.build("extraction", system=SYSTEM, content="x")
    with pytest.raises(FakeMissError):
        await fake.complete(request)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (completion(stop_reason="refusal", stop_category="cyber"), LLMRefusalError),
        (completion(text='{"total": 1', stop_reason="max_tokens"), LLMTruncatedError),
        (completion(stop_reason="model_context_window_exceeded"), LLMTruncatedError),
        (completion(stop_reason="end_turn"), LLMOutputError),
        (completion(text='{"total": "abc"}', stop_reason="end_turn"), LLMOutputError),
    ],
)
async def test_bad_stop_reasons_and_outputs_raise_typed_errors(fake, ledger, raw, error):
    raw = raw.model_copy(update={"usage": TokenUsage(input_tokens=100, output_tokens=50)})
    fake.register(ReceiptTotal, raw)
    with pytest.raises(error) as excinfo:
        await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    call = excinfo.value.call
    assert call is not None
    assert call.stop_reason == raw.stop_reason
    assert call.cost_usd > 0  # failed calls still cost money and are still accounted for
    assert ledger.calls == [call]
    assert call.error and call.error.startswith(error.__name__)


async def test_refusal_carries_category(fake):
    fake.register(ReceiptTotal, completion(stop_reason="refusal", stop_category="bio"))
    with pytest.raises(LLMRefusalError) as excinfo:
        await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert excinfo.value.category == "bio"


async def test_fallback_served_call_is_priced_by_the_serving_model(fake, models_registry):
    usage = TokenUsage(input_tokens=1000, output_tokens=100)
    fake.register(
        ReceiptTotal,
        Completion(
            text='{"total": 1}', stop_reason="end_turn", model_id="claude-opus-5-5", usage=usage
        ),
        route="agent_chat",
    )
    result = await fake.parse("agent_chat", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert result.model_key == "opus"
    assert result.cost_usd == pytest.approx(
        models_registry.estimate_cost("opus", input_tokens=1000, output_tokens=100)
    )


async def test_unknown_serving_model_is_priced_as_requested(fake):
    fake.register(
        ReceiptTotal,
        Completion(text='{"total": 1}', stop_reason="end_turn", model_id="claude-opus-4-8"),
    )
    result = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert (result.model_key, result.model_id) == ("haiku", "claude-opus-4-8")


async def test_ledger_failure_does_not_fail_the_call(models_registry):
    fake = FakeLLM(models_registry, ledger=MemoryLedger(fail=True), env={})
    fake.register(ReceiptTotal, {"total": 3.0})
    result = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert result.parsed.total == 3.0


async def test_works_without_a_ledger(models_registry):
    fake = FakeLLM(models_registry, env={})
    fake.register(ReceiptTotal, {"total": 3.0})
    result = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert result.parsed.total == 3.0


async def test_route_env_override_is_honoured(models_registry):
    fake = FakeLLM(models_registry, env={"ROUTE_EXTRACTION": "opus"})
    fake.register(ReceiptTotal, {"total": 3.0})
    result = await fake.parse("extraction", system=SYSTEM, content="x", output_model=ReceiptTotal)
    assert (result.model_key, result.model_id) == ("opus", "claude-opus-5-5")


async def test_thinking_off_reaches_the_shim(fake):
    fake.register(ReceiptTotal, {"total": 3.0})
    await fake.parse(
        "extraction", system=SYSTEM, content="x", output_model=ReceiptTotal, thinking="off"
    )
    assert fake.requests[0].body["thinking"] == {"type": "disabled"}


async def test_count_tokens_is_deterministic_and_free(fake, ledger):
    a = await fake.count_tokens("extraction", system=SYSTEM, content="Total: 1")
    b = await fake.count_tokens("extraction", system=SYSTEM, content="Total: 1")
    longer = await fake.count_tokens("extraction", system=SYSTEM, content="Total: 1" * 50)
    assert a == b > 0
    assert longer > a
    assert ledger.calls == []
