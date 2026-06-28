from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.entity.support import (
    SupportMessage,
    SupportMessageSender,
    SupportQuery,
    SupportQueryStatus,
)
from app.repository.base_repository import BaseRepository


class SupportRepository(BaseRepository[SupportQuery]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SupportQuery)
        self.messages = BaseRepository(session, SupportMessage)

    async def create_query(
        self,
        *,
        user_id: UUID,
        subject: str,
        pending_at_user: bool,
        pending_at_jugni: bool,
    ) -> SupportQuery:
        return await self.create(
            user_id=user_id,
            subject=subject,
            pending_at_user=pending_at_user,
            pending_at_jugni=pending_at_jugni,
        )

    async def add_message(
        self,
        *,
        query: SupportQuery,
        sender: SupportMessageSender,
        message: str,
    ) -> SupportMessage:
        return await self.messages.create(query=query, sender=sender, message=message)

    async def list_for_user(
        self, *, user_id: UUID, page: int, size: int
    ) -> tuple[list[SupportQuery], int]:
        return await self.list_paginated(
            filters=(SupportQuery.user_id == user_id,),
            page=page,
            page_size=size,
            order_by=(SupportQuery.updated_at.desc(), SupportQuery.created_at.desc()),
        )

    async def get_for_user(
        self, *, user_id: UUID, query_id: str, include_messages: bool = False
    ) -> SupportQuery | None:
        filters = (
            SupportQuery.user_id == user_id,
            SupportQuery.query_id == query_id,
        )
        if not include_messages:
            return await self.get_one(filters=filters)

        statement = select(SupportQuery).where(
            *filters,
        )
        statement = statement.options(selectinload(SupportQuery.messages))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_query_id(self, query_id: str) -> SupportQuery | None:
        return await self.get_one(filters=(SupportQuery.query_id == query_id,))

    async def list_for_jugni(
        self,
        *,
        page: int,
        size: int,
        pending_at_jugni: bool | None,
        pending_at_user: bool | None,
        statuses: Sequence[SupportQueryStatus],
    ) -> tuple[list[SupportQuery], int]:
        filters = []
        if pending_at_jugni is not None:
            filters.append(SupportQuery.pending_at_jugni == pending_at_jugni)
        if pending_at_user is not None:
            filters.append(SupportQuery.pending_at_user == pending_at_user)
        if statuses:
            filters.append(SupportQuery.status.in_(statuses))
        return await self.list_paginated(
            filters=tuple(filters),
            page=page,
            page_size=size,
            order_by=(SupportQuery.created_at.desc(), SupportQuery.id.desc()),
            loader_options=(selectinload(SupportQuery.messages),),
        )
