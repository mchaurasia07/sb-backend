"""add story input request

Revision ID: 20260531_0025
Revises: 20260531_0024
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_0025"
down_revision: str | None = "20260531_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stories", sa.Column("input_request", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("stories", "input_request")
