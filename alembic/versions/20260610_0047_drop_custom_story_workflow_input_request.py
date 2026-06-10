"""drop custom story workflow input request

Revision ID: 20260610_0047
Revises: 20260609_0046
Create Date: 2026-06-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260610_0047"
down_revision: str | None = "20260609_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(table_name, sa.Column("reader_category", sa.String(length=64), nullable=True))
    _add_column_if_missing(table_name, sa.Column("use_child_character", sa.Boolean(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("execute_image", sa.Boolean(), nullable=False, server_default="1"))
    _add_column_if_missing(table_name, sa.Column("execute_narration", sa.Boolean(), nullable=False, server_default="1"))
    _add_column_if_missing(table_name, sa.Column("skip_validation", sa.Boolean(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("execute_workflow", sa.Boolean(), nullable=False, server_default="0"))

    _backfill_reader_category(table_name)
    if _column_exists(table_name, "input_request"):
        _backfill_from_input_request(table_name)
        op.drop_column(table_name, "input_request")


def downgrade() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(table_name, sa.Column("input_request", sa.JSON(), nullable=True))
    for column_name in (
        "execute_workflow",
        "skip_validation",
        "execute_narration",
        "execute_image",
        "use_child_character",
        "reader_category",
    ):
        if _column_exists(table_name, column_name):
            op.drop_column(table_name, column_name)


def _backfill_reader_category(table_name: str) -> None:
    op.execute(
        f"""
        UPDATE {table_name}
        SET reader_category = CASE
            WHEN age_group = '0-3' THEN 'Infant Toddler'
            WHEN age_group = '3-6' THEN 'Early Reader'
            WHEN age_group = '6-9' THEN 'Growing Reader'
            ELSE reader_category
        END
        WHERE reader_category IS NULL
        """
    )


def _backfill_from_input_request(table_name: str) -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name in {"mysql", "mariadb"}:
        op.execute(
            f"""
            UPDATE {table_name}
            SET
                reader_category = COALESCE(
                    NULLIF(JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.reader_category')), 'null'),
                    reader_category
                ),
                use_child_character = CASE
                    WHEN LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.use_child_character')), 'false')) IN ('true', '1')
                    THEN 1 ELSE use_child_character
                END,
                execute_image = CASE
                    WHEN LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.execute_image')), '')) IN ('false', '0')
                    THEN 0
                    WHEN LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.skip_image_generation')), '')) IN ('true', '1')
                    THEN 0
                    ELSE execute_image
                END,
                execute_narration = CASE
                    WHEN LOWER(COALESCE(
                        JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.execute_narration')),
                        JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.execute_narrration')),
                        'true'
                    )) IN ('false', '0')
                    THEN 0 ELSE execute_narration
                END,
                skip_validation = CASE
                    WHEN LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.skip_validation')), 'false')) IN ('true', '1')
                    THEN 1 ELSE skip_validation
                END,
                execute_workflow = CASE
                    WHEN LOWER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(input_request, '$.execute_workflow')), 'true')) IN ('false', '0')
                    THEN 0 ELSE execute_workflow
                END
            WHERE input_request IS NOT NULL
            """
        )
        return

    if dialect_name == "postgresql":
        op.execute(
            f"""
            UPDATE {table_name}
            SET
                reader_category = COALESCE(NULLIF(input_request ->> 'reader_category', ''), reader_category),
                use_child_character = CASE
                    WHEN LOWER(COALESCE(input_request ->> 'use_child_character', 'false')) IN ('true', '1')
                    THEN TRUE ELSE use_child_character
                END,
                execute_image = CASE
                    WHEN LOWER(COALESCE(input_request ->> 'execute_image', '')) IN ('false', '0')
                    THEN FALSE
                    WHEN LOWER(COALESCE(input_request ->> 'skip_image_generation', '')) IN ('true', '1')
                    THEN FALSE
                    ELSE execute_image
                END,
                execute_narration = CASE
                    WHEN LOWER(COALESCE(input_request ->> 'execute_narration', input_request ->> 'execute_narrration', 'true')) IN ('false', '0')
                    THEN FALSE ELSE execute_narration
                END,
                skip_validation = CASE
                    WHEN LOWER(COALESCE(input_request ->> 'skip_validation', 'false')) IN ('true', '1')
                    THEN TRUE ELSE skip_validation
                END,
                execute_workflow = CASE
                    WHEN LOWER(COALESCE(input_request ->> 'execute_workflow', 'true')) IN ('false', '0')
                    THEN FALSE ELSE execute_workflow
                END
            WHERE input_request IS NOT NULL
            """
        )
        return

    op.execute(
        f"""
        UPDATE {table_name}
        SET
            reader_category = COALESCE(NULLIF(json_extract(input_request, '$.reader_category'), ''), reader_category),
            use_child_character = CASE
                WHEN LOWER(COALESCE(json_extract(input_request, '$.use_child_character'), 'false')) IN ('true', '1')
                THEN 1 ELSE use_child_character
            END,
            execute_image = CASE
                WHEN LOWER(COALESCE(json_extract(input_request, '$.execute_image'), '')) IN ('false', '0')
                THEN 0
                WHEN LOWER(COALESCE(json_extract(input_request, '$.skip_image_generation'), '')) IN ('true', '1')
                THEN 0
                ELSE execute_image
            END,
            execute_narration = CASE
                WHEN LOWER(COALESCE(
                    json_extract(input_request, '$.execute_narration'),
                    json_extract(input_request, '$.execute_narrration'),
                    'true'
                )) IN ('false', '0')
                THEN 0 ELSE execute_narration
            END,
            skip_validation = CASE
                WHEN LOWER(COALESCE(json_extract(input_request, '$.skip_validation'), 'false')) IN ('true', '1')
                THEN 1 ELSE skip_validation
            END,
            execute_workflow = CASE
                WHEN LOWER(COALESCE(json_extract(input_request, '$.execute_workflow'), 'true')) IN ('false', '0')
                THEN 0 ELSE execute_workflow
            END
        WHERE input_request IS NOT NULL
        """
    )


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
