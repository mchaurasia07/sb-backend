from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.child_activity import ChildActivity


class ChildActivityRepository:
    """Persistence operations for child activity logs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> ChildActivity:
        activity = ChildActivity(**data)
        self.session.add(activity)
        await self.session.flush()
        return activity

    async def list_for_child_paginated(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
        activity_type: str | None = None,
    ) -> tuple[list[ChildActivity], int]:
        query: Select[tuple[ChildActivity]] = select(ChildActivity).where(ChildActivity.child_id == child_id)
        count_query = select(func.count()).select_from(ChildActivity).where(ChildActivity.child_id == child_id)

        if activity_type:
            query = query.where(ChildActivity.activity_type == activity_type)
            count_query = count_query.where(ChildActivity.activity_type == activity_type)

        total = await self.session.scalar(count_query)
        result = await self.session.execute(
            query.order_by(ChildActivity.occurred_at.desc(), ChildActivity.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)
