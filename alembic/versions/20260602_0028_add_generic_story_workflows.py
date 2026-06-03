"""add generic story workflows

Revision ID: 20260602_0028
Revises: 20260601_0027
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260602_0028"
down_revision: str | None = "20260601_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generic_story_workflows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("generic_story_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("workflow_name", sa.String(length=64), nullable=False),
        sa.Column("actual_story", sa.Text(), nullable=False),
        sa.Column("age_group", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("requested_pages", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("character_analysis_json", sa.JSON(), nullable=True),
        sa.Column("scene_plan_json", sa.JSON(), nullable=True),
        sa.Column("story_json", sa.JSON(), nullable=True),
        sa.Column("image_plan_json", sa.JSON(), nullable=True),
        sa.Column("input_request", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("theme", sa.String(length=100), nullable=True),
        sa.Column("genre", sa.String(length=100), nullable=True),
        sa.Column("moral", sa.String(length=255), nullable=True),
        sa.Column("learning_goal", sa.String(length=500), nullable=True),
        sa.Column("cover_image", sa.String(length=1024), nullable=True),
        sa.Column("ai_provider", sa.String(length=32), nullable=False),
        sa.Column("text_model", sa.String(length=128), nullable=True),
        sa.Column("image_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["generic_story_id"], ["generic_stories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generic_story_workflows_user_id", "generic_story_workflows", ["user_id"])
    op.create_index("ix_generic_story_workflows_status", "generic_story_workflows", ["status"])
    op.create_index("ix_generic_story_workflows_generic_story_id", "generic_story_workflows", ["generic_story_id"])
    op.create_index("ix_generic_story_workflows_created_at", "generic_story_workflows", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_generic_story_workflows_created_at", table_name="generic_story_workflows")
    op.drop_index("ix_generic_story_workflows_generic_story_id", table_name="generic_story_workflows")
    op.drop_index("ix_generic_story_workflows_status", table_name="generic_story_workflows")
    op.drop_index("ix_generic_story_workflows_user_id", table_name="generic_story_workflows")
    op.drop_table("generic_story_workflows")
