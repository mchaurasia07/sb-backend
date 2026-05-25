"""ensure generic story content language check removed

Revision ID: 20260524_0012
Revises: 20260524_0011
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0012"
down_revision: str | None = "20260524_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mysql_check_constraint_exists() -> bool:
    result = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'generic_story_contents'
              AND CONSTRAINT_TYPE = 'CHECK'
              AND CONSTRAINT_NAME = 'ck_generic_story_contents_language'
            LIMIT 1
            """
        )
    )
    return result.first() is not None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name in {"mysql", "mariadb"}:
        if _mysql_check_constraint_exists():
            op.execute("ALTER TABLE generic_story_contents DROP CONSTRAINT ck_generic_story_contents_language")
    else:
        constraints = sa.inspect(bind).get_check_constraints("generic_story_contents")
        if any(constraint["name"] == "ck_generic_story_contents_language" for constraint in constraints):
            op.drop_constraint("ck_generic_story_contents_language", "generic_story_contents", type_="check")

    op.alter_column(
        "generic_story_contents",
        "language",
        existing_type=sa.String(2),
        type_=sa.String(16),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "generic_story_contents",
        "language",
        existing_type=sa.String(16),
        type_=sa.String(2),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_generic_story_contents_language",
        "generic_story_contents",
        "language IN ('en', 'hi')",
    )
