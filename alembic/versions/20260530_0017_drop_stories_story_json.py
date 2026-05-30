"""drop stories story_json

Revision ID: 20260530_0017
Revises: 20260529_0016
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260530_0017"
down_revision: str | None = "20260529_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
            s.id,
            'en',
            s.story_json,
            s.created_at,
            s.updated_at
        FROM stories s
        WHERE s.story_json IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM story_contents sc
              WHERE sc.story_id = s.id
                AND sc.language = 'en'
          )
        """
    )
    op.drop_column("stories", "story_json")


def downgrade() -> None:
    op.add_column("stories", sa.Column("story_json", sa.JSON(), nullable=True))
    op.execute(
        """
        UPDATE stories s
        JOIN story_contents sc
          ON sc.story_id = s.id
         AND sc.language = 'en'
        SET s.story_json = sc.story_json
        """
    )
