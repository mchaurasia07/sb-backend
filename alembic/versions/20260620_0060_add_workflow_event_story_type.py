"""add story type to custom workflow events

Revision ID: 20260620_0060
Revises: 20260618_0059
Create Date: 2026-06-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260620_0060"
down_revision: str | None = "20260618_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not _table_exists("custom_story_workflow_events"):
        return

    if not _column_exists("custom_story_workflow_events", "story_type"):
        op.add_column(
            "custom_story_workflow_events",
            sa.Column("story_type", sa.String(length=32), nullable=True, server_default="CUSTOM"),
        )
        op.execute(
            sa.text(
                """
                UPDATE custom_story_workflow_events AS e
                SET story_type = (
                    SELECT w.story_type
                    FROM custom_story_workflows AS w
                    WHERE w.id = e.workflow_id
                )
                WHERE e.workflow_id IN (
                    SELECT id FROM custom_story_workflows
                )
                """
            )
        )
        op.alter_column(
            "custom_story_workflow_events",
            "story_type",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="CUSTOM",
        )

    _create_index_if_missing(
        "ix_custom_story_workflow_events_story_type",
        "custom_story_workflow_events",
        ["story_type"],
    )


def downgrade() -> None:
    if _table_exists("custom_story_workflow_events"):
        _drop_index_if_exists("ix_custom_story_workflow_events_story_type", "custom_story_workflow_events")
        if _column_exists("custom_story_workflow_events", "story_type"):
            op.drop_column("custom_story_workflow_events", "story_type")


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
