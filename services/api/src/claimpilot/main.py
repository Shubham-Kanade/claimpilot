"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from claimpilot import __version__, health, meta, problem
from claimpilot.api import batches, claims, demo, documents, ops, people, stats
from claimpilot.config import get_settings
from claimpilot.container import Container
from claimpilot.obs.logs import configure_logging
from claimpilot.obs.middleware import RequestContextMiddleware


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

        settings = get_settings()
        configure_logging(settings.log_format, settings.log_level)  # real server only, not tests
        built = await build_container(settings)
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
    settings = container.settings if container is not None else get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "X-Persona",
            "X-Sandbox",
            "Idempotency-Key",
            "Content-Type",
            "Last-Event-ID",
            "X-Request-ID",
        ],
        expose_headers=["Content-Type", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)  # outermost: the id covers CORS and errors too
    problem.install(app)
    for module in (health, meta, batches, claims, documents, people, stats, ops, demo):
        app.include_router(module.router)
    return app


app = create_app()
