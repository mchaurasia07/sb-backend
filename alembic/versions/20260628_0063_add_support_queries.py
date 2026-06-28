"""add support queries

Revision ID: 20260628_0063
Revises: 20260628_0062
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0063"
down_revision: str | None = "20260628_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_queries",
        sa.Column("query_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "IN_PROGRESS", "RESPONDED", "CLOSED", native_enum=False),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_queries_query_id", "support_queries", ["query_id"], unique=True)
    op.create_index(
        "ix_support_queries_user_updated", "support_queries", ["user_id", "updated_at"]
    )
    op.create_index("ix_support_queries_status", "support_queries", ["status"])

    op.create_table(
        "support_messages",
        sa.Column("message_id", sa.String(length=32), nullable=False),
        sa.Column("support_query_id", sa.CHAR(36), nullable=False),
        sa.Column(
            "sender",
            sa.Enum("USER", "SUPPORT", "JUGNI", native_enum=False),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["support_query_id"], ["support_queries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_messages_message_id", "support_messages", ["message_id"], unique=True
    )
    op.create_index(
        "ix_support_messages_query_created",
        "support_messages",
        ["support_query_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_messages_query_created", table_name="support_messages")
    op.drop_index("ix_support_messages_message_id", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_index("ix_support_queries_status", table_name="support_queries")
    op.drop_index("ix_support_queries_user_updated", table_name="support_queries")
    op.drop_index("ix_support_queries_query_id", table_name="support_queries")
    op.drop_table("support_queries")
