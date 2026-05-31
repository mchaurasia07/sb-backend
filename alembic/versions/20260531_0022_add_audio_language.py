"""add audio language

Revision ID: 20260531_0022
Revises: 20260531_0021
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_0022"
down_revision: str | None = "20260531_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generic_audios",
        sa.Column("language", sa.String(16), nullable=False, server_default="en"),
    )
    op.add_column(
        "child_audios",
        sa.Column("language", sa.String(16), nullable=False, server_default="en"),
    )

    op.drop_constraint("uq_generic_audios_name", "generic_audios", type_="unique")
    op.create_unique_constraint(
        "uq_generic_audios_name_language",
        "generic_audios",
        ["name", "language"],
    )

    op.create_check_constraint(
        "ck_generic_audios_language",
        "generic_audios",
        "language IN ('en', 'hi', 'mr')",
    )
    op.create_check_constraint(
        "ck_child_audios_language",
        "child_audios",
        "language IN ('en', 'hi', 'mr')",
    )
    op.create_index("ix_generic_audios_language", "generic_audios", ["language"])
    op.create_index("ix_child_audios_language", "child_audios", ["language"])


def downgrade() -> None:
    op.drop_index("ix_child_audios_language", table_name="child_audios")
    op.drop_index("ix_generic_audios_language", table_name="generic_audios")
    op.drop_constraint("ck_child_audios_language", "child_audios", type_="check")
    op.drop_constraint("ck_generic_audios_language", "generic_audios", type_="check")

    op.drop_constraint("uq_generic_audios_name_language", "generic_audios", type_="unique")
    op.create_unique_constraint("uq_generic_audios_name", "generic_audios", ["name"])

    op.drop_column("child_audios", "language")
    op.drop_column("generic_audios", "language")
