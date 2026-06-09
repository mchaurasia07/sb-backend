"""widen custom story workflow uuids

Revision ID: 20260609_0044
Revises: 20260609_0043
Create Date: 2026-06-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260609_0044"
down_revision: str | None = "20260609_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("custom_story_workflows", "id", False),
    ("custom_story_workflows", "user_id", False),
    ("custom_story_workflows", "child_id", False),
    ("custom_story_workflows", "story_id", True),
    ("custom_story_workflow_steps", "id", False),
    ("custom_story_workflow_steps", "workflow_id", False),
    ("custom_story_batch_jobs", "id", False),
    ("custom_story_batch_jobs", "workflow_id", False),
    ("custom_story_batch_jobs", "story_id", True),
)


def upgrade() -> None:
    bind = op.get_bind()
    uuid_columns = _existing_uuid_columns(bind)
    foreign_keys = _uuid_foreign_keys(bind, uuid_columns)
    _drop_foreign_keys(foreign_keys)
    _alter_uuid_columns(uuid_columns, sa.CHAR(36), sa.CHAR(32))
    _hyphenate_uuid_values(uuid_columns)
    _create_foreign_keys(foreign_keys)


def downgrade() -> None:
    # Live-schema repair only. Do not shrink columns back to CHAR(32).
    pass


def _existing_uuid_columns(bind) -> tuple[tuple[str, str, bool], ...]:
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    columns_by_table = {
        table_name: {column["name"]: column for column in inspector.get_columns(table_name)}
        for table_name in table_names
    }
    return tuple(
        (table_name, column_name, nullable)
        for table_name, column_name, nullable in UUID_COLUMNS
        if table_name in table_names and column_name in columns_by_table.get(table_name, {})
    )


def _uuid_foreign_keys(bind, uuid_columns: tuple[tuple[str, str, bool], ...]) -> list[dict]:
    inspector = sa.inspect(bind)
    uuid_column_keys = {(table, column) for table, column, _ in uuid_columns}
    foreign_keys: list[dict] = []
    for table_name in sorted(inspector.get_table_names()):
        for foreign_key in inspector.get_foreign_keys(table_name):
            constrained_columns = tuple(foreign_key.get("constrained_columns") or ())
            referred_table = foreign_key.get("referred_table")
            referred_columns = tuple(foreign_key.get("referred_columns") or ())
            touches_uuid_column = any((table_name, column) in uuid_column_keys for column in constrained_columns)
            touches_uuid_column = touches_uuid_column or any(
                (referred_table, column) in uuid_column_keys for column in referred_columns
            )
            if not touches_uuid_column:
                continue
            name = foreign_key.get("name")
            if not name:
                raise RuntimeError(f"Cannot alter UUID column because foreign key on {table_name} has no name")
            foreign_keys.append(
                {
                    "name": name,
                    "source_table": table_name,
                    "referent_table": referred_table,
                    "local_cols": list(constrained_columns),
                    "remote_cols": list(referred_columns),
                    "ondelete": (foreign_key.get("options") or {}).get("ondelete"),
                    "onupdate": (foreign_key.get("options") or {}).get("onupdate"),
                    "source_schema": foreign_key.get("constrained_schema"),
                    "referent_schema": foreign_key.get("referred_schema"),
                }
            )
    return foreign_keys


def _drop_foreign_keys(foreign_keys: list[dict]) -> None:
    for foreign_key in foreign_keys:
        op.drop_constraint(
            foreign_key["name"],
            foreign_key["source_table"],
            type_="foreignkey",
            schema=foreign_key.get("source_schema"),
        )


def _create_foreign_keys(foreign_keys: list[dict]) -> None:
    for foreign_key in foreign_keys:
        kwargs = {
            "source_schema": foreign_key.get("source_schema"),
            "referent_schema": foreign_key.get("referent_schema"),
            "ondelete": foreign_key.get("ondelete"),
            "onupdate": foreign_key.get("onupdate"),
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        op.create_foreign_key(
            foreign_key["name"],
            foreign_key["source_table"],
            foreign_key["referent_table"],
            foreign_key["local_cols"],
            foreign_key["remote_cols"],
            **kwargs,
        )


def _alter_uuid_columns(
    uuid_columns: tuple[tuple[str, str, bool], ...],
    new_type: sa.CHAR,
    existing_type: sa.CHAR,
) -> None:
    for table_name, column_name, nullable in uuid_columns:
        op.alter_column(
            table_name,
            column_name,
            type_=new_type,
            existing_type=existing_type,
            existing_nullable=nullable,
            nullable=nullable,
        )


def _hyphenate_uuid_values(uuid_columns: tuple[tuple[str, str, bool], ...]) -> None:
    for table_name, column_name, _ in uuid_columns:
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
                f"WHERE `{column_name}` IS NOT NULL "
                f"AND LENGTH(`{column_name}`) = 32 "
                f"AND `{column_name}` NOT LIKE '%-%'"
            )
        )
