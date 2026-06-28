"""widen support query UUID columns

Revision ID: 20260628_0065
Revises: 20260628_0064
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0065"
down_revision: str | None = "20260628_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("support_queries", "id", False),
    ("support_queries", "user_id", False),
    ("support_messages", "id", False),
    ("support_messages", "support_query_id", False),
)


def upgrade() -> None:
    bind = op.get_bind()
    columns_to_widen = _columns_to_widen(bind)
    if not columns_to_widen:
        return

    foreign_keys = _affected_foreign_keys(bind, columns_to_widen)
    for foreign_key in foreign_keys:
        op.drop_constraint(
            foreign_key["name"],
            foreign_key["source_table"],
            type_="foreignkey",
        )

    for table_name, column_name, nullable in columns_to_widen:
        op.alter_column(
            table_name,
            column_name,
            type_=sa.CHAR(36),
            existing_type=sa.CHAR(32),
            existing_nullable=nullable,
            nullable=nullable,
        )
        op.execute(
            sa.text(
                f"UPDATE `{table_name}` "
                f"SET `{column_name}` = CONCAT("
                f"SUBSTRING(`{column_name}`, 1, 8), '-', "
                f"SUBSTRING(`{column_name}`, 9, 4), '-', "
                f"SUBSTRING(`{column_name}`, 13, 4), '-', "
                f"SUBSTRING(`{column_name}`, 17, 4), '-', "
                f"SUBSTRING(`{column_name}`, 21, 12)"
                f") "
                f"WHERE LENGTH(`{column_name}`) = 32 "
                f"AND `{column_name}` NOT LIKE '%-%'"
            )
        )

    for foreign_key in foreign_keys:
        op.create_foreign_key(
            foreign_key["name"],
            foreign_key["source_table"],
            foreign_key["referent_table"],
            foreign_key["local_cols"],
            foreign_key["remote_cols"],
            **foreign_key["options"],
        )


def downgrade() -> None:
    # UUID columns must remain wide enough for the application's hyphenated format.
    pass


def _columns_to_widen(bind) -> tuple[tuple[str, str, bool], ...]:
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    columns_by_table = {
        table_name: {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        for table_name in table_names
    }
    return tuple(
        (table_name, column_name, nullable)
        for table_name, column_name, nullable in UUID_COLUMNS
        if table_name in columns_by_table
        and column_name in columns_by_table[table_name]
        and (getattr(columns_by_table[table_name][column_name]["type"], "length", 0) or 0) < 36
    )


def _affected_foreign_keys(
    bind,
    columns_to_widen: tuple[tuple[str, str, bool], ...],
) -> list[dict]:
    inspector = sa.inspect(bind)
    target_columns = {
        (table_name, column_name)
        for table_name, column_name, _ in columns_to_widen
    }
    foreign_keys: list[dict] = []
    for table_name in inspector.get_table_names():
        for foreign_key in inspector.get_foreign_keys(table_name):
            local_columns = list(foreign_key.get("constrained_columns") or ())
            remote_table = foreign_key.get("referred_table")
            remote_columns = list(foreign_key.get("referred_columns") or ())
            affected = any(
                (table_name, column_name) in target_columns
                for column_name in local_columns
            ) or any(
                (remote_table, column_name) in target_columns
                for column_name in remote_columns
            )
            if not affected:
                continue
            name = foreign_key.get("name")
            if not name:
                raise RuntimeError(
                    f"Cannot widen support UUID columns: unnamed foreign key on {table_name}"
                )
            options = {
                key: value
                for key, value in (foreign_key.get("options") or {}).items()
                if value is not None
            }
            foreign_keys.append(
                {
                    "name": name,
                    "source_table": table_name,
                    "referent_table": remote_table,
                    "local_cols": local_columns,
                    "remote_cols": remote_columns,
                    "options": options,
                }
            )
    return foreign_keys
