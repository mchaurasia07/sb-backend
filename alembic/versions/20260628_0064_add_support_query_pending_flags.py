"""add support query pending flags

Revision ID: 20260628_0064
Revises: 20260628_0063
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0064"
down_revision: str | None = "20260628_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("support_queries")
    }
    if "pending_at_user" not in columns:
        op.add_column(
            "support_queries",
            sa.Column(
                "pending_at_user",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )
    if "pending_at_jugni" not in columns:
        op.add_column(
            "support_queries",
            sa.Column(
                "pending_at_jugni",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )

    indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("support_queries")
    }
    if "ix_support_queries_pending_status_created" not in indexes:
        op.create_index(
            "ix_support_queries_pending_status_created",
            "support_queries",
            ["pending_at_jugni", "pending_at_user", "status", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {
        index["name"]
        for index in inspector.get_indexes("support_queries")
    }
    if "ix_support_queries_pending_status_created" in indexes:
        op.drop_index(
            "ix_support_queries_pending_status_created",
            table_name="support_queries",
        )

    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("support_queries")
    }
    if "pending_at_jugni" in columns:
        op.drop_column("support_queries", "pending_at_jugni")
    if "pending_at_user" in columns:
        op.drop_column("support_queries", "pending_at_user")
