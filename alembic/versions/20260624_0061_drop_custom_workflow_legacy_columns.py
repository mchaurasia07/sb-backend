"""drop legacy custom workflow columns

Revision ID: 20260624_0061
Revises: 20260620_0060
Create Date: 2026-06-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260624_0061"
down_revision: str | None = "20260620_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    _preserve_legacy_values(table_name)

    for column_name in ("event_description", "genre", "source_title", "language", "input_request"):
        if _column_exists(table_name, column_name):
            op.drop_column(table_name, column_name)


def downgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    if not _column_exists(table_name, "event_description"):
        op.add_column(table_name, sa.Column("event_description", sa.Text(), nullable=True))
    if not _column_exists(table_name, "genre"):
        op.add_column(table_name, sa.Column("genre", sa.String(length=100), nullable=True))
    if not _column_exists(table_name, "source_title"):
        op.add_column(table_name, sa.Column("source_title", sa.String(length=255), nullable=True))
    if not _column_exists(table_name, "language"):
        op.add_column(
            table_name,
            sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
        )
    if not _column_exists(table_name, "input_request"):
        op.add_column(table_name, sa.Column("input_request", sa.JSON(), nullable=True))


def _preserve_legacy_values(table_name: str) -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if "languages" in columns and "language" in columns:
        op.execute(
            sa.text(
                """
                UPDATE custom_story_workflows
                SET languages = JSON_ARRAY(language)
                WHERE languages IS NULL
                  AND language IS NOT NULL
                """
            )
        )


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
