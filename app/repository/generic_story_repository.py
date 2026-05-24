from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.generic_story import GenericStory


class GenericStoryRepository:
    """Persistence operations for generic stories."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> GenericStory:
        generic_story = GenericStory(**data)
        self.session.add(generic_story)
        await self.session.flush()
        return generic_story

    async def get_by_id(self, generic_story_id: UUID) -> GenericStory | None:
        result = await self.session.execute(
            select(GenericStory).where(GenericStory.id == generic_story_id)
        )
        return result.scalar_one_or_none()

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[GenericStory], int]:
        query: Select[tuple[GenericStory]] = select(GenericStory)
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

    async def delete(self, generic_story: GenericStory) -> None:
        await self.session.delete(generic_story)
        await self.session.flush()
