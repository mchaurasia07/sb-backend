"""add generic story visual bible step output

Revision ID: 20260604_0031
Revises: 20260603_0030
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260604_0031"
down_revision: str | None = "20260603_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generic_story_workflows", sa.Column("visual_bible_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("generic_story_workflows", "visual_bible_json")
