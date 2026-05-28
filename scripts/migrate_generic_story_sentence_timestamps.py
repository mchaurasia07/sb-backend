"""Convert story narration timestamps from words to sentences.

Usage:
    python scripts/migrate_generic_story_sentence_timestamps.py

Edit the hard-coded values below before running.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.database import AsyncSessionLocal
from app.entity.generic_story import GenericStoryContent
from app.entity.story import StoryContent
from app.utils.word_timestamps import generate_word_timestamps


APPLY_CHANGES = True
PRINT_JSON = True
FORCE_REGENERATE = False
TARGET_TABLE = "story_contents"  # "story_contents" or "generic_story_contents"
LANGUAGE: str | None = None
STORY_ID_LIST = [
    "0c2e94eaa9534cfa920a3cbad07ac9a9",
    "929015fb867a493085d6c8cf3b4d1a3b",
    "a47f54183e36464b9db189e13add09c7",
    "c63aa5d17a5c4fec9f44716b69de00cc",
    "f3176f1a5de14f6a9282ffd49fce825c",
    "f70555339ce9440285b3ed215d3c5232",
    "3fa0ac3befaa4fddbbb202615a289aba",
]


def _is_sentence_timing(page: dict) -> bool:
    timestamps = page.get("word_timestamps")
    text = page.get("text", "")
    if not isinstance(timestamps, list) or not timestamps:
        return False
    if not isinstance(text, str) or not text.strip():
        return True

    word_count = len(text.split())
    timestamp_count = len(timestamps)
    return timestamp_count < max(2, word_count // 2)


def _convert_story_json(story_json: dict, *, force: bool) -> tuple[dict, int, int]:
    updated_story_json = deepcopy(story_json)
    pages = updated_story_json.get("pages")
    if not isinstance(pages, list):
        return updated_story_json, 0, 0

    changed_pages = 0
    skipped_pages = 0
    for page in pages:
        if not isinstance(page, dict):
            skipped_pages += 1
            continue

        text = page.get("text")
        duration = page.get("duration")
        if not isinstance(text, str) or not text.strip() or not isinstance(duration, (int, float)) or duration <= 0:
            skipped_pages += 1
            continue

        if not force and _is_sentence_timing(page):
            skipped_pages += 1
            continue

        page["word_timestamps"] = generate_word_timestamps(text, float(duration))
        changed_pages += 1

    return updated_story_json, changed_pages, skipped_pages


async def migrate(
    *,
    apply: bool,
    target_table: str,
    language: str | None,
    story_ids: list[UUID],
    force: bool,
    print_json: bool,
) -> None:
    async with AsyncSessionLocal() as session:
        if target_table == "story_contents":
            content_model = StoryContent
            story_id_column = StoryContent.story_id
            story_label = "story"
        elif target_table == "generic_story_contents":
            content_model = GenericStoryContent
            story_id_column = GenericStoryContent.generic_story_id
            story_label = "generic_story"
        else:
            raise ValueError("TARGET_TABLE must be 'story_contents' or 'generic_story_contents'")

        query = select(content_model)
        if language:
            query = query.where(content_model.language == language)
        if story_ids:
            query = query.where(story_id_column.in_(story_ids))

        result = await session.execute(query.order_by(content_model.created_at))
        contents = list(result.scalars().all())

        total_rows_changed = 0
        total_pages_changed = 0
        total_pages_skipped = 0

        for content in contents:
            content_story_id = getattr(content, "story_id", None) or getattr(content, "generic_story_id", None)
            if not isinstance(content.story_json, dict):
                print(
                    f"SKIP table={target_table} row={content.id} {story_label}={content_story_id} "
                    f"language={content.language}: invalid story_json"
                )
                continue

            updated_json, changed_pages, skipped_pages = _convert_story_json(content.story_json, force=force)
            total_pages_changed += changed_pages
            total_pages_skipped += skipped_pages

            if changed_pages == 0:
                print(
                    f"UNCHANGED table={target_table} row={content.id} {story_label}={content_story_id} "
                    f"language={content.language} skipped_pages={skipped_pages}"
                )
                continue

            total_rows_changed += 1
            print(
                f"{'UPDATE' if apply else 'DRY-RUN'} table={target_table} row={content.id} "
                f"{story_label}={content_story_id} "
                f"language={content.language} changed_pages={changed_pages} skipped_pages={skipped_pages}"
            )
            if print_json:
                print("--- converted story_json begin ---")
                print(json.dumps(updated_json, ensure_ascii=False, indent=2))
                print("--- converted story_json end ---")

            if apply:
                content.story_json = updated_json
                flag_modified(content, "story_json")

        if apply:
            await session.commit()
            action = "Updated"
        else:
            await session.rollback()
            action = "Would update"

        print(
            f"\n{action} {total_rows_changed}/{len(contents)} content rows; "
            f"changed_pages={total_pages_changed}; skipped_pages={total_pages_skipped}."
        )


def main() -> None:
    story_ids = [UUID(story_id) for story_id in STORY_ID_LIST]
    asyncio.run(
        migrate(
            apply=APPLY_CHANGES,
            target_table=TARGET_TABLE,
            language=LANGUAGE.strip().lower() if LANGUAGE else None,
            story_ids=story_ids,
            force=FORCE_REGENERATE,
            print_json=PRINT_JSON,
        )
    )


if __name__ == "__main__":
    main()
