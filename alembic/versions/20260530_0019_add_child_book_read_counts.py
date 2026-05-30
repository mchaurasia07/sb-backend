"""add child book read counts

Revision ID: 20260530_0019
Revises: 20260530_0018
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260530_0019"
down_revision: str | None = "20260530_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "child_books",
        sa.Column("reading_started_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "child_books",
        sa.Column("reading_completed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("child_books", "reading_completed_count")
    op.drop_column("child_books", "reading_started_count")
