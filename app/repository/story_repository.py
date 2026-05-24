from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.entity.story import Story, StoryGenerationMode, AgeGroup, StoryStatus


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
            .options(selectinload(Story.pages))
            .where(Story.id == story_id, Story.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, story_id: UUID) -> Story | None:
        """Retrieve story by ID (no user check, used in background tasks)."""
        result = await self.session.execute(
            select(Story).options(selectinload(Story.pages)).where(Story.id == story_id)
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: UUID, child_id: UUID | None = None) -> list[Story]:
        """List stories for user, optionally filtered by child."""
        query = select(Story).options(selectinload(Story.pages)).where(Story.user_id == user_id)
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
    ) -> tuple[list[Story], int]:
        """List stories for user with pagination metadata."""
        filters = [Story.user_id == user_id]
        if child_id:
            filters.append(Story.child_id == child_id)

        total = await self.session.scalar(select(func.count()).select_from(Story).where(*filters))
        query = (
            select(Story)
            .options(selectinload(Story.pages))
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
