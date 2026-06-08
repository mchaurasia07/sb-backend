"""compatibility bridge for local databases stamped at 0036

Revision ID: 20260607_0036
Revises: 20260605_0034
Create Date: 2026-06-07
"""

from collections.abc import Sequence

revision: str = "20260607_0036"
down_revision: str | None = "20260605_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
