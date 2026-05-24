"""add generic stories and child books

Revision ID: 20260524_0007
Revises: 20260516_0006
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0007"
down_revision: str | None = "20260516_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generic_stories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("age_group", sa.String(32), nullable=False),
        sa.Column("theme", sa.String(100), nullable=True),
        sa.Column("genre", sa.String(100), nullable=True),
        sa.Column("language", sa.String(50), nullable=False),
        sa.Column("moral", sa.String(255), nullable=True),
        sa.Column("learning_goal", sa.String(500), nullable=True),
        sa.Column("reading_time_minutes", sa.Integer(), nullable=True),
        sa.Column("character_type", sa.String(100), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=False),
        sa.Column("cover_image", sa.String(1024), nullable=True),
        sa.Column("story_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reading_time_minutes >= 0", name="ck_generic_stories_reading_time_non_negative"),
        sa.CheckConstraint("total_pages >= 0", name="ck_generic_stories_total_pages_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generic_stories_status", "generic_stories", ["status"])
    op.create_index("ix_generic_stories_age_group", "generic_stories", ["age_group"])
    op.create_index("ix_generic_stories_theme", "generic_stories", ["theme"])
    op.create_index("ix_generic_stories_genre", "generic_stories", ["genre"])
    op.create_index("ix_generic_stories_created_at", "generic_stories", ["created_at"])

    op.create_table(
        "child_books",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("child_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("story_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("story_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("cover_image", sa.String(1024), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_page_read", sa.Integer(), nullable=False),
        sa.Column("last_page_read_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("child_id", "story_id", "story_type", name="uq_child_books_child_story_type"),
    )
    op.create_index("ix_child_books_child_id", "child_books", ["child_id"])
    op.create_index("ix_child_books_story_id", "child_books", ["story_id"])
    op.create_index("ix_child_books_status", "child_books", ["status"])
    op.create_index("ix_child_books_created_at", "child_books", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_child_books_created_at", table_name="child_books")
    op.drop_index("ix_child_books_status", table_name="child_books")
    op.drop_index("ix_child_books_story_id", table_name="child_books")
    op.drop_index("ix_child_books_child_id", table_name="child_books")
    op.drop_table("child_books")

    op.drop_index("ix_generic_stories_created_at", table_name="generic_stories")
    op.drop_index("ix_generic_stories_genre", table_name="generic_stories")
    op.drop_index("ix_generic_stories_theme", table_name="generic_stories")
    op.drop_index("ix_generic_stories_age_group", table_name="generic_stories")
    op.drop_index("ix_generic_stories_status", table_name="generic_stories")
    op.drop_table("generic_stories")
