"""add notifications

Revision ID: 20260601_0027
Revises: 20260601_0026
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_0027"
down_revision: str | None = "20260601_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_device_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=True),
        sa.Column("account_type", sa.Enum("PARENT", "CHILD", native_enum=False), nullable=False),
        sa.Column("expo_push_token", sa.String(length=255), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_push_device_tokens_token", "push_device_tokens", ["expo_push_token"], unique=True)
    op.create_index("ix_push_device_tokens_user_id", "push_device_tokens", ["user_id"])
    op.create_index("ix_push_device_tokens_child_id", "push_device_tokens", ["child_id"])
    op.create_index("ix_push_device_tokens_account_type", "push_device_tokens", ["account_type"])
    op.create_index("ix_push_device_tokens_active", "push_device_tokens", ["active"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "audience",
            sa.Enum("ALL", "PARENTS", "CHILDREN", "PARENT_USER", "CHILD", native_enum=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("child_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENT", "PARTIAL", "FAILED", "SKIPPED", native_enum=False),
            nullable=False,
        ),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("tickets", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_event_type", "notifications", ["event_type"])
    op.create_index("ix_notifications_audience", "notifications", ["audience"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_child_id", "notifications", ["child_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_child_id", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index("ix_notifications_audience", table_name="notifications")
    op.drop_index("ix_notifications_event_type", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_push_device_tokens_active", table_name="push_device_tokens")
    op.drop_index("ix_push_device_tokens_account_type", table_name="push_device_tokens")
    op.drop_index("ix_push_device_tokens_child_id", table_name="push_device_tokens")
    op.drop_index("ix_push_device_tokens_user_id", table_name="push_device_tokens")
    op.drop_index("ix_push_device_tokens_token", table_name="push_device_tokens")
    op.drop_table("push_device_tokens")
