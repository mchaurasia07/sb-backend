"""migrate generic stories into stories

Revision ID: 20260628_0067
Revises: 20260628_0066
Create Date: 2026-06-28
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "20260628_0067"
down_revision: str | None = "20260628_0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not _table_exists("stories"):
        return

    _add_column_if_missing(
        "stories",
        sa.Column("story_type", sa.String(length=32), nullable=False, server_default="CUSTOM"),
    )
    op.execute("UPDATE stories SET story_type = 'CUSTOM' WHERE story_type IS NULL OR story_type = ''")
    _create_index_if_missing("ix_stories_story_type", "stories", ["story_type"])

    _alter_story_owner_nullable(nullable=True)
    _migrate_generic_stories()
    _migrate_generic_story_contents()


def downgrade() -> None:
    if not _table_exists("stories") or not _column_exists("stories", "story_type"):
        return

    _delete_migrated_generic_story_contents()
    op.execute("DELETE FROM stories WHERE story_type = 'GENERIC'")
    _alter_story_owner_nullable(nullable=False)
    _drop_index_if_exists("ix_stories_story_type", "stories")
    op.drop_column("stories", "story_type")


def _migrate_generic_stories() -> None:
    if not _table_exists("generic_stories"):
        return

    bind = op.get_bind()
    stories = _stories_table()
    generic_stories = _generic_stories_table()

    existing_story_ids = {
        _uuid_string(story_id)
        for story_id in bind.execute(sa.select(stories.c.id)).scalars().all()
    }
    generic_rows = bind.execute(sa.select(generic_stories)).mappings().all()

    rows_to_insert = []
    for row in generic_rows:
        story_id = _uuid_string(row["id"])
        if story_id in existing_story_ids:
            continue
        rows_to_insert.append(
            {
                "id": story_id,
                "user_id": None,
                "child_id": None,
                "story_type": "GENERIC",
                "title": row["title"],
                "moral": row["moral"],
                "summary": row["summary"],
                "generation_mode": "INPUT_DRIVEN",
                "age_group": row["age_group"],
                "category": row["theme"] or row["genre"],
                "learning_goal": row["learning_goal"],
                "context": None,
                "event_description": None,
                "status": "COMPLETED",
                "current_step": None,
                "error_message": None,
                "story_plan_json": None,
                "story_plan_validated": False,
                "image_plan_json": None,
                "image_plan_validated": False,
                "input_request": {
                    "source": "generic_stories_migration",
                    "generic_story_id": story_id,
                    "theme": row["theme"],
                    "genre": row["genre"],
                    "reading_time_minutes": row["reading_time_minutes"],
                    "character_type": row["character_type"],
                    "total_pages": row["total_pages"],
                    "cover_image": row["cover_image"],
                    "publish_status": row["status"],
                },
                "video_created": False,
                "video_metadata": None,
                "ai_provider": None,
                "text_model": None,
                "image_model": None,
                "reference_image_model": None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    if rows_to_insert:
        bind.execute(stories.insert(), rows_to_insert)


def _migrate_generic_story_contents() -> None:
    if not _table_exists("generic_story_contents") or not _table_exists("story_contents"):
        return

    bind = op.get_bind()
    stories = _stories_identity_table()
    story_contents = _story_contents_table()
    generic_story_contents = _generic_story_contents_table()

    generic_story_ids = {
        _uuid_string(story_id)
        for story_id in bind.execute(
            sa.select(stories.c.id).where(stories.c.story_type == "GENERIC")
        ).scalars().all()
    }
    existing_pairs = {
        (_uuid_string(row.story_id), str(row.language))
        for row in bind.execute(sa.select(story_contents.c.story_id, story_contents.c.language)).all()
    }
    generic_content_rows = bind.execute(sa.select(generic_story_contents)).mappings().all()

    rows_to_insert = []
    for row in generic_content_rows:
        story_id = _uuid_string(row["generic_story_id"])
        language = str(row["language"])
        if story_id not in generic_story_ids or (story_id, language) in existing_pairs:
            continue
        rows_to_insert.append(
            {
                "id": str(uuid4()),
                "story_id": story_id,
                "language": language,
                "story_json": row["story_json"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    if rows_to_insert:
        bind.execute(story_contents.insert(), rows_to_insert)


def _delete_migrated_generic_story_contents() -> None:
    if not _table_exists("story_contents"):
        return
    op.execute(
        """
        DELETE FROM story_contents
        WHERE story_id IN (
            SELECT id
            FROM stories
            WHERE story_type = 'GENERIC'
        )
        """
    )


def _alter_story_owner_nullable(*, nullable: bool) -> None:
    if _column_exists("stories", "user_id"):
        op.alter_column("stories", "user_id", existing_type=sa.CHAR(length=36), nullable=nullable)
    if _column_exists("stories", "child_id"):
        op.alter_column("stories", "child_id", existing_type=sa.CHAR(length=36), nullable=nullable)


def _stories_table() -> sa.Table:
    return sa.table(
        "stories",
        sa.column("id", sa.String(length=36)),
        sa.column("user_id", sa.String(length=36)),
        sa.column("child_id", sa.String(length=36)),
        sa.column("story_type", sa.String(length=32)),
        sa.column("title", sa.String(length=255)),
        sa.column("moral", sa.String(length=255)),
        sa.column("summary", sa.Text()),
        sa.column("generation_mode", sa.String(length=50)),
        sa.column("age_group", sa.String(length=32)),
        sa.column("category", sa.String(length=100)),
        sa.column("learning_goal", sa.String(length=500)),
        sa.column("context", sa.Text()),
        sa.column("event_description", sa.Text()),
        sa.column("status", sa.String(length=50)),
        sa.column("current_step", sa.String(length=50)),
        sa.column("error_message", sa.Text()),
        sa.column("story_plan_json", sa.JSON()),
        sa.column("story_plan_validated", sa.Boolean()),
        sa.column("image_plan_json", sa.JSON()),
        sa.column("image_plan_validated", sa.Boolean()),
        sa.column("input_request", sa.JSON()),
        sa.column("video_created", sa.Boolean()),
        sa.column("video_metadata", sa.JSON()),
        sa.column("ai_provider", sa.String(length=32)),
        sa.column("text_model", sa.String(length=128)),
        sa.column("image_model", sa.String(length=128)),
        sa.column("reference_image_model", sa.String(length=128)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _stories_identity_table() -> sa.Table:
    return sa.table(
        "stories",
        sa.column("id", sa.String(length=36)),
        sa.column("story_type", sa.String(length=32)),
    )


def _generic_stories_table() -> sa.Table:
    return sa.table(
        "generic_stories",
        sa.column("id", sa.String(length=36)),
        sa.column("title", sa.String(length=255)),
        sa.column("summary", sa.Text()),
        sa.column("age_group", sa.String(length=32)),
        sa.column("theme", sa.String(length=100)),
        sa.column("genre", sa.String(length=100)),
        sa.column("moral", sa.String(length=255)),
        sa.column("learning_goal", sa.String(length=500)),
        sa.column("reading_time_minutes", sa.Integer()),
        sa.column("character_type", sa.String(length=100)),
        sa.column("total_pages", sa.Integer()),
        sa.column("cover_image", sa.String(length=1024)),
        sa.column("status", sa.String(length=32)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _story_contents_table() -> sa.Table:
    return sa.table(
        "story_contents",
        sa.column("id", sa.String(length=36)),
        sa.column("story_id", sa.String(length=36)),
        sa.column("language", sa.String(length=16)),
        sa.column("story_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _generic_story_contents_table() -> sa.Table:
    return sa.table(
        "generic_story_contents",
        sa.column("id", sa.String(length=36)),
        sa.column("generic_story_id", sa.String(length=36)),
        sa.column("language", sa.String(length=16)),
        sa.column("story_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _uuid_string(value) -> str:
    return str(value)


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


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
