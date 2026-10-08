"""Typed pipeline progress events and the bus that carries them from the worker to SSE clients.

The bus is an append-only list per batch, not a pub/sub channel: the worker appends, an SSE
client reads from an index. A client that connects late (or reconnects with ``Last-Event-ID``)
simply reads from where it left off, so no event is ever missed and no race exists between
"subscribe" and "publish".
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

EVENT_TTL_SECONDS = 24 * 3600
POLL_INTERVAL_S = 0.25
STREAM_TIMEOUT_S = 15 * 60


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str


class BatchStarted(_Event):
    type: Literal["batch_started"] = "batch_started"
    total: int


class DocumentExtracted(_Event):
    """A document has been read and categorised (the slow, parallel phase)."""

    type: Literal["document_extracted"] = "document_extracted"
    document_id: str
    filename: str
    position: int
    doc_type: str
    merchant: str | None
    total: float | None
    category: str
    category_confidence: float
    engine: str
    cached: bool
    cost_usd: float


class DocumentChecked(_Event):
    """Trust and policy checks finished for a document (in upload order)."""

    type: Literal["document_checked"] = "document_checked"
    document_id: str
    trust_score: int
    verdict: str
    findings: int


class DocumentFailed(_Event):
    type: Literal["document_failed"] = "document_failed"
    document_id: str
    filename: str
    error: str


class ClaimsReady(_Event):
    type: Literal["claims_ready"] = "claims_ready"
    claim_ids: list[str]


class BatchDone(_Event):
    type: Literal["batch_done"] = "batch_done"
    processed: int
    failed: int
    claims: int
    cost_usd: float


class BatchFailed(_Event):
    type: Literal["batch_failed"] = "batch_failed"
    error: str


AnyEvent = (
    BatchStarted
    | DocumentExtracted
    | DocumentChecked
    | DocumentFailed
    | ClaimsReady
    | BatchDone
    | BatchFailed
)
PipelineEvent = Annotated[AnyEvent, Field(discriminator="type")]
_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(PipelineEvent)
TERMINAL_TYPES = frozenset({"batch_done", "batch_failed"})


def encode(event: AnyEvent) -> str:
    return event.model_dump_json()


def decode(raw: str | bytes) -> AnyEvent:
    return _ADAPTER.validate_json(raw)


class EventBus(Protocol):
    async def publish(self, event: AnyEvent) -> None: ...

    async def read(self, batch_id: str, start: int = 0) -> list[AnyEvent]: ...


class InMemoryEventBus:
    def __init__(self) -> None:
        self._events: dict[str, list[AnyEvent]] = defaultdict(list)

    async def publish(self, event: AnyEvent) -> None:
        self._events[event.batch_id].append(event)

    async def read(self, batch_id: str, start: int = 0) -> list[AnyEvent]:
        return list(self._events[batch_id][start:])


class RedisLike(Protocol):
    """The subset of ``redis.asyncio.Redis`` the bus needs."""

    async def rpush(self, name: str, *values: str) -> int: ...

    async def expire(self, name: str, time: int) -> bool: ...

    async def lrange(self, name: str, start: int, end: int) -> list[bytes | str]: ...


class RedisEventBus:
    """Events live in a Redis list per batch (``RPUSH`` / ``LRANGE``), expiring after a day."""

    def __init__(self, redis: RedisLike) -> None:
        self._redis = redis

    @staticmethod
    def _key(batch_id: str) -> str:
        return f"batch:{batch_id}:events"

    async def publish(self, event: AnyEvent) -> None:
        key = self._key(event.batch_id)
        await self._redis.rpush(key, encode(event))
        await self._redis.expire(key, EVENT_TTL_SECONDS)

    async def read(self, batch_id: str, start: int = 0) -> list[AnyEvent]:
        raw = await self._redis.lrange(self._key(batch_id), start, -1)
        return [decode(item) for item in raw]


async def stream_events(
    bus: EventBus,
    batch_id: str,
    *,
    start: int = 0,
    poll_interval_s: float = POLL_INTERVAL_S,
    timeout_s: float = STREAM_TIMEOUT_S,
    heartbeat_s: float | None = None,
) -> AsyncIterator[tuple[int, AnyEvent] | None]:
    """Yield ``(index, event)`` from ``start`` until a terminal event (or the timeout).

    With ``heartbeat_s`` set, yields ``None`` whenever that long passes without an event, so the
    HTTP layer can send a keep-alive and proxies do not close an idle connection.
    """
    index = start
    now = time.monotonic()
    deadline, last_sent = now + timeout_s, now
    while time.monotonic() < deadline:
        for event in await bus.read(batch_id, index):
            yield index, event
            index += 1
            last_sent = time.monotonic()
            if event.type in TERMINAL_TYPES:
                return
        if heartbeat_s is not None and time.monotonic() - last_sent >= heartbeat_s:
            yield None
            last_sent = time.monotonic()
        await asyncio.sleep(poll_interval_s)
