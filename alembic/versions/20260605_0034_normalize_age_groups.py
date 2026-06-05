"""normalize age groups

Revision ID: 20260605_0034
Revises: 20260604_0033
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260605_0034"
down_revision: str | None = "20260604_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ("generic_stories", "generic_story_workflows", "stories")


def upgrade() -> None:
    for table_name in TABLES:
        op.execute(
            f"""
            UPDATE {table_name}
            SET age_group = CASE
                WHEN age_group IN ('INFANT_TODDLER', 'TODDLER', '0-2', '2-4') THEN '0-3'
                WHEN age_group IN ('EARLY_READER', '4-6') THEN '3-6'
                WHEN age_group IN ('ADVANCED', 'GROWING_READER', '6-8') THEN '6-9'
                ELSE age_group
            END
            """
        )


def downgrade() -> None:
    for table_name in TABLES:
        op.execute(
            f"""
            UPDATE {table_name}
            SET age_group = CASE
                WHEN age_group = '0-3' THEN '2-4'
                WHEN age_group = '3-6' THEN '4-6'
                WHEN age_group = '6-9' THEN '6-8'
                ELSE age_group
            END
            """
        )
