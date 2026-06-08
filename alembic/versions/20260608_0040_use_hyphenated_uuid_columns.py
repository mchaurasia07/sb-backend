"""use hyphenated uuid columns

Revision ID: 20260608_0040
Revises: 20260608_0039
Create Date: 2026-06-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260608_0040"
down_revision: str | None = "20260608_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("child_activity_logs", "child_id", False),
    ("child_activity_logs", "resource_id", True),
    ("child_activity_logs", "id", False),
    ("child_audios", "child_id", False),
    ("child_audios", "audio_id", False),
    ("child_audios", "id", False),
    ("child_books", "child_id", False),
    ("child_books", "story_id", False),
    ("child_books", "id", False),
    ("child_profiles", "user_id", False),
    ("child_profiles", "id", False),
    ("generic_audios", "id", False),
    ("generic_stories", "id", False),
    ("generic_story_batch_jobs", "generic_story_id", True),
    ("generic_story_batch_jobs", "workflow_id", False),
    ("generic_story_batch_jobs", "id", False),
    ("generic_story_contents", "generic_story_id", False),
    ("generic_story_contents", "id", False),
    ("generic_story_workflows", "user_id", False),
    ("generic_story_workflows", "generic_story_id", True),
    ("generic_story_workflows", "id", False),
    ("notifications", "user_id", True),
    ("notifications", "child_id", True),
    ("notifications", "id", False),
    ("otp_verifications", "user_id", False),
    ("otp_verifications", "id", False),
    ("push_device_tokens", "user_id", False),
    ("push_device_tokens", "child_id", True),
    ("push_device_tokens", "id", False),
    ("refresh_tokens", "user_id", False),
    ("refresh_tokens", "id", False),
    ("stories", "user_id", False),
    ("stories", "child_id", False),
    ("stories", "id", False),
    ("story_batch_jobs", "story_id", False),
    ("story_batch_jobs", "id", False),
    ("story_contents", "story_id", False),
    ("story_contents", "id", False),
    ("story_pages", "story_id", False),
    ("story_pages", "id", False),
    ("story_steps", "story_id", False),
    ("story_steps", "id", False),
    ("users", "active_child_profile_id", True),
    ("users", "id", False),
)


EXPECTED_FOREIGN_KEYS: tuple[dict, ...] = (
    {
        "name": "fk_users_active_child_profile_id_child_profiles",
        "source_table": "users",
        "referent_table": "child_profiles",
        "local_cols": ["active_child_profile_id"],
        "remote_cols": ["id"],
    },
    {
        "name": "fk_child_profiles_user_id_users",
        "source_table": "child_profiles",
        "referent_table": "users",
        "local_cols": ["user_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_stories_user_id_users",
        "source_table": "stories",
        "referent_table": "users",
        "local_cols": ["user_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_stories_child_id_child_profiles",
        "source_table": "stories",
        "referent_table": "child_profiles",
        "local_cols": ["child_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_story_pages_story_id_stories",
        "source_table": "story_pages",
        "referent_table": "stories",
        "local_cols": ["story_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_story_steps_story_id_stories",
        "source_table": "story_steps",
        "referent_table": "stories",
        "local_cols": ["story_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_story_contents_story_id_stories",
        "source_table": "story_contents",
        "referent_table": "stories",
        "local_cols": ["story_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_child_books_child_id_child_profiles",
        "source_table": "child_books",
        "referent_table": "child_profiles",
        "local_cols": ["child_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_generic_story_contents_generic_story_id_generic_stories",
        "source_table": "generic_story_contents",
        "referent_table": "generic_stories",
        "local_cols": ["generic_story_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_child_activity_logs_child_id_child_profiles",
        "source_table": "child_activity_logs",
        "referent_table": "child_profiles",
        "local_cols": ["child_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_child_audios_child_id_child_profiles",
        "source_table": "child_audios",
        "referent_table": "child_profiles",
        "local_cols": ["child_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_child_audios_audio_id_generic_audios",
        "source_table": "child_audios",
        "referent_table": "generic_audios",
        "local_cols": ["audio_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_story_batch_jobs_story_id_stories",
        "source_table": "story_batch_jobs",
        "referent_table": "stories",
        "local_cols": ["story_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_push_device_tokens_user_id_users",
        "source_table": "push_device_tokens",
        "referent_table": "users",
        "local_cols": ["user_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_push_device_tokens_child_id_child_profiles",
        "source_table": "push_device_tokens",
        "referent_table": "child_profiles",
        "local_cols": ["child_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_refresh_tokens_user_id_users",
        "source_table": "refresh_tokens",
        "referent_table": "users",
        "local_cols": ["user_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_otp_verifications_user_id_users",
        "source_table": "otp_verifications",
        "referent_table": "users",
        "local_cols": ["user_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_generic_story_workflows_user_id_users",
        "source_table": "generic_story_workflows",
        "referent_table": "users",
        "local_cols": ["user_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_generic_story_workflows_generic_story_id_generic_stories",
        "source_table": "generic_story_workflows",
        "referent_table": "generic_stories",
        "local_cols": ["generic_story_id"],
        "remote_cols": ["id"],
        "ondelete": "SET NULL",
    },
    {
        "name": "fk_generic_story_batch_jobs_generic_story_id_generic_stories",
        "source_table": "generic_story_batch_jobs",
        "referent_table": "generic_stories",
        "local_cols": ["generic_story_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
    {
        "name": "fk_generic_story_batch_jobs_workflow_id_generic_story_workflows",
        "source_table": "generic_story_batch_jobs",
        "referent_table": "generic_story_workflows",
        "local_cols": ["workflow_id"],
        "remote_cols": ["id"],
        "ondelete": "CASCADE",
    },
)


def upgrade() -> None:
    bind = op.get_bind()
    uuid_columns = _uuid_columns(bind)
    foreign_keys = _uuid_foreign_keys(bind, uuid_columns)
    _drop_foreign_keys(foreign_keys)
    _alter_uuid_columns(uuid_columns, sa.CHAR(36), sa.CHAR(32))
    _hyphenate_uuid_values(uuid_columns)
    _create_foreign_keys(foreign_keys)
    _ensure_expected_foreign_keys(bind)


def downgrade() -> None:
    bind = op.get_bind()
    uuid_columns = _uuid_columns(bind)
    foreign_keys = _uuid_foreign_keys(bind, uuid_columns)
    _drop_foreign_keys(foreign_keys)
    _compact_uuid_values(uuid_columns)
    _alter_uuid_columns(uuid_columns, sa.CHAR(32), sa.CHAR(36))
    _create_foreign_keys(foreign_keys)
    _ensure_expected_foreign_keys(bind)


def _uuid_columns(bind) -> tuple[tuple[str, str, bool], ...]:
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    columns_by_table = {
        table_name: {column["name"]: column for column in inspector.get_columns(table_name)}
        for table_name in table_names
    }
    uuid_columns = {
        (table_name, column_name): nullable
        for table_name, column_name, nullable in UUID_COLUMNS
        if table_name in table_names
    }

    base_uuid_keys = set(uuid_columns)
    for table_name in sorted(table_names):
        for foreign_key in inspector.get_foreign_keys(table_name):
            referred_table = foreign_key.get("referred_table")
            constrained_columns = tuple(foreign_key.get("constrained_columns") or ())
            referred_columns = tuple(foreign_key.get("referred_columns") or ())
            for local_column, remote_column in zip(constrained_columns, referred_columns, strict=False):
                if (referred_table, remote_column) not in base_uuid_keys:
                    continue
                column = columns_by_table.get(table_name, {}).get(local_column)
                if column is None:
                    continue
                uuid_columns[(table_name, local_column)] = bool(column.get("nullable", True))

    ordered_columns: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    for table_name, column_name, nullable in UUID_COLUMNS:
        if (table_name, column_name) in uuid_columns:
            ordered_columns.append((table_name, column_name, nullable))
            seen.add((table_name, column_name))
    for (table_name, column_name), nullable in sorted(uuid_columns.items()):
        if (table_name, column_name) not in seen:
            ordered_columns.append((table_name, column_name, nullable))
    return tuple(ordered_columns)


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
        _create_foreign_key(foreign_key)


def _create_foreign_key(foreign_key: dict) -> None:
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


def _ensure_expected_foreign_keys(bind) -> None:
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    existing_signatures = _foreign_key_signatures(inspector, table_names)
    for foreign_key in EXPECTED_FOREIGN_KEYS:
        if foreign_key["source_table"] not in table_names or foreign_key["referent_table"] not in table_names:
            continue
        signature = _foreign_key_signature(foreign_key)
        if signature in existing_signatures:
            continue
        _create_foreign_key(foreign_key)


def _foreign_key_signatures(inspector, table_names: set[str]) -> set[tuple]:
    signatures: set[tuple] = set()
    for table_name in sorted(table_names):
        for foreign_key in inspector.get_foreign_keys(table_name):
            signatures.add(
                (
                    table_name,
                    tuple(foreign_key.get("constrained_columns") or ()),
                    foreign_key.get("referred_table"),
                    tuple(foreign_key.get("referred_columns") or ()),
                )
            )
    return signatures


def _foreign_key_signature(foreign_key: dict) -> tuple:
    return (
        foreign_key["source_table"],
        tuple(foreign_key["local_cols"]),
        foreign_key["referent_table"],
        tuple(foreign_key["remote_cols"]),
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


def _compact_uuid_values(uuid_columns: tuple[tuple[str, str, bool], ...]) -> None:
    for table_name, column_name, _ in uuid_columns:
        op.execute(
            sa.text(
                f"UPDATE `{table_name}` "
                f"SET `{column_name}` = REPLACE(`{column_name}`, '-', '') "
                f"WHERE `{column_name}` LIKE '%-%'"
            )
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
