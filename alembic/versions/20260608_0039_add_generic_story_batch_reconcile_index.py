"""add generic story batch reconcile index

Revision ID: 20260608_0039
Revises: 20260608_0038
Create Date: 2026-06-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260608_0039"
down_revision: str | None = "20260608_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_generic_story_batch_jobs_status_updated_at",
        "generic_story_batch_jobs",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generic_story_batch_jobs_status_updated_at",
        table_name="generic_story_batch_jobs",
    )
