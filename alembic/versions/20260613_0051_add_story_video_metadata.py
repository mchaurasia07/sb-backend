"""add story video metadata

Revision ID: 20260613_0051
Revises: 20260610_0050
Create Date: 2026-06-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260613_0051"
down_revision: str | None = "20260610_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not _table_exists("stories"):
        return
    if not _column_exists("stories", "video_created"):
        op.add_column(
            "stories",
            sa.Column("video_created", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if not _column_exists("stories", "video_metadata"):
        op.add_column("stories", sa.Column("video_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    if not _table_exists("stories"):
        return
    if _column_exists("stories", "video_metadata"):
        op.drop_column("stories", "video_metadata")
    if _column_exists("stories", "video_created"):
        op.drop_column("stories", "video_created")


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
