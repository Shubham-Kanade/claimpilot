"""ORM tables. Schema changes need an Alembic migration (never ``create_all`` outside tests)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from claimpilot.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LlmCall(Base):
    """Cost ledger: one row per LLM call, successful or failed (ADR-005)."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    route: Mapped[str] = mapped_column(String(64), index=True)
    model_key: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str | None] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(14, 8, asdecimal=False), default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    batch: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(500))
