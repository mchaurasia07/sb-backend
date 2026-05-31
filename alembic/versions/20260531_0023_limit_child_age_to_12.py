"""limit child age to 12

Revision ID: 20260531_0023
Revises: 20260531_0022
Create Date: 2026-05-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260531_0023"
down_revision: str | None = "20260531_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_child_profiles_age_range", "child_profiles", type_="check")
    op.create_check_constraint(
        "ck_child_profiles_age_range",
        "child_profiles",
        "age >= 0 AND age <= 12",
    )


def downgrade() -> None:
    op.drop_constraint("ck_child_profiles_age_range", "child_profiles", type_="check")
    op.create_check_constraint(
        "ck_child_profiles_age_range",
        "child_profiles",
        "age >= 0 AND age <= 18",
    )
