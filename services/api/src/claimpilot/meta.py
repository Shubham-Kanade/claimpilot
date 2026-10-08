"""`/v1/meta`: runtime config the UI shows (which model serves each route, the LLM mode)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from claimpilot.config import get_settings
from claimpilot.llm.registry import get_registry

router = APIRouter(prefix="/v1/meta", tags=["meta"])


class RouteInfo(BaseModel):
    route: str
    model_key: str
    model_id: str
    effort: str | None
    overridden: bool


class MetaInfo(BaseModel):
    llm_mode: str
    decision_engine: str
    routes: list[RouteInfo]


@router.get("", response_model=MetaInfo)
async def get_meta() -> MetaInfo:
    settings = get_settings()
    registry = get_registry()
    routes = []
    for name in registry.routes:
        resolved = registry.resolve(name)
        routes.append(
            RouteInfo(
                route=name,
                model_key=resolved.model.key,
                model_id=resolved.model.id,
                effort=resolved.effort,
                overridden=resolved.overridden,
            )
        )
    return MetaInfo(
        llm_mode=settings.llm_mode, decision_engine=settings.decision_engine, routes=routes
    )
