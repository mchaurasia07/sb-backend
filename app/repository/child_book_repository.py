from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.entity.child_book import ChildBook
from app.entity.generic_story import GenericStory
from app.entity.story import Story, StoryStatus


class ChildBookRepository:
    """Persistence operations for child library books."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> ChildBook:
        child_book = ChildBook(**data)
        self.session.add(child_book)
        await self.session.flush()
        return child_book

    async def get_for_child(
        self,
        child_id: UUID,
        child_book_id: UUID,
        *,
        for_update: bool = False,
    ) -> ChildBook | None:
        query = select(ChildBook).where(ChildBook.id == child_book_id, ChildBook.child_id == child_id)
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_child_story(
        self,
        *,
        child_id: UUID,
        story_id: UUID,
        story_type: str,
        language: str,
    ) -> ChildBook | None:
        result = await self.session.execute(
            select(ChildBook).where(
                ChildBook.child_id == child_id,
                ChildBook.story_id == story_id,
                ChildBook.story_type == story_type,
                ChildBook.language == language,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_child_paginated(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[ChildBook], int]:
        query: Select[tuple[ChildBook]] = select(ChildBook).where(ChildBook.child_id == child_id)
        count_query = select(func.count()).select_from(ChildBook).where(ChildBook.child_id == child_id)

        if status:
            query = query.where(ChildBook.status == status)
            count_query = count_query.where(ChildBook.status == status)

        total = await self.session.scalar(count_query)
        result = await self.session.execute(
            query.order_by(ChildBook.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def list_generic_library_paginated(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[ChildBook, GenericStory]], int]:
        filters = [
            ChildBook.child_id == child_id,
            ChildBook.story_type == "generic",
            GenericStory.status == "active",
        ]
        count_query = (
            select(func.count())
            .select_from(ChildBook)
            .join(GenericStory, GenericStory.id == ChildBook.story_id)
            .where(*filters)
        )
        query = (
            select(ChildBook, GenericStory)
            .join(GenericStory, GenericStory.id == ChildBook.story_id)
            .where(*filters)
            .order_by(ChildBook.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(count_query)
        result = await self.session.execute(query)
        return list(result.tuples().all()), int(total or 0)

    async def list_custom_library_paginated(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[ChildBook, Story]], int]:
        filters = [
            ChildBook.child_id == child_id,
            ChildBook.story_type == "custom",
            Story.child_id == child_id,
            Story.status == StoryStatus.COMPLETED,
        ]
        count_query = (
            select(func.count())
            .select_from(ChildBook)
            .join(Story, Story.id == ChildBook.story_id)
            .where(*filters)
        )
        query = (
            select(ChildBook, Story)
            .join(Story, Story.id == ChildBook.story_id)
            .options(selectinload(Story.pages), selectinload(Story.contents))
            .where(*filters)
            .order_by(ChildBook.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(count_query)
        result = await self.session.execute(query)
        return list(result.tuples().all()), int(total or 0)

    async def delete(self, child_book: ChildBook) -> None:
        await self.session.delete(child_book)
        await self.session.flush()

    async def update(self, child_book: ChildBook) -> ChildBook:
        await self.session.flush()
        return child_book

    async def delete_by_story(self, *, story_id: UUID, story_type: str) -> None:
        await self.session.execute(
            delete(ChildBook).where(
                ChildBook.story_id == story_id,
                ChildBook.story_type == story_type,
            )
        )
        await self.session.flush()
