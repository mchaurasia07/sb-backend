"""store child password as plaintext

Revision ID: 20260529_0016
Revises: 20260528_0015
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260529_0016"
down_revision: str | None = "20260528_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _default_password(dob) -> str:
    return dob.strftime("%d%m%Y") if dob else "01012000"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, dob, child_password FROM child_profiles")).mappings()

    for row in rows:
        current_password = row["child_password"] or ""
        if current_password and not current_password.startswith("$2"):
            continue

        bind.execute(
            sa.text("UPDATE child_profiles SET child_password = :child_password WHERE id = :id"),
            {
                "child_password": _default_password(row["dob"]),
                "id": row["id"],
            },
        )


def downgrade() -> None:
    pass
