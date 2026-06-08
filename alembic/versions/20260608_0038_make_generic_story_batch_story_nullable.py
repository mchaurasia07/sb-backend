"""make generic story batch story nullable

Revision ID: 20260608_0038
Revises: 20260605_0034
Create Date: 2026-06-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260608_0038"
down_revision: str | None = "20260605_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "generic_story_batch_jobs",
        "generic_story_id",
        existing_type=sa.Uuid(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "generic_story_batch_jobs",
        "generic_story_id",
        existing_type=sa.Uuid(as_uuid=True),
        nullable=False,
    )
