"""Liveness and readiness endpoints, plus dependency checks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from claimpilot import __version__
from claimpilot.config import get_settings

Check = Callable[[], Awaitable[None]]

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    version: str


class Readiness(BaseModel):
    status: str
    checks: dict[str, str]


async def check_redis() -> None:
    from redis.asyncio import Redis

    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    try:
        await client.ping()  # type: ignore[misc]  # redis stubs type ping() as sync|async
    finally:
        await client.aclose()


async def check_postgres() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(get_settings().database_url, connect_args={"timeout": 2})
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


DEFAULT_CHECKS: dict[str, Check] = {"redis": check_redis, "postgres": check_postgres}


@router.get("/healthz", response_model=Health)
async def healthz() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/readyz", response_model=Readiness)
async def readyz(request: Request, response: Response) -> Readiness:
    checks: dict[str, Check] = getattr(request.app.state, "readiness_checks", DEFAULT_CHECKS)

    async def run(check: Check) -> str:
        try:
            await asyncio.wait_for(check(), timeout=3)
        except Exception as exc:  # report any dependency failure, never crash the probe
            return f"error: {type(exc).__name__}"
        return "ok"

    results = dict(
        zip(checks, await asyncio.gather(*(run(c) for c in checks.values())), strict=True)
    )
    ready = all(v == "ok" for v in results.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ready" if ready else "degraded", checks=results)
