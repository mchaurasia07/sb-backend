"""add custom workflow event retry metadata

Revision ID: 20260618_0057
Revises: 20260618_0056
Create Date: 2026-06-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260618_0057"
down_revision: str | None = "20260618_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not _table_exists("custom_story_workflow_events"):
        return
    columns = _columns("custom_story_workflow_events")
    status_type = columns.get("status", {}).get("type")
    status_length = getattr(status_type, "length", None)
    if status_length is not None and status_length < 32:
        op.alter_column(
            "custom_story_workflow_events",
            "status",
            existing_type=status_type,
            type_=sa.String(length=32),
            existing_nullable=False,
        )
    if "retry_flag" not in columns:
        op.add_column(
            "custom_story_workflow_events",
            sa.Column("retry_flag", sa.Boolean(), nullable=False, server_default="0"),
        )
    if "retry_comment" not in columns:
        op.add_column(
            "custom_story_workflow_events",
            sa.Column("retry_comment", sa.String(length=64), nullable=True),
        )
    if "retry_source_event_id" not in columns:
        op.add_column(
            "custom_story_workflow_events",
            sa.Column("retry_source_event_id", sa.CHAR(36), nullable=True),
        )


def downgrade() -> None:
    if not _table_exists("custom_story_workflow_events"):
        return
    columns = _columns("custom_story_workflow_events")
    if "retry_source_event_id" in columns:
        op.drop_column("custom_story_workflow_events", "retry_source_event_id")
    if "retry_comment" in columns:
        op.drop_column("custom_story_workflow_events", "retry_comment")
    if "retry_flag" in columns:
        op.drop_column("custom_story_workflow_events", "retry_flag")


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _columns(table_name: str) -> dict[str, dict]:
    bind = op.get_bind()
    return {column["name"]: column for column in sa.inspect(bind).get_columns(table_name)}
