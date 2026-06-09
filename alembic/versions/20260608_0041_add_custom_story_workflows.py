"""add custom story workflows

Revision ID: 20260608_0041
Revises: 20260608_0040
Create Date: 2026-06-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260608_0041"
down_revision: str | None = "20260608_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_table_if_missing(
        "custom_story_workflows",
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("child_id", sa.CHAR(36), nullable=False),
        sa.Column("story_id", sa.CHAR(36), nullable=True),
        sa.Column("generation_mode", sa.String(length=32), nullable=False),
        sa.Column("processing_mode", sa.String(length=32), nullable=False),
        sa.Column("age_group", sa.Enum("0-3", "3-6", "6-9", name="agegroup", native_enum=False), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("learning_goal", sa.String(length=500), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("event_description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", name="customstoryworkflowstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_request", sa.JSON(), nullable=True),
        sa.Column("story_plan_json", sa.JSON(), nullable=True),
        sa.Column("story_plan_validated", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("story_json", sa.JSON(), nullable=True),
        sa.Column("image_plan_json", sa.JSON(), nullable=True),
        sa.Column("image_plan_validated", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("moral", sa.String(length=255), nullable=True),
        sa.Column("ai_provider", sa.String(length=32), nullable=True),
        sa.Column("text_model", sa.String(length=128), nullable=True),
        sa.Column("image_model", sa.String(length=128), nullable=True),
        sa.Column("reference_image_model", sa.String(length=128), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing("ix_custom_story_workflows_child_id", "custom_story_workflows", ["child_id"])
    _create_index_if_missing("ix_custom_story_workflows_created_at", "custom_story_workflows", ["created_at"])
    _create_index_if_missing("ix_custom_story_workflows_status", "custom_story_workflows", ["status"])
    _create_index_if_missing("ix_custom_story_workflows_story_id", "custom_story_workflows", ["story_id"])
    _create_index_if_missing("ix_custom_story_workflows_user_created_at", "custom_story_workflows", ["user_id", "created_at"])
    _create_index_if_missing("ix_custom_story_workflows_user_id", "custom_story_workflows", ["user_id"])

    _create_table_if_missing(
        "custom_story_workflow_steps",
        sa.Column("workflow_id", sa.CHAR(36), nullable=False),
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
        ),
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
        ),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["custom_story_workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing("ix_custom_story_workflow_steps_status", "custom_story_workflow_steps", ["status"])
    _create_index_if_missing("ix_custom_story_workflow_steps_step_name", "custom_story_workflow_steps", ["step_name"])
    _create_index_if_missing("ix_custom_story_workflow_steps_workflow_id", "custom_story_workflow_steps", ["workflow_id"])

    _create_table_if_missing(
        "custom_story_batch_jobs",
        sa.Column("workflow_id", sa.CHAR(36), nullable=False),
        sa.Column("story_id", sa.CHAR(36), nullable=True),
        sa.Column("job_type", sa.Enum("IMAGE", "AUDIO", name="storybatchjobtype", native_enum=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum("SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", name="storybatchjobstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_job_name", sa.String(length=255), nullable=True),
        sa.Column("provider_model", sa.String(length=128), nullable=True),
        sa.Column("provider_state", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("expected_item_count", sa.Integer(), nullable=False),
        sa.Column("completed_item_count", sa.Integer(), nullable=False),
        sa.Column("failed_item_count", sa.Integer(), nullable=False),
        sa.Column("request_keys", sa.JSON(), nullable=True),
        sa.Column("missing_keys", sa.JSON(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_id"], ["custom_story_workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_job_name", name="uq_custom_story_batch_jobs_provider_job_name"),
    )
    _create_index_if_missing("ix_custom_story_batch_jobs_provider_job_name", "custom_story_batch_jobs", ["provider_job_name"])
    _create_index_if_missing("ix_custom_story_batch_jobs_status", "custom_story_batch_jobs", ["status"])
    _create_index_if_missing("ix_custom_story_batch_jobs_story_id", "custom_story_batch_jobs", ["story_id"])
    _create_index_if_missing("ix_custom_story_batch_jobs_workflow_id", "custom_story_batch_jobs", ["workflow_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_custom_story_batch_jobs_workflow_id", "custom_story_batch_jobs")
    _drop_index_if_exists("ix_custom_story_batch_jobs_story_id", "custom_story_batch_jobs")
    _drop_index_if_exists("ix_custom_story_batch_jobs_status", "custom_story_batch_jobs")
    _drop_index_if_exists("ix_custom_story_batch_jobs_provider_job_name", "custom_story_batch_jobs")
    _drop_table_if_exists("custom_story_batch_jobs")
    _drop_index_if_exists("ix_custom_story_workflow_steps_workflow_id", "custom_story_workflow_steps")
    _drop_index_if_exists("ix_custom_story_workflow_steps_step_name", "custom_story_workflow_steps")
    _drop_index_if_exists("ix_custom_story_workflow_steps_status", "custom_story_workflow_steps")
    _drop_table_if_exists("custom_story_workflow_steps")
    _drop_index_if_exists("ix_custom_story_workflows_user_id", "custom_story_workflows")
    _drop_index_if_exists("ix_custom_story_workflows_user_created_at", "custom_story_workflows")
    _drop_index_if_exists("ix_custom_story_workflows_story_id", "custom_story_workflows")
    _drop_index_if_exists("ix_custom_story_workflows_status", "custom_story_workflows")
    _drop_index_if_exists("ix_custom_story_workflows_created_at", "custom_story_workflows")
    _drop_index_if_exists("ix_custom_story_workflows_child_id", "custom_story_workflows")
    _drop_table_if_exists("custom_story_workflows")


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
