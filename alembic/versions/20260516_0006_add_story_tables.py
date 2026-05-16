"""add story generation tables

Revision ID: 20260516_0006
Revises: 20260516_0005
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260516_0006"
down_revision: str | None = "20260516_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create stories table
    op.create_table(
        "stories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("child_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("moral", sa.String(255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("generation_mode", sa.String(50), nullable=False),
        sa.Column("age_group", sa.String(10), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("learning_goal", sa.String(500), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("event_description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("current_step", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("story_plan_json", sa.JSON(), nullable=True),
        sa.Column("story_json", sa.JSON(), nullable=True),
        sa.Column("image_plan_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stories_user_id", "stories", ["user_id"])
    op.create_index("ix_stories_child_id", "stories", ["child_id"])
    op.create_index("ix_stories_status", "stories", ["status"])
    op.create_index("ix_stories_created_at", "stories", ["created_at"])

    # Create story_steps table
    op.create_table(
        "story_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("story_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("step_name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_story_steps_story_id", "story_steps", ["story_id"])
    op.create_index("ix_story_steps_step_name", "story_steps", ["step_name"])
    op.create_index("ix_story_steps_status", "story_steps", ["status"])

    # Create story_pages table
    op.create_table(
        "story_pages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("story_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_type", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("image_prompt", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "page_number", name="uq_story_pages_story_id_page_number"),
    )
    op.create_index("ix_story_pages_story_id", "story_pages", ["story_id"])


def downgrade() -> None:
    op.drop_index("ix_story_pages_story_id", table_name="story_pages")
    op.drop_table("story_pages")

    op.drop_index("ix_story_steps_status", table_name="story_steps")
    op.drop_index("ix_story_steps_step_name", table_name="story_steps")
    op.drop_index("ix_story_steps_story_id", table_name="story_steps")
    op.drop_table("story_steps")

    op.drop_index("ix_stories_created_at", table_name="stories")
    op.drop_index("ix_stories_status", table_name="stories")
    op.drop_index("ix_stories_child_id", table_name="stories")
    op.drop_index("ix_stories_user_id", table_name="stories")
    op.drop_table("stories")
