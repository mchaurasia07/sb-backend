"""widen batch job type columns

Revision ID: 20260618_0056
Revises: 20260618_0055
Create Date: 2026-06-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260618_0056"
down_revision: str | None = "20260618_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _widen_job_type("story_batch_jobs")
    _widen_job_type("custom_story_batch_jobs")


def downgrade() -> None:
    # Keep the widened columns on downgrade. Narrowing can fail when rows contain
    # STORY_PLAN, STORY, or IMAGE_PLAN values.
    pass


def _widen_job_type(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    column = columns.get("job_type")
    if column is None:
        return
    length = getattr(column["type"], "length", None)
    if length is not None and length >= 32:
        return
    op.alter_column(
        table_name,
        "job_type",
        existing_type=column["type"],
        type_=sa.String(length=32),
        existing_nullable=False,
    )
