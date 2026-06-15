"""add generic story workflow steps

Revision ID: 20260614_0052
Revises: 20260613_0051
Create Date: 2026-06-14
"""

from collections.abc import Sequence
from datetime import UTC, datetime
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20260614_0052"
down_revision: str | None = "20260613_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ORDERED_STEPS = (
    "CHARACTER_EXTRACTION",
    "SCENE_PLAN_GENERATION",
    "VISUAL_BIBLE_GENERATION",
    "STORY_GENERATION",
    "IMAGE_PLAN_GENERATION",
    "IMAGE_GENERATION",
    "NARRATION_GENERATION",
    "PUBLISH_GENERIC_STORY",
)


def upgrade() -> None:
    _create_table_if_missing(
        "generic_story_workflow_steps",
        sa.Column("workflow_id", sa.CHAR(36), nullable=False),
        sa.Column(
            "step_name",
            sa.Enum(
                "CHARACTER_EXTRACTION",
                "SCENE_PLAN_GENERATION",
                "VISUAL_BIBLE_GENERATION",
                "STORY_GENERATION",
                "IMAGE_PLAN_GENERATION",
                "IMAGE_GENERATION",
                "NARRATION_GENERATION",
                "PUBLISH_GENERIC_STORY",
                name="genericstoryworkflowstep",
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
            server_default="PENDING",
        ),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["generic_story_workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _backfill_missing_workflow_steps()
    _create_index_if_missing("ix_generic_story_workflow_steps_workflow_id", "generic_story_workflow_steps", ["workflow_id"])
    _create_index_if_missing("ix_generic_story_workflow_steps_step_name", "generic_story_workflow_steps", ["step_name"])
    _create_index_if_missing("ix_generic_story_workflow_steps_status", "generic_story_workflow_steps", ["status"])
    _create_index_if_missing(
        "ix_generic_story_workflow_steps_workflow_created_at",
        "generic_story_workflow_steps",
        ["workflow_id", "created_at"],
    )
    _create_index_if_missing(
        "ix_generic_story_workflow_steps_workflow_step_created_at",
        "generic_story_workflow_steps",
        ["workflow_id", "step_name", "created_at"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_generic_story_workflow_steps_workflow_step_created_at", "generic_story_workflow_steps")
    _drop_index_if_exists("ix_generic_story_workflow_steps_workflow_created_at", "generic_story_workflow_steps")
    _drop_index_if_exists("ix_generic_story_workflow_steps_status", "generic_story_workflow_steps")
    _drop_index_if_exists("ix_generic_story_workflow_steps_step_name", "generic_story_workflow_steps")
    _drop_index_if_exists("ix_generic_story_workflow_steps_workflow_id", "generic_story_workflow_steps")
    _drop_table_if_exists("generic_story_workflow_steps")


def _create_table_if_missing(table_name: str, *columns, **kwargs) -> None:
    if _table_exists(table_name):
        return
    op.create_table(table_name, *columns, **kwargs)


def _backfill_missing_workflow_steps() -> None:
    if not _table_exists("generic_story_workflows") or not _table_exists("generic_story_workflow_steps"):
        return

    bind = op.get_bind()
    existing = {
        (str(row.workflow_id), str(row.step_name))
        for row in bind.execute(sa.text("SELECT workflow_id, step_name FROM generic_story_workflow_steps"))
    }
    rows = []
    now = datetime.now(UTC)
    for workflow in _workflow_rows():
        workflow_id = str(workflow.get("id") or "")
        if not workflow_id:
            continue
        for step_name in ORDERED_STEPS:
            if (workflow_id, step_name) in existing:
                continue
            status = _backfilled_step_status(workflow, step_name)
            rows.append(
                {
                    "id": str(uuid4()),
                    "workflow_id": workflow_id,
                    "step_name": step_name,
                    "status": status,
                    "input_json": _backfilled_step_input(workflow, step_name),
                    "prompt": None,
                    "output_json": _backfilled_step_output(workflow, step_name) if status == "COMPLETED" else None,
                    "error_message": workflow.get("error_message") if status == "FAILED" else None,
                    "retry_count": 0,
                    "started_at": now if status != "PENDING" else None,
                    "completed_at": now if status in {"COMPLETED", "FAILED"} else None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    if not rows:
        return

    step_table = sa.table(
        "generic_story_workflow_steps",
        sa.column("id", sa.CHAR(36)),
        sa.column("workflow_id", sa.CHAR(36)),
        sa.column("step_name", sa.String(length=64)),
        sa.column("status", sa.String(length=32)),
        sa.column("input_json", sa.JSON()),
        sa.column("prompt", sa.Text()),
        sa.column("output_json", sa.JSON()),
        sa.column("error_message", sa.Text()),
        sa.column("retry_count", sa.Integer()),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind.execute(step_table.insert(), rows)


def _workflow_rows() -> list[dict]:
    available_columns = {
        column.get("name")
        for column in sa.inspect(op.get_bind()).get_columns("generic_story_workflows")
    }
    requested_columns = [
        "id",
        "status",
        "current_step",
        "error_message",
        "generic_story_id",
        "actual_story",
        "age_group",
        "language",
        "requested_pages",
        "title",
        "cover_image",
        "character_analysis_json",
        "scene_plan_json",
        "visual_bible_json",
        "story_json",
        "image_plan_json",
        "input_request",
    ]
    selected_columns = [column for column in requested_columns if column in available_columns]
    if "id" not in selected_columns:
        return []

    statement = sa.text(f"SELECT {', '.join(selected_columns)} FROM generic_story_workflows")
    rows = []
    for row in op.get_bind().execute(statement).mappings():
        data = {column: row.get(column) for column in selected_columns}
        for column in (
            "character_analysis_json",
            "scene_plan_json",
            "visual_bible_json",
            "story_json",
            "image_plan_json",
            "input_request",
        ):
            data[column] = _json_data(data.get(column))
        rows.append(data)
    return rows


def _backfilled_step_status(workflow: dict, step_name: str) -> str:
    workflow_status = str(workflow.get("status") or "")
    current_step = str(workflow.get("current_step") or "")
    if workflow_status == "FAILED" and current_step == step_name:
        return "FAILED"
    if _backfilled_step_is_complete(workflow, step_name):
        return "COMPLETED"
    if workflow_status == "IN_PROGRESS" and current_step == step_name:
        if step_name == "IMAGE_GENERATION":
            return "SUBMITTED_BATCH_JOB"
        return "IN_PROGRESS"
    return "PENDING"


def _backfilled_step_is_complete(workflow: dict, step_name: str) -> bool:
    if step_name == "CHARACTER_EXTRACTION":
        return bool(workflow.get("character_analysis_json"))
    if step_name == "SCENE_PLAN_GENERATION":
        return bool(workflow.get("scene_plan_json"))
    if step_name == "VISUAL_BIBLE_GENERATION":
        return bool(_visual_bible(workflow))
    if step_name == "STORY_GENERATION":
        return bool(workflow.get("story_json"))
    if step_name == "IMAGE_PLAN_GENERATION":
        return bool(workflow.get("image_plan_json"))
    if step_name == "IMAGE_GENERATION":
        return _story_has_images(workflow.get("story_json") or {})
    if step_name == "NARRATION_GENERATION":
        return _story_has_audio(workflow.get("story_json") or {})
    if step_name == "PUBLISH_GENERIC_STORY":
        return bool(workflow.get("generic_story_id")) and workflow.get("status") == "COMPLETED"
    return False


def _backfilled_step_output(workflow: dict, step_name: str) -> dict | None:
    if step_name == "CHARACTER_EXTRACTION":
        return workflow.get("character_analysis_json")
    if step_name == "SCENE_PLAN_GENERATION":
        return workflow.get("scene_plan_json")
    if step_name == "VISUAL_BIBLE_GENERATION":
        return _visual_bible(workflow) or None
    if step_name == "STORY_GENERATION":
        return workflow.get("story_json")
    if step_name == "IMAGE_PLAN_GENERATION":
        return workflow.get("image_plan_json")
    if step_name in {"IMAGE_GENERATION", "NARRATION_GENERATION"}:
        return workflow.get("story_json")
    if step_name == "PUBLISH_GENERIC_STORY":
        return {
            "generic_story_id": str(workflow.get("generic_story_id")) if workflow.get("generic_story_id") else None,
            "title": workflow.get("title"),
            "cover_image": workflow.get("cover_image"),
        }
    return None


def _backfilled_step_input(workflow: dict, step_name: str) -> dict:
    data = {
        "workflow_id": str(workflow.get("id")) if workflow.get("id") else None,
        "generic_story_id": str(workflow.get("generic_story_id")) if workflow.get("generic_story_id") else None,
        "age_group": workflow.get("age_group"),
        "language": workflow.get("language"),
        "requested_pages": workflow.get("requested_pages"),
        "title": workflow.get("title"),
    }
    if step_name == "CHARACTER_EXTRACTION":
        data["actual_story_chars"] = len(str(workflow.get("actual_story") or ""))
    return {key: value for key, value in data.items() if value is not None}


def _visual_bible(workflow: dict) -> dict | None:
    visual_bible = workflow.get("visual_bible_json")
    if isinstance(visual_bible, dict) and visual_bible:
        return visual_bible
    image_plan = workflow.get("image_plan_json")
    if isinstance(image_plan, dict) and isinstance(image_plan.get("visual_bible"), dict):
        return image_plan["visual_bible"]
    return None


def _story_has_images(story_json: dict) -> bool:
    if not isinstance(story_json, dict):
        return False
    if story_json.get("cover_image_url"):
        return True
    return any(isinstance(page, dict) and bool(page.get("image_url")) for page in story_json.get("pages") or [])


def _story_has_audio(story_json: dict) -> bool:
    if not isinstance(story_json, dict):
        return False
    return any(isinstance(page, dict) and bool(page.get("audio_url")) for page in story_json.get("pages") or [])


def _json_data(value):
    if isinstance(value, (dict, list)) or value is None:
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


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
