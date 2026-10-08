"""Arq worker entrypoint: `arq claimpilot.worker.WorkerSettings`.

The receipt pipeline jobs (extract → decide → trust/policy → group) land here in M1/M2.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from claimpilot.config import get_settings


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job proving the queue round-trips."""
    return "pong"


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 300
