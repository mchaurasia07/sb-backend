"""limit child age to 12

Revision ID: 20260531_0023
Revises: 20260531_0022
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_0023"
down_revision: str | None = "20260531_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_age_constraint_if_exists() -> None:
    bind = op.get_bind()
    if bind.dialect.name in {"mysql", "mariadb"}:
        result = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'child_profiles'
                  AND CONSTRAINT_TYPE = 'CHECK'
                  AND CONSTRAINT_NAME = 'ck_child_profiles_age_range'
                LIMIT 1
                """
            )
        )
        if result.first() is not None:
            op.execute("ALTER TABLE child_profiles DROP CONSTRAINT ck_child_profiles_age_range")
        return

    constraints = sa.inspect(bind).get_check_constraints("child_profiles")
    if any(constraint["name"] == "ck_child_profiles_age_range" for constraint in constraints):
        op.drop_constraint("ck_child_profiles_age_range", "child_profiles", type_="check")


def upgrade() -> None:
    _drop_age_constraint_if_exists()
    op.execute("UPDATE child_profiles SET age = 12 WHERE age > 12")
    op.create_check_constraint(
        "ck_child_profiles_age_range",
        "child_profiles",
        "age >= 0 AND age <= 12",
    )


def downgrade() -> None:
    _drop_age_constraint_if_exists()
    op.create_check_constraint(
        "ck_child_profiles_age_range",
        "child_profiles",
        "age >= 0 AND age <= 18",
    )
