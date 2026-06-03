"""add generic story batch jobs

Revision ID: 20260603_0030
Revises: 20260603_0029
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_0030"
down_revision: str | None = "20260603_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generic_story_batch_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("generic_story_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_id", sa.Uuid(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["generic_story_id"], ["generic_stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["generic_story_workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generic_story_batch_jobs_generic_story_id", "generic_story_batch_jobs", ["generic_story_id"])
    op.create_index("ix_generic_story_batch_jobs_workflow_id", "generic_story_batch_jobs", ["workflow_id"])
    op.create_index("ix_generic_story_batch_jobs_provider_job_name", "generic_story_batch_jobs", ["provider_job_name"])
    op.create_index("ix_generic_story_batch_jobs_status", "generic_story_batch_jobs", ["status"])
    op.create_index("ix_generic_story_batch_jobs_job_type", "generic_story_batch_jobs", ["job_type"])


def downgrade() -> None:
    op.drop_index("ix_generic_story_batch_jobs_job_type", table_name="generic_story_batch_jobs")
    op.drop_index("ix_generic_story_batch_jobs_status", table_name="generic_story_batch_jobs")
    op.drop_index("ix_generic_story_batch_jobs_provider_job_name", table_name="generic_story_batch_jobs")
    op.drop_index("ix_generic_story_batch_jobs_workflow_id", table_name="generic_story_batch_jobs")
    op.drop_index("ix_generic_story_batch_jobs_generic_story_id", table_name="generic_story_batch_jobs")
    op.drop_table("generic_story_batch_jobs")
