"""sandbox: one demo visitor's private copy of the data; llm_calls know their batch

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SANDBOXED = ("batches", "documents", "claims", "llm_calls")


def upgrade() -> None:
    for table in SANDBOXED:
        op.add_column(table, sa.Column("sandbox", sa.String(length=64), nullable=True))
        op.create_index(op.f(f"ix_{table}_sandbox"), table, ["sandbox"])
    op.add_column("llm_calls", sa.Column("batch_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_llm_calls_batch_id"), "llm_calls", ["batch_id"])


def downgrade() -> None:
    # Indexes first: SQLite refuses to drop a column that an index still uses.
    op.drop_index(op.f("ix_llm_calls_batch_id"), table_name="llm_calls")
    op.drop_column("llm_calls", "batch_id")
    for table in reversed(SANDBOXED):
        op.drop_index(op.f(f"ix_{table}_sandbox"), table_name=table)
        op.drop_column(table, "sandbox")
