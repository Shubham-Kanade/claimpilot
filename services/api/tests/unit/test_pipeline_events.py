from __future__ import annotations

import asyncio

import pytest

from claimpilot.pipeline.events import (
    BatchDone,
    BatchFailed,
    BatchStarted,
    ClaimsReady,
    DocumentChecked,
    DocumentExtracted,
    DocumentFailed,
    InMemoryEventBus,
    RedisEventBus,
    decode,
    encode,
    stream_events,
)

EXTRACTED = DocumentExtracted(
    batch_id="b1",
    document_id="d1",
    filename="a.png",
    position=0,
    doc_type="cab_receipt",
    merchant="Zip Cabs",
    total=320.0,
    category="local_conveyance",
    category_confidence=0.93,
    engine="jev",
    cached=False,
    cost_usd=0.0004,
)


@pytest.mark.parametrize(
    "event",
    [
        BatchStarted(batch_id="b1", total=3),
        EXTRACTED,
        DocumentChecked(
            batch_id="b1", document_id="d1", trust_score=92, verdict="clean", findings=1
        ),
        DocumentFailed(batch_id="b1", document_id="d2", filename="x.png", error="unreadable"),
        ClaimsReady(batch_id="b1", claim_ids=["c1"]),
        BatchDone(batch_id="b1", processed=2, failed=1, claims=1, cost_usd=0.002),
        BatchFailed(batch_id="b1", error="boom"),
    ],
)
def test_every_event_round_trips_through_json(event):
    assert decode(encode(event)) == event


def test_decode_rejects_unknown_types():
    with pytest.raises(ValueError):
        decode('{"type": "nope", "batch_id": "b"}')


async def test_in_memory_bus_reads_from_an_offset():
    bus = InMemoryEventBus()
    for n in range(3):
        await bus.publish(BatchStarted(batch_id="b1", total=n))
    await bus.publish(BatchStarted(batch_id="b2", total=99))
    assert [e.total for e in await bus.read("b1")] == [0, 1, 2]  # type: ignore[union-attr]
    assert [e.total for e in await bus.read("b1", 2)] == [2]  # type: ignore[union-attr]
    assert await bus.read("none") == []


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}

    async def rpush(self, name: str, *values: str) -> int:
        self.lists.setdefault(name, []).extend(values)
        return len(self.lists[name])

    async def expire(self, name: str, time: int) -> bool:
        self.ttl[name] = time
        return True

    async def lrange(self, name: str, start: int, end: int) -> list[bytes | str]:
        items = self.lists.get(name, [])
        return [i.encode() for i in (items[start:] if end == -1 else items[start : end + 1])]


async def test_redis_bus_appends_with_ttl_and_reads_back():
    redis = FakeRedis()
    bus = RedisEventBus(redis)
    await bus.publish(BatchStarted(batch_id="b1", total=2))
    await bus.publish(EXTRACTED)
    assert redis.ttl["batch:b1:events"] == 24 * 3600
    events = await bus.read("b1")
    assert [e.type for e in events] == ["batch_started", "document_extracted"]
    assert [e.type for e in await bus.read("b1", 1)] == ["document_extracted"]


async def test_stream_yields_indexed_events_until_terminal():
    bus = InMemoryEventBus()
    await bus.publish(BatchStarted(batch_id="b1", total=1))

    async def producer():
        await asyncio.sleep(0.05)
        await bus.publish(EXTRACTED)
        await bus.publish(BatchDone(batch_id="b1", processed=1, failed=0, claims=1, cost_usd=0.0))
        await bus.publish(BatchStarted(batch_id="b1", total=42))  # after terminal: never delivered

    task = asyncio.create_task(producer())
    seen = [
        (item[0], item[1].type)
        async for item in stream_events(bus, "b1", poll_interval_s=0.01)
        if item
    ]
    await task
    assert seen == [(0, "batch_started"), (1, "document_extracted"), (2, "batch_done")]


async def test_stream_resumes_from_last_event_id():
    bus = InMemoryEventBus()
    for event in (
        BatchStarted(batch_id="b1", total=1),
        EXTRACTED,
        BatchDone(batch_id="b1", processed=1, failed=0, claims=0, cost_usd=0.0),
    ):
        await bus.publish(event)
    seen = [
        item[0] async for item in stream_events(bus, "b1", start=2, poll_interval_s=0.01) if item
    ]
    assert seen == [2]


async def test_stream_gives_up_after_the_timeout():
    bus = InMemoryEventBus()
    seen = [e async for e in stream_events(bus, "never", poll_interval_s=0.01, timeout_s=0.05)]
    assert seen == []


async def test_stream_sends_heartbeats_while_idle():
    bus = InMemoryEventBus()
    beats = []
    async for item in stream_events(
        bus, "idle", poll_interval_s=0.01, timeout_s=0.2, heartbeat_s=0.05
    ):
        beats.append(item)
    assert beats and all(b is None for b in beats)


async def test_the_in_memory_bus_forgets_the_oldest_batch_beyond_its_limit():
    bus = InMemoryEventBus(max_batches=2)
    for batch_id in ("a", "b", "c"):
        await bus.publish(BatchStarted(batch_id=batch_id, total=1))
    assert await bus.read("a") == []  # dropped: the oldest of three
    assert len(await bus.read("b")) == 1 and len(await bus.read("c")) == 1
    await bus.publish(BatchStarted(batch_id="c", total=2))  # an existing batch never evicts
    assert len(await bus.read("c")) == 2 and len(await bus.read("b")) == 1


async def test_reading_an_unknown_batch_does_not_create_it():
    bus = InMemoryEventBus(max_batches=1)
    assert await bus.read("nobody") == []
    await bus.publish(BatchStarted(batch_id="a", total=1))
    assert len(await bus.read("a")) == 1  # the read above did not take the only slot
