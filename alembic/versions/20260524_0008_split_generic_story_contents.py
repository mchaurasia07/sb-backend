"""split generic story content by language

Revision ID: 20260524_0008
Revises: 20260524_0007
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0008"
down_revision: str | None = "20260524_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generic_story_contents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("generic_story_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("story_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["generic_story_id"], ["generic_stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generic_story_id", "language", name="uq_generic_story_contents_story_language"),
    )
    op.create_index("ix_generic_story_contents_story_id", "generic_story_contents", ["generic_story_id"])
    op.create_index("ix_generic_story_contents_language", "generic_story_contents", ["language"])

    op.execute(
        """
        INSERT INTO generic_story_contents (
            id,
            generic_story_id,
            language,
            story_json,
            created_at,
            updated_at
        )
        SELECT
            REPLACE(UUID(), '-', ''),
            id,
            language,
            story_json,
            created_at,
            updated_at
        FROM generic_stories
        WHERE story_json IS NOT NULL
        """
    )

    op.drop_column("generic_stories", "story_json")
    op.drop_column("generic_stories", "language")


def downgrade() -> None:
    op.add_column("generic_stories", sa.Column("language", sa.String(50), nullable=False, server_default="en"))
    op.add_column("generic_stories", sa.Column("story_json", sa.JSON(), nullable=True))

    op.execute(
        """
        UPDATE generic_stories gs
        JOIN generic_story_contents gsc
          ON gsc.generic_story_id = gs.id
         AND gsc.language = 'en'
        SET gs.language = gsc.language,
            gs.story_json = gsc.story_json
        """
    )

    op.drop_index("ix_generic_story_contents_language", table_name="generic_story_contents")
    op.drop_index("ix_generic_story_contents_story_id", table_name="generic_story_contents")
    op.drop_table("generic_story_contents")
