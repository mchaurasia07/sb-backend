"""add child dob

Revision ID: 20260514_0004
Revises: 20260514_0003
Create Date: 2026-05-14
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260514_0004"
down_revision: str | None = "20260514_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("child_profiles", sa.Column("dob", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("child_profiles", "dob")
