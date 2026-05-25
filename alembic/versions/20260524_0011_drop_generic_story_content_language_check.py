"""drop generic story content language check

Revision ID: 20260524_0011
Revises: 20260524_0010
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0011"
down_revision: str | None = "20260524_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name in {"mysql", "mariadb"}:
        constraint_exists = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'generic_story_contents'
                  AND CONSTRAINT_NAME = 'ck_generic_story_contents_language'
                """
            )
        ).first()
    else:
        constraints = sa.inspect(bind).get_check_constraints("generic_story_contents")
        constraint_exists = any(
            constraint["name"] == "ck_generic_story_contents_language" for constraint in constraints
        )

    if constraint_exists:
        if bind.dialect.name in {"mysql", "mariadb"}:
            op.execute("ALTER TABLE generic_story_contents DROP CONSTRAINT ck_generic_story_contents_language")
        else:
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
