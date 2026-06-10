"""widen workflow step status columns

Revision ID: 20260610_0048
Revises: 20260610_0047
Create Date: 2026-06-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260610_0048"
down_revision: str | None = "20260610_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _widen_status_column("custom_story_workflow_steps")
    _widen_status_column("story_steps")


def downgrade() -> None:
    # Keep the wider status columns. Older narrow columns cannot safely store
    # SUBMITTED_BATCH_JOB, which is used by delayed/batch workflows.
    pass


def _widen_status_column(table_name: str) -> None:
    if not _column_exists(table_name, "status"):
        return
    op.alter_column(
        table_name,
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=32),
        existing_nullable=False,
        nullable=False,
    )


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
