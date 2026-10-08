"""RecordReplayLLM: record through a stub backend, replay offline, miss loudly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from claimpilot.llm.base import BaseLLM
from claimpilot.llm.errors import LLMRefusalError, ReplayMissError
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.replay import RecordReplayLLM
from claimpilot.llm.types import Completion, TokenUsage

SYSTEM = "You extract receipt totals."


class ReceiptTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: float


class StubLive(BaseLLM):
    """Stands in for AnthropicLLM: counts calls, never touches the network."""

    def __init__(self, registry: ModelRegistry, completion: Completion) -> None:
        super().__init__(registry, mode="live")
        self.completion = completion
        self.calls = 0
        self.counts = 0

    async def complete(self, request: LLMRequest) -> Completion:
        self.calls += 1
        return self.completion

    async def count(self, request: LLMRequest) -> int:
        self.counts += 1
        return 321


OK = Completion(
    text='{"total": 120.5}',
    stop_reason="end_turn",
    model_id="claude-haiku-5-5",
    usage=TokenUsage(input_tokens=200, output_tokens=12, cache_read_tokens=5),
    latency_ms=840,
)


async def parse(llm: BaseLLM, content: str = "Total: Rs 120.50"):
    return await llm.parse("extraction", system=SYSTEM, content=content, output_model=ReceiptTotal)


async def test_record_then_replay_round_trip(models_registry, tmp_path: Path):
    live = StubLive(models_registry, OK)
    recorder = RecordReplayLLM(models_registry, tmp_path, inner=live)
    recorded = await parse(recorder)
    assert recorder.mode == "live"
    assert recorded.mode == "live"
    assert live.calls == 1

    replayer = RecordReplayLLM(models_registry, tmp_path)
    replayed = await parse(replayer)
    assert replayer.mode == "replay"
    assert replayed.mode == "replay"
    assert replayed.parsed == recorded.parsed == ReceiptTotal(total=120.5)
    assert (replayed.usage, replayed.cost_usd, replayed.latency_ms) == (
        recorded.usage,
        recorded.cost_usd,
        recorded.latency_ms,
    )
    assert replayed.request_hash == recorded.request_hash


async def test_recording_is_once_hits_never_go_live(models_registry, tmp_path):
    live = StubLive(models_registry, OK)
    recorder = RecordReplayLLM(models_registry, tmp_path, inner=live)
    await parse(recorder)
    second = await parse(recorder)
    assert live.calls == 1
    assert second.mode == "replay"


async def test_recording_file_is_keyed_by_hash_and_holds_no_request_body(models_registry, tmp_path):
    recorder = RecordReplayLLM(models_registry, tmp_path, inner=StubLive(models_registry, OK))
    result = await parse(recorder)
    files = list(tmp_path.glob("*.json"))
    assert [f.name for f in files] == [f"{result.request_hash}.json"]
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["route"] == "extraction"
    assert data["output_model"] == "ReceiptTotal"
    assert "replayed" not in data["completion"]
    assert "Rs 120.50" not in files[0].read_text(encoding="utf-8")  # no prompt content stored


async def test_replay_miss_is_a_helpful_error(models_registry, tmp_path):
    with pytest.raises(ReplayMissError, match="LLM_RECORD=1"):
        await parse(RecordReplayLLM(models_registry, tmp_path))


async def test_changed_request_misses(models_registry, tmp_path):
    await parse(RecordReplayLLM(models_registry, tmp_path, inner=StubLive(models_registry, OK)))
    with pytest.raises(ReplayMissError):
        await parse(RecordReplayLLM(models_registry, tmp_path), content="Total: Rs 99")


async def test_refusals_are_recorded_and_replayed_as_refusals(models_registry, tmp_path):
    refusal = Completion(
        text=None, stop_reason="refusal", stop_category="cyber", model_id="claude-haiku-5-5"
    )
    recorder = RecordReplayLLM(models_registry, tmp_path, inner=StubLive(models_registry, refusal))
    with pytest.raises(LLMRefusalError):
        await parse(recorder)
    with pytest.raises(LLMRefusalError) as excinfo:
        await parse(RecordReplayLLM(models_registry, tmp_path))
    assert excinfo.value.category == "cyber"


async def test_count_tokens_record_and_replay(models_registry, tmp_path):
    live = StubLive(models_registry, OK)
    recorder = RecordReplayLLM(models_registry, tmp_path, inner=live)
    kwargs = {"system": SYSTEM, "content": "Total: 1", "output_model": ReceiptTotal}
    assert await recorder.count_tokens("extraction", **kwargs) == 321
    assert (
        await RecordReplayLLM(models_registry, tmp_path).count_tokens("extraction", **kwargs) == 321
    )
    assert live.counts == 1
    with pytest.raises(ReplayMissError):
        await RecordReplayLLM(models_registry, tmp_path).count_tokens(
            "extraction", system=SYSTEM, content="other"
        )


async def test_replay_can_take_a_fraction_of_the_recorded_time(
    models_registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    naps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        naps.append(seconds)

    from claimpilot.llm import replay as replay_module

    monkeypatch.setattr(replay_module.asyncio, "sleep", fake_sleep)

    async def ask(client: RecordReplayLLM) -> None:
        await client.parse(
            "extraction", system=SYSTEM, content="Total: 120.50", output_model=ReceiptTotal
        )

    await ask(RecordReplayLLM(models_registry, tmp_path, inner=StubLive(models_registry, OK)))
    await ask(RecordReplayLLM(models_registry, tmp_path))
    assert naps == []  # the default replays at once

    await ask(RecordReplayLLM(models_registry, tmp_path, latency_scale=0.5))
    assert naps == [pytest.approx(0.42)]  # half of the 840 ms the call took when it was recorded
