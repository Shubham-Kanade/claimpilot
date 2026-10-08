"""Arq worker entrypoint: ``arq claimpilot.worker.WorkerSettings``.

``process_batch`` is the job the API enqueues after an upload; it runs the receipt pipeline
(``claimpilot.pipeline.process``). The collaborators are built once at startup and shared by
every job, then closed on shutdown.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from claimpilot.config import get_settings
from claimpilot.pipeline.process import process_batch as run_pipeline


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job proving the queue round-trips."""
    return "pong"


async def startup(ctx: dict[str, Any]) -> None:
    from claimpilot.wiring import build_pipeline_deps  # heavy imports only in the worker

    ctx["deps"], ctx["close"] = await build_pipeline_deps(get_settings())


async def shutdown(ctx: dict[str, Any]) -> None:
    if close := ctx.get("close"):
        await close()


async def process_batch(ctx: dict[str, Any], batch_id: str) -> None:
    await run_pipeline(ctx["deps"], batch_id)


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, process_batch]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 900  # a full batch of 30 receipts
    max_tries = 2  # process_batch records its own failures; this only covers a crashed worker
