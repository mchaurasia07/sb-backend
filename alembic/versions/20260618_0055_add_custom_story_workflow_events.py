"""add custom story workflow events

Revision ID: 20260618_0055
Revises: 20260617_0054
Create Date: 2026-06-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260618_0055"
down_revision: str | None = "20260617_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_table_if_missing(
        "custom_story_workflow_events",
        sa.Column("workflow_id", sa.CHAR(36), nullable=False),
        sa.Column(
            "step_name",
            sa.Enum(
                "STORY_PLAN_GENERATION",
                "STORY_PLAN_VALIDATION",
                "STORY_GENERATION",
                "IMAGE_PLAN_GENERATION",
                "IMAGE_PLAN_VALIDATION",
                "IMAGE_GENERATION",
                "NARRATION_GENERATION",
                "PUBLISH_STORY",
                name="customstoryworkflowstep",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                name="customstoryworkfloweventstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["custom_story_workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing(
        "ix_custom_story_workflow_events_workflow_id",
        "custom_story_workflow_events",
        ["workflow_id"],
    )
    _create_index_if_missing(
        "ix_custom_story_workflow_events_status_created",
        "custom_story_workflow_events",
        ["status", "created_at"],
    )
    _create_index_if_missing(
        "ix_custom_story_workflow_events_workflow_step_status",
        "custom_story_workflow_events",
        ["workflow_id", "step_name", "status"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_custom_story_workflow_events_workflow_step_status", "custom_story_workflow_events")
    _drop_index_if_exists("ix_custom_story_workflow_events_status_created", "custom_story_workflow_events")
    _drop_index_if_exists("ix_custom_story_workflow_events_workflow_id", "custom_story_workflow_events")
    _drop_table_if_exists("custom_story_workflow_events")


def _create_table_if_missing(table_name: str, *columns, **kwargs) -> None:
    if _table_exists(table_name):
        return
    op.create_table(table_name, *columns, **kwargs)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _table_exists(table_name) or _index_exists(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _table_exists(table_name) and _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_table_if_exists(table_name: str) -> None:
    if _table_exists(table_name):
        op.drop_table(table_name)


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))
