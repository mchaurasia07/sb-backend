"""add story metadata columns and drop unused model fields

Revision ID: 20260628_0069
Revises: 20260628_0068
Create Date: 2026-06-28
"""

from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op


revision: str = "20260628_0069"
down_revision: str | None = "20260628_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STORY_COLUMNS_TO_DROP = (
    "text_model",
    "ai_provider",
    "image_model",
    "reference_image_model",
    "input_request",
    "current_step",
    "context",
)


def upgrade() -> None:
    if not _table_exists("stories"):
        return

    _add_column_if_missing(
        "stories",
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "stories",
        sa.Column("cover_image", sa.String(length=1024), nullable=True),
    )
    _backfill_story_metadata_from_english_content()

    for column_name in STORY_COLUMNS_TO_DROP:
        _drop_column_if_exists("stories", column_name)


def downgrade() -> None:
    if not _table_exists("stories"):
        return

    _add_column_if_missing("stories", sa.Column("text_model", sa.String(length=128), nullable=True))
    _add_column_if_missing("stories", sa.Column("ai_provider", sa.String(length=32), nullable=True))
    _add_column_if_missing("stories", sa.Column("image_model", sa.String(length=128), nullable=True))
    _add_column_if_missing("stories", sa.Column("reference_image_model", sa.String(length=128), nullable=True))
    _add_column_if_missing("stories", sa.Column("input_request", sa.JSON(), nullable=True))
    _add_column_if_missing("stories", sa.Column("current_step", sa.String(length=50), nullable=True))
    _add_column_if_missing("stories", sa.Column("context", sa.Text(), nullable=True))

    _drop_column_if_exists("stories", "cover_image")
    _drop_column_if_exists("stories", "total_pages")


def _backfill_story_metadata_from_english_content() -> None:
    if not _table_exists("story_contents"):
        return
    if not _column_exists("story_contents", "story_json") or not _column_exists("story_contents", "language"):
        return

    bind = op.get_bind()
    story_contents = sa.table(
        "story_contents",
        sa.column("story_id", sa.String(length=36)),
        sa.column("language", sa.String(length=16)),
        sa.column("story_json", sa.JSON()),
    )
    stories = sa.table(
        "stories",
        sa.column("id", sa.String(length=36)),
        sa.column("total_pages", sa.Integer()),
        sa.column("cover_image", sa.String(length=1024)),
    )

    rows = bind.execute(
        sa.select(story_contents.c.story_id, story_contents.c.story_json).where(
            sa.func.lower(story_contents.c.language) == "en"
        )
    ).all()

    for story_id, story_json in rows:
        payload = _json_dict(story_json)
        if not payload:
            continue
        bind.execute(
            stories.update()
            .where(stories.c.id == story_id)
            .values(
                total_pages=_story_total_pages(payload),
                cover_image=_truncate(_story_cover_image(payload), 1024),
            )
        )


def _story_total_pages(story_json: dict) -> int:
    pages = story_json.get("pages") if isinstance(story_json, dict) else None
    return len(pages) if isinstance(pages, list) else 0


def _story_cover_image(story_json: dict) -> str | None:
    if not isinstance(story_json, dict):
        return None
    cover = story_json.get("cover") if isinstance(story_json.get("cover"), dict) else {}
    value = (
        story_json.get("cover_image_url")
        or story_json.get("coverImageUrl")
        or story_json.get("cover_image")
        or story_json.get("coverImage")
        or story_json.get("image_url")
        or story_json.get("imageUrl")
        or cover.get("image_url")
        or cover.get("imageUrl")
    )
    return str(value) if value else None


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return value[:max_length]


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
