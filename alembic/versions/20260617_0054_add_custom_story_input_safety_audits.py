"""add custom story input safety audits

Revision ID: 20260617_0054
Revises: 20260616_0053
Create Date: 2026-06-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260617_0054"
down_revision: str | None = "20260616_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_table_if_missing(
        "custom_story_input_safety_audits",
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("child_id", sa.CHAR(36), nullable=False),
        sa.Column("workflow_id", sa.CHAR(36), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "IN_PROGRESS",
                "SAFE",
                "UNSAFE",
                "ERROR",
                name="customstoryinputsafetyauditstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="IN_PROGRESS",
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("request_idea_json", sa.JSON(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("safe", sa.Boolean(), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=True),
        sa.Column("blocked_categories", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("safe_rewrite", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["custom_story_workflows.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing("ix_custom_story_input_safety_audits_user_id", "custom_story_input_safety_audits", ["user_id"])
    _create_index_if_missing(
        "ix_custom_story_input_safety_audits_child_id",
        "custom_story_input_safety_audits",
        ["child_id"],
    )
    _create_index_if_missing(
        "ix_custom_story_input_safety_audits_workflow_id",
        "custom_story_input_safety_audits",
        ["workflow_id"],
    )
    _create_index_if_missing(
        "ix_custom_story_input_safety_audits_status",
        "custom_story_input_safety_audits",
        ["status"],
    )
    _create_index_if_missing(
        "ix_custom_story_input_safety_audits_created_at",
        "custom_story_input_safety_audits",
        ["created_at"],
    )
    _create_index_if_missing(
        "ix_custom_story_input_safety_audits_user_created_at",
        "custom_story_input_safety_audits",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_custom_story_input_safety_audits_user_created_at", "custom_story_input_safety_audits")
    _drop_index_if_exists("ix_custom_story_input_safety_audits_created_at", "custom_story_input_safety_audits")
    _drop_index_if_exists("ix_custom_story_input_safety_audits_status", "custom_story_input_safety_audits")
    _drop_index_if_exists("ix_custom_story_input_safety_audits_workflow_id", "custom_story_input_safety_audits")
    _drop_index_if_exists("ix_custom_story_input_safety_audits_child_id", "custom_story_input_safety_audits")
    _drop_index_if_exists("ix_custom_story_input_safety_audits_user_id", "custom_story_input_safety_audits")
    _drop_table_if_exists("custom_story_input_safety_audits")


def _create_table_if_missing(table_name: str, *columns, **kwargs) -> None:
    if _table_exists(table_name):
        return
    op.create_table(table_name, *columns, **kwargs)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _table_exists(table_name) or _index_exists(table_name, index_name):
        return
    op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _table_exists(table_name) and _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_table_if_exists(table_name: str) -> None:
    if _table_exists(table_name):
        op.drop_table(table_name)


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))
