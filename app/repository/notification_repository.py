from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.entity.notification import (
    Notification,
    NotificationAccountType,
    NotificationAudience,
    PushDeviceToken,
)


class PushDeviceTokenRepository:
    """Persistence operations for registered Expo push tokens."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(
        self,
        *,
        user_id: UUID,
        child_id: UUID | None,
        account_type: NotificationAccountType,
        expo_push_token: str,
        device_id: str | None,
        platform: str | None,
        app_version: str | None,
    ) -> PushDeviceToken:
        existing = await self.get_by_token(expo_push_token)
        if existing is None:
            existing = PushDeviceToken(
                user_id=user_id,
                child_id=child_id,
                account_type=account_type,
                expo_push_token=expo_push_token,
            )
            self.session.add(existing)

        existing.user_id = user_id
        existing.child_id = child_id
        existing.account_type = account_type
        existing.device_id = device_id
        existing.platform = platform
        existing.app_version = app_version
        existing.active = True
        existing.last_error = None
        await self.session.flush()
        return existing

    async def get_by_token(self, expo_push_token: str) -> PushDeviceToken | None:
        result = await self.session.execute(
            select(PushDeviceToken).where(PushDeviceToken.expo_push_token == expo_push_token)
        )
        return result.scalar_one_or_none()

    async def deactivate_token(self, expo_push_token: str, error: str | None = None) -> None:
        token = await self.get_by_token(expo_push_token)
        if token is None:
            return
        token.active = False
        token.last_error = error
        await self.session.flush()

    async def active_for_parent_user(self, user_id: UUID) -> list[PushDeviceToken]:
        result = await self.session.execute(
            select(PushDeviceToken).where(
                PushDeviceToken.user_id == user_id,
                PushDeviceToken.account_type == NotificationAccountType.PARENT,
                PushDeviceToken.active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def active_for_parent_users(self, user_ids: list[UUID]) -> list[PushDeviceToken]:
        if not user_ids:
            return []
        result = await self.session.execute(
            select(PushDeviceToken).where(
                PushDeviceToken.user_id.in_(user_ids),
                PushDeviceToken.account_type == NotificationAccountType.PARENT,
                PushDeviceToken.active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def active_for_child(self, child_id: UUID) -> list[PushDeviceToken]:
        result = await self.session.execute(
            select(PushDeviceToken).where(
                PushDeviceToken.child_id == child_id,
                PushDeviceToken.account_type == NotificationAccountType.CHILD,
                PushDeviceToken.active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def active_for_children(self, child_ids: list[UUID]) -> list[PushDeviceToken]:
        if not child_ids:
            return []
        result = await self.session.execute(
            select(PushDeviceToken).where(
                PushDeviceToken.child_id.in_(child_ids),
                PushDeviceToken.account_type == NotificationAccountType.CHILD,
                PushDeviceToken.active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def active_for_audience(self, audience: NotificationAudience) -> list[PushDeviceToken]:
        query = select(PushDeviceToken).where(PushDeviceToken.active.is_(True))
        if audience == NotificationAudience.PARENTS:
            query = query.where(PushDeviceToken.account_type == NotificationAccountType.PARENT)
        elif audience == NotificationAudience.CHILDREN:
            query = query.where(PushDeviceToken.account_type == NotificationAccountType.CHILD)
        result = await self.session.execute(query)
        return list(result.scalars().all())


class NotificationRepository:
    """Persistence operations for notification audit logs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, notification_id: UUID) -> Notification | None:
        result = await self.session.execute(select(Notification).where(Notification.id == notification_id))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        event_type: str,
        audience: NotificationAudience,
        title: str,
        body: str,
        data: dict | None,
        user_id: UUID | None = None,
        child_id: UUID | None = None,
    ) -> Notification:
        notification = Notification(
            event_type=event_type,
            audience=audience,
            title=title,
            body=body,
            data=data,
            user_id=user_id,
            child_id=child_id,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def update(self, notification: Notification) -> Notification:
        if notification.data is not None:
            flag_modified(notification, "data")
        if notification.tickets is not None:
            flag_modified(notification, "tickets")
        await self.session.flush()
        return notification
