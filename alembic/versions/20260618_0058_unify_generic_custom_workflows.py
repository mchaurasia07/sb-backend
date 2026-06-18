"""unify generic workflows into custom workflow tables

Revision ID: 20260618_0058
Revises: 20260618_0057
Create Date: 2026-06-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260618_0058"
down_revision: str | None = "20260618_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if _table_exists("custom_story_workflows"):
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("story_type", sa.String(length=32), nullable=False, server_default="CUSTOM"),
        )
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("generic_story_id", sa.String(length=36), nullable=True),
        )
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
        )
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("genre", sa.String(length=100), nullable=True),
        )
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("publish_status", sa.String(length=32), nullable=True),
        )
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("source_title", sa.String(length=255), nullable=True),
        )
        _add_column_if_missing(
            "custom_story_workflows",
            sa.Column("input_request", sa.JSON(), nullable=True),
        )
        _alter_nullable_if_exists(
            "custom_story_workflows",
            "child_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        _create_index_if_missing("ix_custom_story_workflows_story_type", "custom_story_workflows", ["story_type"])
        _create_index_if_missing(
            "ix_custom_story_workflows_generic_story_id",
            "custom_story_workflows",
            ["generic_story_id"],
        )

    if _table_exists("custom_story_batch_jobs"):
        _add_column_if_missing(
            "custom_story_batch_jobs",
            sa.Column("generic_story_id", sa.String(length=36), nullable=True),
        )
        _create_index_if_missing(
            "ix_custom_story_batch_jobs_generic_story_id",
            "custom_story_batch_jobs",
            ["generic_story_id"],
        )

    if _table_exists("custom_story_input_safety_audits"):
        _alter_nullable_if_exists(
            "custom_story_input_safety_audits",
            "child_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )

    _drop_table_if_exists("generic_story_batch_jobs")
    _drop_table_if_exists("generic_story_workflow_steps")
    _drop_table_if_exists("generic_story_workflows")


def downgrade() -> None:
    # This migration intentionally drops legacy workflow history by product decision.
    # Recreating the dropped tables without data would be misleading, so downgrade
    # only removes the new nullable columns/indexes where safe.
    if _table_exists("custom_story_batch_jobs"):
        _drop_index_if_exists("ix_custom_story_batch_jobs_generic_story_id", "custom_story_batch_jobs")
        _drop_column_if_exists("custom_story_batch_jobs", "generic_story_id")

    if _table_exists("custom_story_workflows"):
        _drop_index_if_exists("ix_custom_story_workflows_generic_story_id", "custom_story_workflows")
        _drop_index_if_exists("ix_custom_story_workflows_story_type", "custom_story_workflows")
        for column_name in (
            "input_request",
            "source_title",
            "publish_status",
            "genre",
            "language",
            "generic_story_id",
            "story_type",
        ):
            _drop_column_if_exists("custom_story_workflows", column_name)


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)


def _alter_nullable_if_exists(table_name: str, column_name: str, *, existing_type, nullable: bool) -> None:
    if _column_exists(table_name, column_name):
        inspector = sa.inspect(op.get_bind())
        foreign_keys = [
            fk
            for fk in inspector.get_foreign_keys(table_name)
            if column_name in (fk.get("constrained_columns") or []) and fk.get("name")
        ]
        for fk in foreign_keys:
            op.drop_constraint(fk["name"], table_name, type_="foreignkey")
        op.alter_column(table_name, column_name, existing_type=existing_type, nullable=nullable)
        for fk in foreign_keys:
            op.create_foreign_key(
                fk["name"],
                table_name,
                fk["referred_table"],
                fk.get("constrained_columns") or [],
                fk.get("referred_columns") or [],
                source_schema=fk.get("constrained_schema"),
                referent_schema=fk.get("referred_schema"),
                onupdate=(fk.get("options") or {}).get("onupdate"),
                ondelete=(fk.get("options") or {}).get("ondelete"),
            )


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_table_if_exists(table_name: str) -> None:
    if _table_exists(table_name):
        op.drop_table(table_name)
