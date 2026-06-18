"""add selected languages to custom workflow table

Revision ID: 20260618_0059
Revises: 20260618_0058
Create Date: 2026-06-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260618_0059"
down_revision: str | None = "20260618_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if _table_exists("custom_story_workflows") and not _column_exists("custom_story_workflows", "languages"):
        op.add_column("custom_story_workflows", sa.Column("languages", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _table_exists("custom_story_workflows") and _column_exists("custom_story_workflows", "languages"):
        op.drop_column("custom_story_workflows", "languages")


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
