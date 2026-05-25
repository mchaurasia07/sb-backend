"""add marathi child book language

Revision ID: 20260524_0013
Revises: 20260524_0012
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0013"
down_revision: str | None = "20260524_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_child_books_language_check() -> None:
    bind = op.get_bind()
    if bind.dialect.name in {"mysql", "mariadb"}:
        constraint_exists = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'child_books'
                  AND CONSTRAINT_TYPE = 'CHECK'
                  AND CONSTRAINT_NAME = 'ck_child_books_language'
                LIMIT 1
                """
            )
        ).first()
        if constraint_exists:
            op.execute("ALTER TABLE child_books DROP CONSTRAINT ck_child_books_language")
    else:
        constraints = sa.inspect(bind).get_check_constraints("child_books")
        if any(constraint["name"] == "ck_child_books_language" for constraint in constraints):
            op.drop_constraint("ck_child_books_language", "child_books", type_="check")


def upgrade() -> None:
    _drop_child_books_language_check()
    op.create_check_constraint("ck_child_books_language", "child_books", "language IN ('en', 'hi', 'mr')")


def downgrade() -> None:
    _drop_child_books_language_check()
    op.create_check_constraint("ck_child_books_language", "child_books", "language IN ('en', 'hi')")
