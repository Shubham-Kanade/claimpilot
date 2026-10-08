"""Production wiring: build the collaborators from the settings.

Imported lazily (API startup, worker startup), so unit tests that build their own container with
fakes never need Redis, Arq or the MCP servers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from claimpilot.config import Settings
from claimpilot.container import Closer, Container
from claimpilot.db import SessionFactory, create_engine, create_session_factory
from claimpilot.decisions import get_engine
from claimpilot.extraction import ReceiptExtractor
from claimpilot.extraction.cache import RedisExtractionCache
from claimpilot.extraction.locate import FieldLocator
from claimpilot.llm.client import LLMClient, get_llm
from claimpilot.llm.ledger import CostLedger
from claimpilot.mcp.adapters import McpPorts, get_mcp_ports
from claimpilot.pipeline.dupindex import DbDuplicateIndex
from claimpilot.pipeline.events import InMemoryEventBus, RedisEventBus
from claimpilot.pipeline.process import PipelineDeps
from claimpilot.pipeline.process import process_batch as run_pipeline
from claimpilot.pipeline.repo import Repository
from claimpilot.policy import Policy
from claimpilot.storage import LocalStorage

logger = logging.getLogger(__name__)


@dataclass
class _Infra:
    sessions: SessionFactory
    redis: Redis | None
    repo: Repository
    llm: LLMClient
    ports: McpPorts
    closers: list[Closer] = field(default_factory=list)


async def _infra(settings: Settings) -> _Infra:
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    embedded = settings.runtime == "embedded"
    redis: Redis | None = None if embedded else Redis.from_url(settings.redis_url)
    return _Infra(
        sessions=sessions,
        redis=redis,
        repo=Repository(sessions),
        llm=get_llm(settings, ledger=CostLedger(sessions)),
        ports=get_mcp_ports(settings),
        closers=[engine.dispose] if redis is None else [engine.dispose, redis.aclose],
    )


async def build_container(settings: Settings) -> Container:
    """The API process: it accepts uploads and enqueues them for the worker.

    With ``runtime=embedded`` there is no worker: the API runs each batch itself, in a background
    task (see ``_embedded``).
    """
    if settings.runtime == "embedded":
        return await _embedded(settings)
    infra = await _infra(settings)
    assert infra.redis is not None  # distributed runtime
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    async def enqueue(batch_id: str) -> None:
        await pool.enqueue_job("process_batch", batch_id)

    return Container(
        settings=settings,
        sessions=infra.sessions,
        repo=infra.repo,
        storage=LocalStorage(settings.upload_dir),
        events=RedisEventBus(infra.redis),
        enqueue=enqueue,
        directory=infra.ports.directory,
        finance=infra.ports.finance,
        calendar=infra.ports.calendar,
        llm=infra.llm,
        closers=[*infra.closers, pool.aclose],
    )


def _pipeline_deps(
    settings: Settings, infra: _Infra, events: InMemoryEventBus | RedisEventBus
) -> PipelineDeps:
    cache = RedisExtractionCache(infra.redis) if infra.redis is not None else None
    return PipelineDeps(
        repo=infra.repo,
        storage=LocalStorage(settings.upload_dir),
        events=events,
        extractor=ReceiptExtractor(infra.llm, cache=cache),
        decisions=get_engine(settings, infra.llm),
        policy=Policy.load(),
        directory=infra.ports.directory,
        calendar=infra.ports.calendar,
        index=DbDuplicateIndex(infra.sessions),
        settings=settings,
        locator=FieldLocator(infra.llm) if settings.locate_fields else None,
    )


async def build_pipeline_deps(settings: Settings) -> tuple[PipelineDeps, Closer]:
    """The worker process: everything the pipeline needs, plus one function to close it all."""
    infra = await _infra(settings)
    assert infra.redis is not None  # the worker only exists in the distributed runtime

    async def close() -> None:
        for closer in reversed(infra.closers):
            await closer()

    return _pipeline_deps(settings, infra, RedisEventBus(infra.redis)), close


async def _embedded(settings: Settings) -> Container:
    """One process for everything: the batch runs as a task of the API's own event loop.

    The event bus and the (absent) extraction cache live in memory, so a restart forgets running
    batches; that is fine for the single-container demo, whose database is a throw-away file too.
    """
    infra = await _infra(settings)
    events = InMemoryEventBus()
    deps = _pipeline_deps(settings, infra, events)
    running: set[asyncio.Task[None]] = set()

    async def enqueue(batch_id: str) -> None:
        task = asyncio.create_task(run_pipeline(deps, batch_id), name=f"batch-{batch_id}")
        running.add(task)
        task.add_done_callback(running.discard)

    async def drain() -> None:
        for task in list(running):
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    return Container(
        settings=settings,
        sessions=infra.sessions,
        repo=infra.repo,
        storage=deps.storage,
        events=events,
        enqueue=enqueue,
        directory=infra.ports.directory,
        finance=infra.ports.finance,
        calendar=infra.ports.calendar,
        llm=infra.llm,
        policy=deps.policy,
        closers=[*infra.closers, drain],
    )
