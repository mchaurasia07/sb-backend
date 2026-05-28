"""add child login fields

Revision ID: 20260528_0015
Revises: 20260528_0014
Create Date: 2026-05-28
"""

from collections.abc import Sequence
import re

import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0015"
down_revision: str | None = "20260528_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BLOCKED_USERNAME_SUFFIXES = {69, 420, 666}


def _username_base(first_name: str) -> str:
    raw_username = first_name.lower()
    username = re.sub(r"[^a-z0-9._-]+", "", raw_username)
    username = re.sub(r"[_-]+", "_", username)
    username = re.sub(r"\.+", "", username).strip("_-")
    return username or "child"


def _is_blocked_suffix(suffix: int) -> bool:
    return suffix in BLOCKED_USERNAME_SUFFIXES


def _default_password(dob) -> str:
    return dob.strftime("%d%m%Y") if dob else "01012000"


def upgrade() -> None:
    op.add_column("child_profiles", sa.Column("child_user_id", sa.String(length=128), nullable=True))
    op.add_column("child_profiles", sa.Column("child_password", sa.String(length=255), nullable=True))
    op.add_column("child_profiles", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, first_name, dob FROM child_profiles ORDER BY created_at, id")).mappings()

    used_usernames: set[str] = set()
    for row in rows:
        base_username = _username_base(row["first_name"] or "child")
        suffix = 1
        while True:
            child_user_id = f"{base_username}_{suffix:02d}"
            if not _is_blocked_suffix(suffix) and child_user_id not in used_usernames:
                break
            suffix += 1
        used_usernames.add(child_user_id)

        bind.execute(
            sa.text(
                """
                UPDATE child_profiles
                SET child_user_id = :child_user_id,
                    child_password = :child_password
                WHERE id = :id
                """
            ),
            {
                "child_user_id": child_user_id,
                "child_password": _default_password(row["dob"]),
                "id": row["id"],
            },
        )

    op.alter_column("child_profiles", "child_user_id", existing_type=sa.String(length=128), nullable=False)
    op.alter_column("child_profiles", "child_password", existing_type=sa.String(length=255), nullable=False)
    op.create_index("ix_child_profiles_child_user_id", "child_profiles", ["child_user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_child_profiles_child_user_id", table_name="child_profiles")
    op.drop_column("child_profiles", "active")
    op.drop_column("child_profiles", "child_password")
    op.drop_column("child_profiles", "child_user_id")
