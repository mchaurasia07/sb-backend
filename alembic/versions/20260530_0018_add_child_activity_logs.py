"""add child activity logs

Revision ID: 20260530_0018
Revises: 20260530_0017
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260530_0018"
down_revision: str | None = "20260530_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("child_books", sa.Column("reading_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("child_books", sa.Column("reading_completed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "child_activity_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("child_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("activity_name", sa.String(100), nullable=False),
        sa.Column("activity_type", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resource_name", sa.String(255), nullable=True),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_child_activity_logs_child_id", "child_activity_logs", ["child_id"])
    op.create_index("ix_child_activity_logs_activity_type", "child_activity_logs", ["activity_type"])
    op.create_index("ix_child_activity_logs_occurred_at", "child_activity_logs", ["occurred_at"])
    op.create_index("ix_child_activity_logs_resource", "child_activity_logs", ["resource_type", "resource_id"])


def downgrade() -> None:
    op.drop_index("ix_child_activity_logs_resource", table_name="child_activity_logs")
    op.drop_index("ix_child_activity_logs_occurred_at", table_name="child_activity_logs")
    op.drop_index("ix_child_activity_logs_activity_type", table_name="child_activity_logs")
    op.drop_index("ix_child_activity_logs_child_id", table_name="child_activity_logs")
    op.drop_table("child_activity_logs")
    op.drop_column("child_books", "reading_completed_at")
    op.drop_column("child_books", "reading_started_at")
