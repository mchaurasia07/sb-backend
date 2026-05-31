"""add story validation checkpoints

Revision ID: 20260531_0020
Revises: 20260530_0019
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_0020"
down_revision: str | None = "20260530_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stories",
        sa.Column("story_plan_validated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "stories",
        sa.Column("image_plan_validated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("stories", "image_plan_validated")
    op.drop_column("stories", "story_plan_validated")
