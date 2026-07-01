from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.age_groups import AgeGroup, validate_age_group
from app.core.database import AsyncSessionLocal
from app.entity.generic_story import GenericStory
from app.entity.story import Story, StoryContent, StoryStatus, StoryType


async def migrate_story(
    story_id: UUID,
    *,
    commit: bool,
    delete_source: bool = False,
) -> dict:
    async with AsyncSessionLocal() as session:
        source = await session.scalar(
            select(GenericStory)
            .options(selectinload(GenericStory.contents))
            .where(GenericStory.id == story_id)
        )
        if source is None:
            raise RuntimeError(f"Generic story {story_id} was not found")

        target = await session.get(Story, story_id)
        action = "updated"
        if target is None:
            action = "created"
            target = Story(id=story_id)
            session.add(target)
        elif target.story_type != StoryType.GENERIC:
            raise RuntimeError(
                f"Story ID {story_id} already belongs to a {target.story_type.value} story"
            )

        target.user_id = None
        target.child_id = None
        target.story_type = StoryType.GENERIC
        target.title = source.title
        target.moral = source.moral
        target.summary = source.summary
        target.total_pages = source.total_pages
        target.cover_image = source.cover_image
        target.age_group = AgeGroup(validate_age_group(source.age_group))
        target.category = source.theme or source.genre
        target.learning_goal = source.learning_goal
        target.status = StoryStatus.COMPLETED
        target.error_message = None
        target.video_created = False
        target.video_metadata = None
        await session.flush()

        copied_languages: list[str] = []
        for source_content in source.contents:
            language = str(source_content.language)
            target_content = await session.scalar(
                select(StoryContent).where(
                    StoryContent.story_id == story_id,
                    StoryContent.language == language,
                )
            )
            if target_content is None:
                target_content = StoryContent(
                    story_id=story_id,
                    language=language,
                    story_json=source_content.story_json,
                )
                session.add(target_content)
            else:
                target_content.story_json = source_content.story_json
            copied_languages.append(language)

        await session.flush()
        target_languages = list(
            await session.scalars(
                select(StoryContent.language)
                .where(StoryContent.story_id == story_id)
                .order_by(StoryContent.language)
            )
        )
        report = {
            "story_id": str(story_id),
            "action": action,
            "source_languages": sorted(copied_languages),
            "target_languages": [str(language) for language in target_languages],
            "story_type": target.story_type.value,
            "user_id": target.user_id,
            "child_id": target.child_id,
            "source_deleted": delete_source,
            "committed": commit,
        }
        if delete_source:
            await session.delete(source)
            await session.flush()
        if commit:
            await session.commit()
        else:
            await session.rollback()
        return report


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy one generic story and all language contents into stories."
    )
    parser.add_argument("story_id", type=UUID)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--delete-source", action="store_true")
    args = parser.parse_args()
    report = await migrate_story(
        args.story_id,
        commit=args.commit,
        delete_source=args.delete_source,
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
