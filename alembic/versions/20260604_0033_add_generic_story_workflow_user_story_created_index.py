"""add generic story workflow user story created index

Revision ID: 20260604_0033
Revises: 20260604_0032
Create Date: 2026-06-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260604_0033"
down_revision: str | None = "20260604_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_generic_story_workflows_user_story_created_at",
        "generic_story_workflows",
        ["user_id", "generic_story_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generic_story_workflows_user_story_created_at",
        table_name="generic_story_workflows",
    )
