"""add story batch jobs

Revision ID: 20260601_0026
Revises: 20260531_0025
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_0026"
down_revision: str | None = "20260531_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "story_batch_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("story_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.Enum("IMAGE", "AUDIO", native_enum=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum("SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", native_enum=False),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_job_name", sa.String(length=255), nullable=True),
        sa.Column("provider_model", sa.String(length=128), nullable=True),
        sa.Column("provider_state", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("expected_item_count", sa.Integer(), nullable=False),
        sa.Column("completed_item_count", sa.Integer(), nullable=False),
        sa.Column("failed_item_count", sa.Integer(), nullable=False),
        sa.Column("request_keys", sa.JSON(), nullable=True),
        sa.Column("missing_keys", sa.JSON(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_story_batch_jobs_story_id", "story_batch_jobs", ["story_id"])
    op.create_index("ix_story_batch_jobs_provider_job_name", "story_batch_jobs", ["provider_job_name"])
    op.create_index("ix_story_batch_jobs_status", "story_batch_jobs", ["status"])
    op.create_index("ix_story_batch_jobs_job_type", "story_batch_jobs", ["job_type"])


def downgrade() -> None:
    op.drop_index("ix_story_batch_jobs_job_type", table_name="story_batch_jobs")
    op.drop_index("ix_story_batch_jobs_status", table_name="story_batch_jobs")
    op.drop_index("ix_story_batch_jobs_provider_job_name", table_name="story_batch_jobs")
    op.drop_index("ix_story_batch_jobs_story_id", table_name="story_batch_jobs")
    op.drop_table("story_batch_jobs")
