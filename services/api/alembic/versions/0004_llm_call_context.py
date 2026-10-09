"""llm_calls know their trace, document and claim; batches remember the request that made them

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LLM_COLUMNS = (("trace_id", 64), ("document_id", 36), ("claim_id", 64))


def upgrade() -> None:
    for name, length in LLM_COLUMNS:
        op.add_column("llm_calls", sa.Column(name, sa.String(length=length), nullable=True))
        op.create_index(op.f(f"ix_llm_calls_{name}"), "llm_calls", [name])
    op.add_column("batches", sa.Column("trace_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "trace_id")
    for name, _ in reversed(LLM_COLUMNS):  # indexes first: SQLite will not drop an indexed column
        op.drop_index(op.f(f"ix_llm_calls_{name}"), table_name="llm_calls")
        op.drop_column("llm_calls", name)
