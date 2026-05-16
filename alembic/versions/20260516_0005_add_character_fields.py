"""add character generation fields to child profiles

Revision ID: 20260516_0005
Revises: 20260514_0004
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260516_0005"
down_revision: str | None = "20260514_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("child_profiles", sa.Column("character_image_url", sa.String(1024), nullable=True))
    op.add_column("child_profiles", sa.Column("character_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("child_profiles", "character_metadata")
    op.drop_column("child_profiles", "character_image_url")
