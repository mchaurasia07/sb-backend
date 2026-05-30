from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import selectinload

from app.entity.story import Story, StoryContent, StoryGenerationMode, AgeGroup, StoryStatus


class StoryRepository:
    """Persistence operations for stories."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        child_id: UUID,
        generation_mode: str,
        age_group: str,
        **kwargs,
    ) -> Story:
        """Create a new story record."""
        # Remove status from kwargs if present (always use PENDING for new stories)
        kwargs.pop("status", None)

        story = Story(
            user_id=user_id,
            child_id=child_id,
            generation_mode=StoryGenerationMode(generation_mode),
            age_group=AgeGroup(age_group),
            status=StoryStatus.PENDING,
            **kwargs,
        )
        self.session.add(story)
        await self.session.flush()
        return story

    async def get_for_user(self, user_id: UUID, story_id: UUID) -> Story | None:
        """Retrieve story for a specific user with pages and steps."""
        result = await self.session.execute(
            select(Story)
            .options(selectinload(Story.pages), selectinload(Story.contents))
            .where(Story.id == story_id, Story.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, story_id: UUID) -> Story | None:
        """Retrieve story by ID (no user check, used in background tasks)."""
        result = await self.session.execute(
            select(Story)
            .options(selectinload(Story.pages), selectinload(Story.contents))
            .where(Story.id == story_id)
        )
        return result.scalar_one_or_none()

    async def get_status_for_user(self, user_id: UUID, story_id: UUID):
        """Retrieve only story status fields for a specific user."""
        result = await self.session.execute(
            select(
                Story.id,
                Story.status,
                Story.current_step,
                Story.error_message,
                Story.updated_at,
            ).where(Story.id == story_id, Story.user_id == user_id)
        )
        return result.one_or_none()

    async def get_content_by_story_and_language(
        self,
        *,
        story_id: UUID,
        language: str,
    ) -> StoryContent | None:
        result = await self.session.execute(
            select(StoryContent).where(
                StoryContent.story_id == story_id,
                StoryContent.language == language,
            )
        )
        return result.scalar_one_or_none()

    async def update_content(self, content: StoryContent) -> StoryContent:
        flag_modified(content, "story_json")
        await self.session.flush()
        return content

    async def upsert_content(
        self,
        story: Story,
        *,
        language: str,
        story_json: dict,
    ) -> StoryContent:
        content = await self.get_content_by_story_and_language(
            story_id=story.id,
            language=language,
        )
        if content is None:
            content = StoryContent(
                story_id=story.id,
                language=language,
                story_json=story_json,
            )
            self.session.add(content)
        else:
            content.story_json = story_json
            flag_modified(content, "story_json")
        await self.session.flush()
        return content

    async def get_available_languages_by_story_ids(
        self,
        story_ids: list[UUID],
    ) -> dict[UUID, list[str]]:
        if not story_ids:
            return {}

        result = await self.session.execute(
            select(StoryContent.story_id, StoryContent.language)
            .where(StoryContent.story_id.in_(story_ids))
            .order_by(StoryContent.language)
        )
        languages_by_story_id: dict[UUID, list[str]] = {}
        for story_id, language in result.all():
            languages_by_story_id.setdefault(story_id, []).append(str(language))
        return languages_by_story_id

    async def list_by_user(self, user_id: UUID, child_id: UUID | None = None) -> list[Story]:
        """List stories for user, optionally filtered by child."""
        query = select(Story).options(selectinload(Story.pages), selectinload(Story.contents)).where(Story.user_id == user_id)
        if child_id:
            query = query.where(Story.child_id == child_id)
        query = query.order_by(Story.created_at.desc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_user_paginated(
        self,
        user_id: UUID,
        child_id: UUID | None = None,
        *,
        page: int,
        page_size: int,
        status_filter: str | None = None,
    ) -> tuple[list[Story], int]:
        """List stories for user with pagination metadata."""
        filters = [Story.user_id == user_id]
        if child_id:
            filters.append(Story.child_id == child_id)
        if status_filter:
            filters.append(Story.status == status_filter)

        total = await self.session.scalar(select(func.count()).select_from(Story).where(*filters))
        query = (
            select(Story)
            .options(selectinload(Story.pages), selectinload(Story.contents))
            .where(*filters)
            .order_by(Story.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), int(total or 0)

    async def update(self, story: Story) -> Story:
        """Update an existing story."""
        await self.session.flush()
        return story
