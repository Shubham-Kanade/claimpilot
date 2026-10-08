"""Production wiring: build the collaborators from the settings.

Imported lazily (API startup, worker startup), so unit tests that build their own container with
fakes never need Redis, Arq or the MCP servers.
"""

from __future__ import annotations

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
from claimpilot.llm.client import LLMClient, get_llm
from claimpilot.llm.ledger import CostLedger
from claimpilot.mcp.adapters import McpPorts, get_mcp_ports
from claimpilot.pipeline.dupindex import DbDuplicateIndex
from claimpilot.pipeline.events import RedisEventBus
from claimpilot.pipeline.process import PipelineDeps
from claimpilot.pipeline.repo import Repository
from claimpilot.policy import Policy
from claimpilot.storage import LocalStorage


@dataclass
class _Infra:
    sessions: SessionFactory
    redis: Redis
    repo: Repository
    llm: LLMClient
    ports: McpPorts
    closers: list[Closer] = field(default_factory=list)


async def _infra(settings: Settings) -> _Infra:
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url)
    return _Infra(
        sessions=sessions,
        redis=redis,
        repo=Repository(sessions),
        llm=get_llm(settings, ledger=CostLedger(sessions)),
        ports=get_mcp_ports(settings),
        closers=[engine.dispose, redis.aclose],
    )


async def build_container(settings: Settings) -> Container:
    """The API process: it accepts uploads and enqueues them for the worker."""
    infra = await _infra(settings)
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


async def build_pipeline_deps(settings: Settings) -> tuple[PipelineDeps, Closer]:
    """The worker process: everything the pipeline needs, plus one function to close it all."""
    infra = await _infra(settings)

    async def close() -> None:
        for closer in reversed(infra.closers):
            await closer()

    deps = PipelineDeps(
        repo=infra.repo,
        storage=LocalStorage(settings.upload_dir),
        events=RedisEventBus(infra.redis),
        extractor=ReceiptExtractor(infra.llm, cache=RedisExtractionCache(infra.redis)),
        decisions=get_engine(settings, infra.llm),
        policy=Policy.load(),
        directory=infra.ports.directory,
        calendar=infra.ports.calendar,
        index=DbDuplicateIndex(infra.sessions),
        settings=settings,
    )
    return deps, close
