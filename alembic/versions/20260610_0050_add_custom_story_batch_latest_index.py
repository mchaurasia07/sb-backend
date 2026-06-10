"""add custom story batch latest lookup index

Revision ID: 20260610_0050
Revises: 20260610_0049
Create Date: 2026-06-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260610_0050"
down_revision: str | None = "20260610_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_index_if_missing(
        "custom_story_batch_jobs",
        "ix_custom_story_batch_jobs_workflow_created_at",
        ["workflow_id", "created_at"],
    )
    _create_index_if_missing(
        "custom_story_batch_jobs",
        "ix_custom_story_batch_jobs_workflow_type_created_at",
        ["workflow_id", "job_type", "created_at"],
    )
    _create_index_if_missing(
        "custom_story_workflow_steps",
        "ix_custom_story_workflow_steps_workflow_created_at",
        ["workflow_id", "created_at"],
    )
    _create_index_if_missing(
        "custom_story_workflow_steps",
        "ix_custom_story_workflow_steps_workflow_step_created_at",
        ["workflow_id", "step_name", "created_at"],
    )


def downgrade() -> None:
    _drop_index_if_exists("custom_story_workflow_steps", "ix_custom_story_workflow_steps_workflow_step_created_at")
    _drop_index_if_exists("custom_story_workflow_steps", "ix_custom_story_workflow_steps_workflow_created_at")
    _drop_index_if_exists("custom_story_batch_jobs", "ix_custom_story_batch_jobs_workflow_type_created_at")
    _drop_index_if_exists("custom_story_batch_jobs", "ix_custom_story_batch_jobs_workflow_created_at")


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if _table_exists(table_name) and not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _table_exists(table_name) and _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))
