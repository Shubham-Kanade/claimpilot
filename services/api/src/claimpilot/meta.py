"""`/v1/meta`: runtime config the UI shows (which model serves each route, the LLM mode, the
upload limits)."""

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
    llm_record: bool  # live mode that replays what it has recorded and records the rest
    daily_llm_budget_usd: float  # the cap on live LLM spend per 24 h (0 = no cap)
    max_batch_files: int  # upload limits, so the UI checks against the server's real ones
    max_upload_mb: int
    decision_engine: str
    demo: bool = False  # the public demo: "start over" exists and receipts come from recordings
    runtime: str = "distributed"
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
        llm_mode=settings.llm_mode,
        llm_record=settings.llm_record,
        daily_llm_budget_usd=settings.daily_llm_budget_usd,
        max_batch_files=settings.max_batch_files,
        max_upload_mb=settings.max_upload_mb,
        decision_engine=settings.decision_engine,
        demo=settings.demo_mode,
        runtime=settings.runtime,
        routes=routes,
    )
