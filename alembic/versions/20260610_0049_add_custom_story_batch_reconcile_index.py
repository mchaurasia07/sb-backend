"""add custom story batch reconcile index

Revision ID: 20260610_0049
Revises: 20260610_0048
Create Date: 2026-06-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260610_0049"
down_revision: str | None = "20260610_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_name = "custom_story_batch_jobs"
    index_name = "ix_custom_story_batch_jobs_status_updated_at"
    if _table_exists(table_name) and not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, ["status", "updated_at"])


def downgrade() -> None:
    table_name = "custom_story_batch_jobs"
    index_name = "ix_custom_story_batch_jobs_status_updated_at"
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
