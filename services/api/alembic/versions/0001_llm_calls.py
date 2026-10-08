"""llm_calls cost ledger

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("route", sa.String(length=64), nullable=False),
        sa.Column("model_key", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("effort", sa.String(length=16), nullable=True),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=8), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("batch", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"])
    op.create_index(op.f("ix_llm_calls_route"), "llm_calls", ["route"])


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_calls_route"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_created_at"), table_name="llm_calls")
    op.drop_table("llm_calls")
