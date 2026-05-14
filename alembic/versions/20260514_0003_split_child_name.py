"""split child name

Revision ID: 20260514_0003
Revises: 20260514_0002
Create Date: 2026-05-14
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260514_0003"
down_revision: str | None = "20260514_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("child_profiles", sa.Column("first_name", sa.String(length=60), nullable=True))
    op.add_column("child_profiles", sa.Column("last_name", sa.String(length=60), nullable=True))

    op.execute(
        """
        UPDATE child_profiles
        SET
            first_name = COALESCE(NULLIF(TRIM(SUBSTRING_INDEX(child_name, ' ', 1)), ''), child_name),
            last_name = COALESCE(NULLIF(TRIM(SUBSTRING(child_name, LENGTH(SUBSTRING_INDEX(child_name, ' ', 1)) + 1)), ''), '')
        WHERE child_name IS NOT NULL
        """
    )

    op.alter_column("child_profiles", "first_name", existing_type=sa.String(length=60), nullable=False)
    op.alter_column("child_profiles", "last_name", existing_type=sa.String(length=60), nullable=False)
    op.drop_column("child_profiles", "child_name")


def downgrade() -> None:
    op.add_column("child_profiles", sa.Column("child_name", sa.String(length=120), nullable=True))
    op.execute(
        """
        UPDATE child_profiles
        SET child_name = COALESCE(NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''), first_name)
        WHERE first_name IS NOT NULL OR last_name IS NOT NULL
        """
    )
    op.alter_column("child_profiles", "child_name", existing_type=sa.String(length=120), nullable=False)
    op.drop_column("child_profiles", "last_name")
    op.drop_column("child_profiles", "first_name")
