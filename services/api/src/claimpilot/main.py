"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from claimpilot import __version__, health, meta


def create_app() -> FastAPI:
    app = FastAPI(
        title="ClaimPilot API",
        version=__version__,
        description="AI expense & reimbursement agent: from a pile of receipts to ready-to-submit.",
    )
    app.include_router(health.router)
    app.include_router(meta.router)
    return app


app = create_app()
