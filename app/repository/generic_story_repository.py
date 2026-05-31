from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import selectinload

from app.entity.generic_story import GenericStory, GenericStoryContent


class GenericStoryRepository:
    """Persistence operations for generic stories."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> GenericStory:
        generic_story = GenericStory(**data)
        self.session.add(generic_story)
        await self.session.flush()
        return generic_story

    async def get_by_title(self, title: str) -> GenericStory | None:
        result = await self.session.execute(
            select(GenericStory)
            .options(selectinload(GenericStory.contents))
            .where(GenericStory.title == title)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, generic_story_id: UUID) -> GenericStory | None:
        result = await self.session.execute(
            select(GenericStory)
            .options(selectinload(GenericStory.contents))
            .where(GenericStory.id == generic_story_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_content_by_story_and_language(
        self,
        *,
        generic_story_id: UUID,
        language: str,
    ) -> GenericStoryContent | None:
        result = await self.session.execute(
            select(GenericStoryContent).where(
                GenericStoryContent.generic_story_id == generic_story_id,
                GenericStoryContent.language == language,
            )
        )
        return result.scalar_one_or_none()

    async def update_content(self, content: GenericStoryContent) -> GenericStoryContent:
        flag_modified(content, "story_json")
        await self.session.flush()
        return content

    async def get_available_languages_by_story_ids(
        self,
        generic_story_ids: list[UUID],
    ) -> dict[UUID, list[str]]:
        if not generic_story_ids:
            return {}

        result = await self.session.execute(
            select(GenericStoryContent.generic_story_id, GenericStoryContent.language)
            .where(GenericStoryContent.generic_story_id.in_(generic_story_ids))
            .order_by(GenericStoryContent.language)
        )
        languages_by_story_id: dict[UUID, list[str]] = {}
        for generic_story_id, language in result.all():
            languages_by_story_id.setdefault(generic_story_id, []).append(str(language))
        return languages_by_story_id

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[GenericStory], int]:
        query: Select[tuple[GenericStory]] = select(GenericStory).options(selectinload(GenericStory.contents))
        count_query = select(func.count()).select_from(GenericStory)

        if status:
            query = query.where(GenericStory.status == status)
            count_query = count_query.where(GenericStory.status == status)

        total = await self.session.scalar(count_query)
        result = await self.session.execute(
            query.order_by(GenericStory.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def upsert_contents(
        self,
        generic_story: GenericStory,
        contents: list[dict],
    ) -> None:
        result = await self.session.execute(
            select(GenericStoryContent).where(GenericStoryContent.generic_story_id == generic_story.id)
        )
        existing_by_language = {str(content.language): content for content in result.scalars().all()}
        for data in contents:
            language = str(data["language"])
            existing = existing_by_language.get(language)
            if existing is None:
                self.session.add(
                    GenericStoryContent(
                        generic_story_id=generic_story.id,
                        language=language,
                        story_json=data["story_json"],
                    )
                )
            else:
                existing.story_json = data["story_json"]
        await self.session.flush()

    async def delete(self, generic_story: GenericStory) -> None:
        await self.session.delete(generic_story)
        await self.session.flush()
