"""add generic story workflow user created index

Revision ID: 20260603_0029
Revises: 20260602_0028
Create Date: 2026-06-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260603_0029"
down_revision: str | None = "20260602_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_generic_story_workflows_user_created_at",
        "generic_story_workflows",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generic_story_workflows_user_created_at",
        table_name="generic_story_workflows",
    )
