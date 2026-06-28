"""drop legacy workflow columns from stories

Revision ID: 20260628_0068
Revises: 20260628_0067
Create Date: 2026-06-28
"""

from collections.abc import Sequence
import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "20260628_0068"
down_revision: str | None = "20260628_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STORY_COLUMNS_TO_DROP = (
    "generation_mode",
    "event_description",
    "story_plan_json",
    "story_plan_validated",
    "image_plan_json",
    "image_plan_validated",
    "visual_bible_json",
)


def upgrade() -> None:
    if not _table_exists("stories"):
        return

    _copy_story_checkpoints_to_steps()
    for column_name in STORY_COLUMNS_TO_DROP:
        _drop_column_if_exists("stories", column_name)


def downgrade() -> None:
    if not _table_exists("stories"):
        return

    _add_column_if_missing(
        "stories",
        sa.Column("generation_mode", sa.String(length=50), nullable=False, server_default="INPUT_DRIVEN"),
    )
    _add_column_if_missing("stories", sa.Column("event_description", sa.Text(), nullable=True))
    _add_column_if_missing("stories", sa.Column("story_plan_json", sa.JSON(), nullable=True))
    _add_column_if_missing(
        "stories",
        sa.Column("story_plan_validated", sa.Boolean(), nullable=False, server_default="0"),
    )
    _add_column_if_missing("stories", sa.Column("image_plan_json", sa.JSON(), nullable=True))
    _add_column_if_missing(
        "stories",
        sa.Column("image_plan_validated", sa.Boolean(), nullable=False, server_default="0"),
    )


def _copy_story_checkpoints_to_steps() -> None:
    if not _table_exists("story_steps"):
        return

    has_story_plan = _column_exists("stories", "story_plan_json")
    has_image_plan = _column_exists("stories", "image_plan_json")
    if not has_story_plan and not has_image_plan:
        return

    bind = op.get_bind()
    stories = _stories_checkpoint_table(has_story_plan=has_story_plan, has_image_plan=has_image_plan)
    story_steps = _story_steps_table()
    rows = bind.execute(sa.select(stories)).mappings().all()

    step_rows = []
    for row in rows:
        story_id = str(row["id"])
        timestamp = row["updated_at"] or row["created_at"]
        story_plan_json = _json_dict(row["story_plan_json"]) if has_story_plan else None
        image_plan_json = _json_dict(row["image_plan_json"]) if has_image_plan else None
        if story_plan_json:
            step_rows.append(
                {
                    "id": str(uuid4()),
                    "story_id": story_id,
                    "step_name": "STORY_PLAN_VALIDATION",
                    "status": "COMPLETED",
                    "prompt": None,
                    "response": {
                        "valid": True,
                        "story_plan": story_plan_json,
                        "_migrated_from_stories": True,
                    },
                    "error_message": None,
                    "retry_count": 0,
                    "started_at": None,
                    "completed_at": timestamp,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
        if image_plan_json:
            step_rows.append(
                {
                    "id": str(uuid4()),
                    "story_id": story_id,
                    "step_name": "IMAGE_PLAN_VALIDATION",
                    "status": "COMPLETED",
                    "prompt": None,
                    "response": {
                        "valid": True,
                        "image_plan": image_plan_json,
                        "_migrated_from_stories": True,
                    },
                    "error_message": None,
                    "retry_count": 0,
                    "started_at": None,
                    "completed_at": timestamp,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )

    if step_rows:
        bind.execute(story_steps.insert(), step_rows)


def _json_dict(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _stories_checkpoint_table(*, has_story_plan: bool, has_image_plan: bool) -> sa.Table:
    columns = [
        sa.column("id", sa.String(length=36)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    ]
    if has_story_plan:
        columns.append(sa.column("story_plan_json", sa.JSON()))
    if has_image_plan:
        columns.append(sa.column("image_plan_json", sa.JSON()))
    return sa.table("stories", *columns)


def _story_steps_table() -> sa.Table:
    return sa.table(
        "story_steps",
        sa.column("id", sa.String(length=36)),
        sa.column("story_id", sa.String(length=36)),
        sa.column("step_name", sa.String(length=50)),
        sa.column("status", sa.String(length=50)),
        sa.column("prompt", sa.Text()),
        sa.column("response", sa.JSON()),
        sa.column("error_message", sa.Text()),
        sa.column("retry_count", sa.Integer()),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)
