"""add language to child books

Revision ID: 20260524_0010
Revises: 20260524_0009
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_0010"
down_revision: str | None = "20260524_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("child_books", sa.Column("language", sa.String(2), nullable=False, server_default="en"))
    op.create_check_constraint("ck_child_books_language", "child_books", "language IN ('en', 'hi')")
    op.drop_constraint("uq_child_books_child_story_type", "child_books", type_="unique")
    op.create_unique_constraint(
        "uq_child_books_child_story_type_language",
        "child_books",
        ["child_id", "story_id", "story_type", "language"],
    )
    op.create_index("ix_child_books_language", "child_books", ["language"])


def downgrade() -> None:
    op.drop_index("ix_child_books_language", table_name="child_books")
    op.drop_constraint("uq_child_books_child_story_type_language", "child_books", type_="unique")
    op.create_unique_constraint(
        "uq_child_books_child_story_type",
        "child_books",
        ["child_id", "story_id", "story_type"],
    )
    op.drop_constraint("ck_child_books_language", "child_books", type_="check")
    op.drop_column("child_books", "language")
