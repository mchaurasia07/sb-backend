"""relax custom story workflow legacy columns

Revision ID: 20260609_0045
Revises: 20260609_0044
Create Date: 2026-06-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260609_0045"
down_revision: str | None = "20260609_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    # Some local databases had a partially-created custom workflow table that
    # inherited generic workflow columns. Make those legacy columns compatible
    # with the custom workflow insert shape instead of failing on omitted fields.
    _alter_if_exists(
        table_name,
        "workflow_name",
        existing_type=sa.String(length=64),
        nullable=False,
        server_default="custom_story",
    )
    _alter_if_exists(table_name, "actual_story", existing_type=sa.Text(), nullable=True)
    _alter_if_exists(
        table_name,
        "language",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="en",
    )
    _alter_if_exists(table_name, "requested_pages", existing_type=sa.Integer(), nullable=True)
    _alter_if_exists(table_name, "theme", existing_type=sa.String(length=100), nullable=True)
    _alter_if_exists(table_name, "genre", existing_type=sa.String(length=100), nullable=True)
    _alter_if_exists(table_name, "cover_image", existing_type=sa.String(length=1024), nullable=True)


def downgrade() -> None:
    # Live-schema repair only. Do not restore incompatible NOT NULL legacy
    # columns on downgrade.
    pass


def _alter_if_exists(
    table_name: str,
    column_name: str,
    *,
    existing_type,
    nullable: bool,
    server_default: str | None = None,
) -> None:
    if not _column_exists(table_name, column_name):
        return
    op.alter_column(
        table_name,
        column_name,
        existing_type=existing_type,
        existing_nullable=True,
        nullable=nullable,
        server_default=server_default,
    )


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
