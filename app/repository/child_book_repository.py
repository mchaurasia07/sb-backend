from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.child_book import ChildBook


class ChildBookRepository:
    """Persistence operations for child library books."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> ChildBook:
        child_book = ChildBook(**data)
        self.session.add(child_book)
        await self.session.flush()
        return child_book

    async def get_for_child(self, child_id: UUID, child_book_id: UUID) -> ChildBook | None:
        result = await self.session.execute(
            select(ChildBook).where(ChildBook.id == child_book_id, ChildBook.child_id == child_id)
        )
        return result.scalar_one_or_none()

    async def get_by_child_story(
        self,
        *,
        child_id: UUID,
        story_id: UUID,
        story_type: str,
    ) -> ChildBook | None:
        result = await self.session.execute(
            select(ChildBook).where(
                ChildBook.child_id == child_id,
                ChildBook.story_id == story_id,
                ChildBook.story_type == story_type,
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

    async def delete(self, child_book: ChildBook) -> None:
        await self.session.delete(child_book)
        await self.session.flush()

    async def delete_by_story(self, *, story_id: UUID, story_type: str) -> None:
        await self.session.execute(
            delete(ChildBook).where(
                ChildBook.story_id == story_id,
                ChildBook.story_type == story_type,
            )
        )
        await self.session.flush()
