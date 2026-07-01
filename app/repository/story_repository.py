from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import selectinload

from app.core.age_groups import AgeGroup, validate_age_group
from app.entity.story import Story, StoryContent, StoryStatus, StoryType


class StoryRepository:
    """Persistence operations for stories."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID | None,
        child_id: UUID | None,
        age_group: str,
        **kwargs,
    ) -> Story:
        """Create a new story record."""
        # Remove status from kwargs if present (always use PENDING for new stories)
        kwargs.pop("status", None)
        for removed_column in (
            "ai_provider",
            "text_model",
            "image_model",
            "reference_image_model",
            "input_request",
            "current_step",
            "context",
        ):
            kwargs.pop(removed_column, None)

        story = Story(
            user_id=user_id,
            child_id=child_id,
            age_group=AgeGroup(validate_age_group(age_group)),
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

    async def get_for_user_or_generic(self, user_id: UUID, story_id: UUID) -> Story | None:
        """Retrieve an owned custom story or a shared generic story."""
        result = await self.session.execute(
            select(Story).where(
                Story.id == story_id,
                or_(Story.user_id == user_id, Story.story_type == StoryType.GENERIC),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_user_or_generic_for_update(self, user_id: UUID, story_id: UUID) -> Story | None:
        """Retrieve an owned custom story or shared generic story for editing."""
        result = await self.session.execute(
            select(Story)
            .where(
                Story.id == story_id,
                or_(Story.user_id == user_id, Story.story_type == StoryType.GENERIC),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_for_user_for_update(self, user_id: UUID, story_id: UUID) -> Story | None:
        result = await self.session.execute(
            select(Story)
            .where(Story.id == story_id, Story.user_id == user_id)
            .with_for_update()
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

    async def get_by_id_for_update(self, story_id: UUID) -> Story | None:
        result = await self.session.execute(
            select(Story)
            .where(Story.id == story_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_status_for_user(self, user_id: UUID, story_id: UUID):
        """Retrieve only story status fields for a specific user."""
        result = await self.session.execute(
            select(
                Story.id,
                Story.status,
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

    async def list_contents_by_story(self, story_id: UUID) -> list[StoryContent]:
        result = await self.session.execute(
            select(StoryContent).where(StoryContent.story_id == story_id).order_by(StoryContent.language)
        )
        return list(result.scalars().all())

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
        age_group: str | None = None,
        story_type: StoryType | str | None = None,
        include_details: bool = True,
    ) -> tuple[list[Story], int]:
        """List stories for user with pagination metadata."""
        normalized_story_type = StoryType(story_type) if story_type is not None else None
        filters = []
        if normalized_story_type == StoryType.GENERIC:
            filters.append(Story.story_type == StoryType.GENERIC)
        else:
            filters.append(Story.user_id == user_id)
            if normalized_story_type is not None:
                filters.append(Story.story_type == normalized_story_type)
        if child_id:
            filters.append(Story.child_id == child_id)
        if status_filter:
            filters.append(Story.status == status_filter)
        if age_group:
            filters.append(Story.age_group == AgeGroup(validate_age_group(age_group)))

        total = await self.session.scalar(select(func.count()).select_from(Story).where(*filters))
        query = (
            select(Story)
            .where(*filters)
            .order_by(Story.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if include_details:
            query = query.options(selectinload(Story.pages), selectinload(Story.contents))
        result = await self.session.execute(query)
        return list(result.scalars().all()), int(total or 0)

    async def update(self, story: Story) -> Story:
        """Update an existing story."""
        await self.session.flush()
        return story
