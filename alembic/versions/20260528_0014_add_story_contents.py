"""add story contents

Revision ID: 20260528_0014
Revises: 20260524_0013
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0014"
down_revision: str | None = "20260524_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "story_contents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("story_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("story_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "language", name="uq_story_contents_story_language"),
    )
    op.create_index("ix_story_contents_story_id", "story_contents", ["story_id"])
    op.create_index("ix_story_contents_language", "story_contents", ["language"])

    op.execute(
        """
        INSERT INTO story_contents (
            id,
            story_id,
            language,
            story_json,
            created_at,
            updated_at
        )
        SELECT
            REPLACE(UUID(), '-', ''),
            id,
            'en',
            story_json,
            created_at,
            updated_at
        FROM stories
        WHERE story_json IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_story_contents_language", table_name="story_contents")
    op.drop_index("ix_story_contents_story_id", table_name="story_contents")
    op.drop_table("story_contents")
