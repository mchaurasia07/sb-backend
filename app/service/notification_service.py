import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import AppException
from app.entity.notification import (
    NotificationAccountType,
    NotificationAudience,
    NotificationDeliveryStatus,
    PushDeviceToken,
)
from app.model.request.notification import NotificationSendRequest, PushTokenRegisterRequest
from app.model.response.notification import NotificationSendResponse, PushTokenResponse
from app.repository.notification_repository import NotificationRepository, PushDeviceTokenRepository
from app.service.expo_push_service import expo_push_service

logger = logging.getLogger(__name__)


class NotificationService:
    """Generic notification use cases for individual and bulk Expo push sends."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.tokens = PushDeviceTokenRepository(session)
        self.notifications = NotificationRepository(session)

    async def register_push_token(
        self,
        *,
        auth: AuthContext,
        payload: PushTokenRegisterRequest,
    ) -> PushTokenResponse:
        token_value = payload.expo_push_token.strip()
        if not expo_push_service.is_expo_push_token(token_value):
            raise AppException("Invalid Expo push token", code="INVALID_EXPO_PUSH_TOKEN")

        account_type = NotificationAccountType.CHILD if auth.is_child else NotificationAccountType.PARENT
        token = await self.tokens.upsert(
            user_id=auth.user_id,
            child_id=auth.child_id if auth.is_child else None,
            account_type=account_type,
            expo_push_token=token_value,
            device_id=payload.device_id,
            platform=payload.platform,
            app_version=payload.app_version,
        )
        await self.session.commit()
        return PushTokenResponse.model_validate(token)

    async def unregister_push_token(self, expo_push_token: str) -> None:
        await self.tokens.deactivate_token(expo_push_token.strip(), error="unregistered_by_client")
        await self.session.commit()

    async def send_manual(self, payload: NotificationSendRequest) -> NotificationSendResponse:
        audience = NotificationAudience(payload.audience)
        if audience == NotificationAudience.PARENT_USER:
            if payload.user_id is None:
                raise AppException("user_id is required for parent_user audience", code="USER_ID_REQUIRED")
            return await self.send_to_parent_user(
                user_id=payload.user_id,
                event_type=payload.event_type,
                title=payload.title,
                body=payload.body,
                data=payload.data,
            )
        if audience == NotificationAudience.CHILD:
            if payload.child_id is None:
                raise AppException("child_id is required for child audience", code="CHILD_ID_REQUIRED")
            return await self.send_to_child(
                child_id=payload.child_id,
                event_type=payload.event_type,
                title=payload.title,
                body=payload.body,
                data=payload.data,
            )
        return await self.send_to_audience(
            audience=audience,
            event_type=payload.event_type,
            title=payload.title,
            body=payload.body,
            data=payload.data,
        )

    async def send_to_parent_user(
        self,
        *,
        user_id: UUID,
        event_type: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> NotificationSendResponse:
        tokens = await self.tokens.active_for_parent_user(user_id)
        return await self._send(
            tokens=tokens,
            audience=NotificationAudience.PARENT_USER,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            user_id=user_id,
            child_id=None,
        )

    async def send_to_child(
        self,
        *,
        child_id: UUID,
        event_type: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> NotificationSendResponse:
        tokens = await self.tokens.active_for_child(child_id)
        return await self._send(
            tokens=tokens,
            audience=NotificationAudience.CHILD,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            user_id=None,
            child_id=child_id,
        )

    async def send_to_audience(
        self,
        *,
        audience: NotificationAudience,
        event_type: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> NotificationSendResponse:
        if audience not in (NotificationAudience.ALL, NotificationAudience.PARENTS, NotificationAudience.CHILDREN):
            raise AppException("Invalid bulk notification audience", code="INVALID_NOTIFICATION_AUDIENCE")
        tokens = await self.tokens.active_for_audience(audience)
        return await self._send(
            tokens=tokens,
            audience=audience,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            user_id=None,
            child_id=None,
        )

    async def send_story_completed_to_parent(
        self,
        *,
        user_id: UUID,
        story_id: UUID,
        story_title: str,
    ) -> None:
        await self._send_best_effort(
            self.send_to_parent_user(
                user_id=user_id,
                event_type="custom_story_generated",
                title="Your story is ready",
                body=f"{story_title} is ready to read.",
                data={
                    "event_type": "custom_story_generated",
                    "story_id": str(story_id),
                    "screen": "story_detail",
                },
            )
        )

    async def send_child_story_added(
        self,
        *,
        child_id: UUID,
        child_book_id: UUID,
        story_id: UUID,
        story_title: str,
        story_type: str,
    ) -> None:
        await self._send_best_effort(
            self.send_to_child(
                child_id=child_id,
                event_type="story_added_to_child_library",
                title="New story added",
                body=f"{story_title} is ready in your library.",
                data={
                    "event_type": "story_added_to_child_library",
                    "child_book_id": str(child_book_id),
                    "story_id": str(story_id),
                    "story_type": story_type,
                    "screen": "child_story_detail",
                },
            )
        )

    async def send_child_audio_added(
        self,
        *,
        child_id: UUID,
        child_audio_id: UUID,
        audio_id: UUID,
        audio_name: str,
    ) -> None:
        await self._send_best_effort(
            self.send_to_child(
                child_id=child_id,
                event_type="audio_added_to_child_library",
                title="New audio added",
                body=f"{audio_name} is ready in your audio library.",
                data={
                    "event_type": "audio_added_to_child_library",
                    "child_audio_id": str(child_audio_id),
                    "audio_id": str(audio_id),
                    "screen": "child_audio_player",
                },
            )
        )

    async def _send(
        self,
        *,
        tokens: list[PushDeviceToken],
        audience: NotificationAudience,
        event_type: str,
        title: str,
        body: str,
        data: dict[str, Any] | None,
        user_id: UUID | None,
        child_id: UUID | None,
    ) -> NotificationSendResponse:
        notification = await self.notifications.create(
            event_type=event_type,
            audience=audience,
            title=title,
            body=body,
            data=data or {},
            user_id=user_id,
            child_id=child_id,
        )
        notification.target_count = len(tokens)
        if not tokens:
            notification.status = NotificationDeliveryStatus.SKIPPED
            await self.notifications.update(notification)
            await self.session.commit()
            return self._to_response(notification)

        messages = [
            {
                "to": token.expo_push_token,
                "sound": "default",
                "title": title,
                "body": body,
                "data": data or {},
            }
            for token in tokens
        ]

        try:
            tickets = await expo_push_service.send_messages(messages)
            notification.tickets = tickets
            await self._apply_ticket_results(notification, tokens, tickets)
        except Exception as exc:
            logger.exception("push_notification_send_failed")
            notification.status = NotificationDeliveryStatus.FAILED
            notification.failed_count = len(tokens)
            notification.error_message = str(exc)

        await self.notifications.update(notification)
        await self.session.commit()
        return self._to_response(notification)

    async def _apply_ticket_results(
        self,
        notification,
        tokens: list[PushDeviceToken],
        tickets: list[dict[str, Any]],
    ) -> None:
        sent_count = 0
        failed_count = 0
        for token, ticket in zip(tokens, tickets, strict=False):
            if ticket.get("status") == "ok":
                sent_count += 1
                continue
            failed_count += 1
            error = ((ticket.get("details") or {}).get("error") or ticket.get("message") or "push_error")
            if error == "DeviceNotRegistered":
                await self.tokens.deactivate_token(token.expo_push_token, error=error)
            else:
                token.last_error = str(error)

        missing_ticket_count = max(0, len(tokens) - len(tickets))
        failed_count += missing_ticket_count
        notification.sent_count = sent_count
        notification.failed_count = failed_count
        if sent_count and failed_count:
            notification.status = NotificationDeliveryStatus.PARTIAL
        elif sent_count:
            notification.status = NotificationDeliveryStatus.SENT
        else:
            notification.status = NotificationDeliveryStatus.FAILED

    async def _send_best_effort(self, send_coro) -> None:
        try:
            await send_coro
        except Exception:
            logger.exception("push_notification_best_effort_failed")

    @staticmethod
    def _to_response(notification) -> NotificationSendResponse:
        return NotificationSendResponse(
            notification_id=notification.id,
            status=notification.status.value if hasattr(notification.status, "value") else str(notification.status),
            audience=notification.audience.value if hasattr(notification.audience, "value") else str(notification.audience),
            target_count=notification.target_count,
            sent_count=notification.sent_count,
            failed_count=notification.failed_count,
        )
