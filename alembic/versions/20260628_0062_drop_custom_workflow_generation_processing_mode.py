"""drop custom workflow generation and processing mode columns

Revision ID: 20260628_0062
Revises: 20260624_0061
Create Date: 2026-06-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260628_0062"
down_revision: str | None = "20260624_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    for column_name in ("generation_mode", "processing_mode"):
        if _column_exists(table_name, column_name):
            op.drop_column(table_name, column_name)


def downgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    if not _column_exists(table_name, "generation_mode"):
        op.add_column(
            table_name,
            sa.Column("generation_mode", sa.String(length=32), nullable=False, server_default="INPUT_DRIVEN"),
        )
    if not _column_exists(table_name, "processing_mode"):
        op.add_column(
            table_name,
            sa.Column("processing_mode", sa.String(length=32), nullable=False, server_default="delayed"),
        )


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
