"""add story ai config

Revision ID: 20260531_0024
Revises: 20260531_0023
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_0024"
down_revision: str | None = "20260531_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stories", sa.Column("ai_provider", sa.String(length=32), nullable=True))
    op.add_column("stories", sa.Column("text_model", sa.String(length=128), nullable=True))
    op.add_column("stories", sa.Column("image_model", sa.String(length=128), nullable=True))
    op.add_column("stories", sa.Column("reference_image_model", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("stories", "reference_image_model")
    op.drop_column("stories", "image_model")
    op.drop_column("stories", "text_model")
    op.drop_column("stories", "ai_provider")
