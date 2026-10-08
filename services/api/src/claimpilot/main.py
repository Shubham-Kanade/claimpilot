"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from claimpilot import __version__, health, meta, problem
from claimpilot.api import batches, claims, documents, people
from claimpilot.config import get_settings
from claimpilot.container import Container


def create_app(container: Container | None = None) -> FastAPI:
    """Build the app. Pass a ``container`` to inject fakes (tests); otherwise one is wired from
    the settings when the server starts."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            app.state.container = container
            yield
            return
        from claimpilot.wiring import build_container  # imports Redis/Arq/MCP only when serving

        built = await build_container(get_settings())
        app.state.container = built
        try:
            yield
        finally:
            await built.aclose()

    app = FastAPI(
        title="ClaimPilot API",
        version=__version__,
        description="AI expense & reimbursement agent: from a pile of receipts to ready-to-submit.",
        lifespan=lifespan,
    )
    if container is not None:
        app.state.container = container  # also available when the lifespan is not run (tests)
    problem.install(app)
    for module in (health, meta, batches, claims, documents, people):
        app.include_router(module.router)
    return app


app = create_app()
