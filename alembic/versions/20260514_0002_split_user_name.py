"""split user name

Revision ID: 20260514_0002
Revises: 20260513_0001
Create Date: 2026-05-14
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260514_0002"
down_revision: str | None = "20260513_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=60), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=60), nullable=True))

    op.execute(
        """
        UPDATE users
        SET
            first_name = NULLIF(TRIM(SUBSTRING_INDEX(full_name, ' ', 1)), ''),
            last_name = NULLIF(TRIM(SUBSTRING(full_name, LENGTH(SUBSTRING_INDEX(full_name, ' ', 1)) + 1)), '')
        WHERE full_name IS NOT NULL
        """
    )

    op.drop_column("users", "full_name")


def downgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=120), nullable=True))
    op.execute(
        """
        UPDATE users
        SET full_name = NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), '')
        WHERE first_name IS NOT NULL OR last_name IS NOT NULL
        """
    )
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
