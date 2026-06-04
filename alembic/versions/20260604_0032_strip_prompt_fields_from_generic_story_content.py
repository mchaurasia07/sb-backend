"""strip prompt fields from generic story content json

Revision ID: 20260604_0032
Revises: 20260604_0031
Create Date: 2026-06-04
"""

from collections.abc import Sequence
import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260604_0032"
down_revision: str | None = "20260604_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TOP_LEVEL_FIELDS = {
    "cover_image_prompt",
    "cover_planned_image_prompt",
}
PAGE_FIELDS = {
    "image_prompt",
    "planned_image_prompt",
    "tts_prompt",
}


def _clean_story_json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None

    cleaned = dict(value)
    for field in TOP_LEVEL_FIELDS:
        cleaned.pop(field, None)

    pages = cleaned.get("pages")
    if isinstance(pages, list):
        cleaned_pages = []
        for page in pages:
            if not isinstance(page, dict):
                cleaned_pages.append(page)
                continue
            cleaned_page = dict(page)
            for field in PAGE_FIELDS:
                cleaned_page.pop(field, None)
            cleaned_pages.append(cleaned_page)
        cleaned["pages"] = cleaned_pages

    return cleaned


def upgrade() -> None:
    bind = op.get_bind()
    generic_story_contents = sa.table(
        "generic_story_contents",
        sa.column("id"),
        sa.column("story_json", sa.JSON()),
    )

    rows = bind.execute(sa.select(generic_story_contents.c.id, generic_story_contents.c.story_json)).fetchall()
    for row in rows:
        cleaned = _clean_story_json(row.story_json)
        if cleaned is None or cleaned == row.story_json:
            continue
        bind.execute(
            generic_story_contents.update()
            .where(generic_story_contents.c.id == row.id)
            .values(story_json=cleaned)
        )


def downgrade() -> None:
    # Prompt/debug fields are intentionally not restored.
    pass
