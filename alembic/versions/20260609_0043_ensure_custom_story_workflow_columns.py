"""ensure custom story workflow columns

Revision ID: 20260609_0043
Revises: 20260608_0042
Create Date: 2026-06-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260609_0043"
down_revision: str | None = "20260608_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _ensure_custom_story_workflow_columns()
    _ensure_custom_story_workflow_step_columns()
    _ensure_custom_story_batch_job_columns()


def downgrade() -> None:
    # This is a live-schema repair migration for databases that already had
    # partial custom workflow tables. Leave columns in place on downgrade.
    pass


def _ensure_custom_story_workflow_columns() -> None:
    table_name = "custom_story_workflows"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(table_name, sa.Column("generation_mode", sa.String(length=32), nullable=False, server_default="INPUT_DRIVEN"))
    _add_column_if_missing(table_name, sa.Column("processing_mode", sa.String(length=32), nullable=False, server_default="instant"))
    _add_column_if_missing(
        table_name,
        sa.Column("age_group", sa.Enum("0-3", "3-6", "6-9", name="agegroup", native_enum=False), nullable=False, server_default="3-6"),
    )
    _add_column_if_missing(table_name, sa.Column("category", sa.String(length=100), nullable=True))
    _add_column_if_missing(table_name, sa.Column("learning_goal", sa.String(length=500), nullable=True))
    _add_column_if_missing(table_name, sa.Column("context", sa.Text(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("event_description", sa.Text(), nullable=True))
    _add_column_if_missing(
        table_name,
        sa.Column(
            "status",
            sa.Enum("PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", name="customstoryworkflowstatus", native_enum=False),
            nullable=False,
            server_default="PENDING",
        ),
    )
    _add_column_if_missing(table_name, sa.Column("current_step", sa.String(length=64), nullable=True))
    _add_column_if_missing(table_name, sa.Column("error_message", sa.Text(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("input_request", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("story_plan_json", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("story_plan_validated", sa.Boolean(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("story_json", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("image_plan_json", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("image_plan_validated", sa.Boolean(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("title", sa.String(length=255), nullable=True))
    _add_column_if_missing(table_name, sa.Column("summary", sa.Text(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("moral", sa.String(length=255), nullable=True))
    _add_column_if_missing(table_name, sa.Column("ai_provider", sa.String(length=32), nullable=True))
    _add_column_if_missing(table_name, sa.Column("text_model", sa.String(length=128), nullable=True))
    _add_column_if_missing(table_name, sa.Column("image_model", sa.String(length=128), nullable=True))
    _add_column_if_missing(table_name, sa.Column("reference_image_model", sa.String(length=128), nullable=True))
    _add_column_if_missing(table_name, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    _add_column_if_missing(table_name, sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def _ensure_custom_story_workflow_step_columns() -> None:
    table_name = "custom_story_workflow_steps"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(
        table_name,
        sa.Column(
            "step_name",
            sa.Enum(
                "STORY_PLAN_GENERATION",
                "STORY_PLAN_VALIDATION",
                "STORY_GENERATION",
                "IMAGE_PLAN_GENERATION",
                "IMAGE_PLAN_VALIDATION",
                "IMAGE_GENERATION",
                "NARRATION_GENERATION",
                "PUBLISH_STORY",
                name="customstoryworkflowstep",
                native_enum=False,
            ),
            nullable=False,
            server_default="STORY_PLAN_GENERATION",
        ),
    )
    _add_column_if_missing(
        table_name,
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "IN_PROGRESS",
                "SUBMITTED_BATCH_JOB",
                "COMPLETED",
                "FAILED",
                name="stepstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
    )
    _add_column_if_missing(table_name, sa.Column("input_json", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("prompt", sa.Text(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("output_json", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("error_message", sa.Text(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(table_name, sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(table_name, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    _add_column_if_missing(table_name, sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def _ensure_custom_story_batch_job_columns() -> None:
    table_name = "custom_story_batch_jobs"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(table_name, sa.Column("story_id", sa.CHAR(36), nullable=True))
    _add_column_if_missing(table_name, sa.Column("job_type", sa.Enum("IMAGE", "AUDIO", name="storybatchjobtype", native_enum=False), nullable=False, server_default="IMAGE"))
    _add_column_if_missing(
        table_name,
        sa.Column(
            "status",
            sa.Enum("SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", name="storybatchjobstatus", native_enum=False),
            nullable=False,
            server_default="SUBMITTED",
        ),
    )
    _add_column_if_missing(table_name, sa.Column("provider", sa.String(length=32), nullable=False, server_default="google"))
    _add_column_if_missing(table_name, sa.Column("provider_job_name", sa.String(length=255), nullable=True))
    _add_column_if_missing(table_name, sa.Column("provider_model", sa.String(length=128), nullable=True))
    _add_column_if_missing(table_name, sa.Column("provider_state", sa.String(length=64), nullable=True))
    _add_column_if_missing(table_name, sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    _add_column_if_missing(table_name, sa.Column("expected_item_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("completed_item_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("failed_item_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(table_name, sa.Column("request_keys", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("missing_keys", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("request_payload", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("response_payload", sa.JSON(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("error_message", sa.Text(), nullable=True))
    _add_column_if_missing(table_name, sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    _add_column_if_missing(table_name, sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


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
