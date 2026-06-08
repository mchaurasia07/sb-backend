"""hyphenate existing uuid values

Revision ID: 20260608_0042
Revises: 20260608_0041
Create Date: 2026-06-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260608_0042"
down_revision: str | None = "20260608_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("users", "id"),
    ("users", "active_child_profile_id"),
    ("child_profiles", "id"),
    ("child_profiles", "user_id"),
    ("otp_verifications", "id"),
    ("otp_verifications", "user_id"),
    ("refresh_tokens", "id"),
    ("refresh_tokens", "user_id"),
    ("stories", "id"),
    ("stories", "user_id"),
    ("stories", "child_id"),
    ("story_pages", "id"),
    ("story_pages", "story_id"),
    ("story_steps", "id"),
    ("story_steps", "story_id"),
    ("story_contents", "id"),
    ("story_contents", "story_id"),
    ("story_batch_jobs", "id"),
    ("story_batch_jobs", "story_id"),
    ("generic_stories", "id"),
    ("generic_story_contents", "id"),
    ("generic_story_contents", "generic_story_id"),
    ("generic_story_workflows", "id"),
    ("generic_story_workflows", "user_id"),
    ("generic_story_workflows", "generic_story_id"),
    ("generic_story_batch_jobs", "id"),
    ("generic_story_batch_jobs", "generic_story_id"),
    ("generic_story_batch_jobs", "workflow_id"),
    ("custom_story_workflows", "id"),
    ("custom_story_workflows", "user_id"),
    ("custom_story_workflows", "child_id"),
    ("custom_story_workflows", "story_id"),
    ("custom_story_workflow_steps", "id"),
    ("custom_story_workflow_steps", "workflow_id"),
    ("custom_story_batch_jobs", "id"),
    ("custom_story_batch_jobs", "workflow_id"),
    ("custom_story_batch_jobs", "story_id"),
    ("child_books", "id"),
    ("child_books", "child_id"),
    ("child_books", "story_id"),
    ("generic_audios", "id"),
    ("child_audios", "id"),
    ("child_audios", "child_id"),
    ("child_audios", "audio_id"),
    ("child_activity_logs", "id"),
    ("child_activity_logs", "child_id"),
    ("child_activity_logs", "resource_id"),
    ("push_device_tokens", "id"),
    ("push_device_tokens", "user_id"),
    ("push_device_tokens", "child_id"),
    ("notifications", "id"),
    ("notifications", "user_id"),
    ("notifications", "child_id"),
)


def upgrade() -> None:
    bind = op.get_bind()
    uuid_columns = _existing_uuid_columns(bind)
    foreign_keys = _uuid_foreign_keys(bind, uuid_columns)
    _drop_foreign_keys(foreign_keys)
    _hyphenate_uuid_values(uuid_columns)
    _create_foreign_keys(foreign_keys)


def downgrade() -> None:
    bind = op.get_bind()
    uuid_columns = _existing_uuid_columns(bind)
    foreign_keys = _uuid_foreign_keys(bind, uuid_columns)
    _drop_foreign_keys(foreign_keys)
    _compact_uuid_values(uuid_columns)
    _create_foreign_keys(foreign_keys)


def _existing_uuid_columns(bind) -> tuple[tuple[str, str], ...]:
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    columns_by_table = {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in table_names
    }
    return tuple(
        (table_name, column_name)
        for table_name, column_name in UUID_COLUMNS
        if table_name in table_names and column_name in columns_by_table.get(table_name, set())
    )


def _uuid_foreign_keys(bind, uuid_columns: tuple[tuple[str, str], ...]) -> list[dict]:
    inspector = sa.inspect(bind)
    uuid_column_keys = set(uuid_columns)
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
                raise RuntimeError(f"Cannot normalize UUID data because foreign key on {table_name} has no name")
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


def _hyphenate_uuid_values(uuid_columns: tuple[tuple[str, str], ...]) -> None:
    for table_name, column_name in uuid_columns:
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


def _compact_uuid_values(uuid_columns: tuple[tuple[str, str], ...]) -> None:
    for table_name, column_name in uuid_columns:
        op.execute(
            sa.text(
                f"UPDATE `{table_name}` "
                f"SET `{column_name}` = REPLACE(`{column_name}`, '-', '') "
                f"WHERE `{column_name}` LIKE '%-%'"
            )
        )
