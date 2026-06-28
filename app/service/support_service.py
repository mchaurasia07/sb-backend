from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException
from app.entity.support import SupportMessageSender, SupportQueryStatus
from app.model.request.support import AddSupportMessageRequest, CreateSupportQueryRequest
from app.model.response.support import (
    JugniSupportQueryListData,
    SupportMessageResponse,
    SupportQueryClosed,
    SupportQueryCreated,
    SupportQueryDetail,
    SupportQueryListData,
    SupportQueryListItem,
)
from app.repository.support_repository import SupportRepository
from app.service.support_reply_email_service import SupportReplyEmailService


class SupportService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.support = SupportRepository(session)
        self.reply_email = SupportReplyEmailService(session)

    @staticmethod
    def _required(value: str | None, message: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError(message)
        return normalized

    async def create_query(
        self, *, user_id: UUID, payload: CreateSupportQueryRequest
    ) -> SupportQueryCreated:
        subject = self._required(payload.subject, "Subject is required.")
        details = self._required(payload.query_details, "Query details are required.")
        query = await self.support.create_query(
            user_id=user_id,
            subject=subject,
            pending_at_user=False,
            pending_at_jugni=True,
        )
        await self.support.add_message(
            query=query, sender=SupportMessageSender.USER, message=details
        )
        await self.session.commit()
        await self.session.refresh(query)
        return SupportQueryCreated.model_validate(query)

    async def list_queries(
        self, *, user_id: UUID, page: int, size: int
    ) -> SupportQueryListData:
        items, total = await self.support.list_for_user(user_id=user_id, page=page, size=size)
        return SupportQueryListData(
            page=page,
            size=size,
            total_records=total,
            total_pages=ceil(total / size) if total else 0,
            items=[
                SupportQueryListItem(
                    query_id=item.query_id,
                    subject=item.subject,
                    status=item.status.value,
                    pending_at_user=item.pending_at_user,
                    pending_at_jugni=item.pending_at_jugni,
                    created_at=item.created_at,
                    last_updated_at=item.updated_at,
                )
                for item in items
            ],
        )

    async def get_query(self, *, user_id: UUID, query_id: str) -> SupportQueryDetail:
        query = await self.support.get_for_user(
            user_id=user_id, query_id=query_id, include_messages=True
        )
        if query is None:
            raise NotFoundException("Support query not found.", code="SUPPORT_QUERY_NOT_FOUND")
        return self._query_detail(query)

    @staticmethod
    def _query_detail(query) -> SupportQueryDetail:
        return SupportQueryDetail(
            query_id=query.query_id,
            subject=query.subject,
            status=query.status.value,
            pending_at_user=query.pending_at_user,
            pending_at_jugni=query.pending_at_jugni,
            created_at=query.created_at,
            messages=[
                SupportMessageResponse.model_validate(message)
                for message in sorted(query.messages, key=lambda item: item.created_at)
            ],
        )

    async def list_jugni_queries(
        self,
        *,
        page: int,
        size: int,
        pending_at_jugni: bool | None,
        pending_at_user: bool | None,
        statuses: list[SupportQueryStatus],
    ) -> JugniSupportQueryListData:
        items, total = await self.support.list_for_jugni(
            page=page,
            size=size,
            pending_at_jugni=pending_at_jugni,
            pending_at_user=pending_at_user,
            statuses=statuses,
        )
        return JugniSupportQueryListData(
            page=page,
            size=size,
            total_records=total,
            total_pages=ceil(total / size) if total else 0,
            items=[self._query_detail(item) for item in items],
        )

    async def add_message(
        self, *, user_id: UUID, query_id: str, payload: AddSupportMessageRequest
    ) -> SupportMessageResponse:
        text = self._required(payload.message, "Message is required.")
        query = await self.support.get_for_user(user_id=user_id, query_id=query_id)
        if query is None:
            raise NotFoundException("Support query not found.", code="SUPPORT_QUERY_NOT_FOUND")
        if query.status == SupportQueryStatus.CLOSED:
            raise ConflictException(
                "This support request has been closed.",
                status_code=409,
                code="SUPPORT_QUERY_CLOSED",
            )
        message = await self.support.add_message(
            query=query, sender=SupportMessageSender.USER, message=text
        )
        query.pending_at_jugni = True
        query.pending_at_user = False
        query.updated_at = datetime.now(UTC)
        await self.support.update(query)
        await self.session.commit()
        return SupportMessageResponse.model_validate(message)

    async def add_jugni_reply(
        self, *, query_id: str, payload: AddSupportMessageRequest
    ) -> SupportMessageResponse:
        text = self._required(payload.message, "Message is required.")
        query = await self.support.get_by_query_id(query_id)
        if query is None:
            raise NotFoundException("Support query not found.", code="SUPPORT_QUERY_NOT_FOUND")
        if query.status == SupportQueryStatus.CLOSED:
            raise ConflictException(
                "This support request has been closed.",
                status_code=409,
                code="SUPPORT_QUERY_CLOSED",
            )
        message = await self.support.add_message(
            query=query, sender=SupportMessageSender.JUGNI, message=text
        )
        query.status = SupportQueryStatus.RESPONDED
        query.pending_at_jugni = False
        query.pending_at_user = True
        query.updated_at = datetime.now(UTC)
        await self.support.update(query)
        await self.session.commit()
        await self.reply_email.send_jugni_reply(query=query, reply_message=text)
        return SupportMessageResponse.model_validate(message)

    async def close_query(self, *, user_id: UUID, query_id: str) -> SupportQueryClosed:
        query = await self.support.get_for_user(user_id=user_id, query_id=query_id)
        if query is None:
            raise NotFoundException("Support query not found.", code="SUPPORT_QUERY_NOT_FOUND")
        if query.status != SupportQueryStatus.CLOSED:
            closed_at = datetime.now(UTC)
            query.status = SupportQueryStatus.CLOSED
            query.closed_at = closed_at
            query.updated_at = closed_at
            await self.support.update(query)
            await self.session.commit()
        return SupportQueryClosed(
            query_id=query.query_id,
            status="CLOSED",
            pending_at_user=query.pending_at_user,
            pending_at_jugni=query.pending_at_jugni,
            closed_at=query.closed_at or query.updated_at,
        )
