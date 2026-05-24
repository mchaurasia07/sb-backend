"""make generic story title unique

Revision ID: 20260524_0009
Revises: 20260524_0008
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260524_0009"
down_revision: str | None = "20260524_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE cb
        FROM child_books cb
        JOIN generic_stories gs
          ON cb.story_type = 'generic'
         AND cb.story_id = gs.id
        JOIN generic_stories newer
          ON newer.title = gs.title
         AND (
              newer.created_at > gs.created_at
              OR (newer.created_at = gs.created_at AND newer.id > gs.id)
         )
        """
    )
    op.execute(
        """
        DELETE gs
        FROM generic_stories gs
        JOIN generic_stories newer
          ON newer.title = gs.title
         AND (
              newer.created_at > gs.created_at
              OR (newer.created_at = gs.created_at AND newer.id > gs.id)
         )
        """
    )
    op.create_unique_constraint("uq_generic_stories_title", "generic_stories", ["title"])


def downgrade() -> None:
    op.drop_constraint("uq_generic_stories_title", "generic_stories", type_="unique")
