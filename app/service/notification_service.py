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
    Notification,
    PushDeviceToken,
)
from app.model.request.notification import (
    NotificationAsyncSendRequest,
    NotificationDeliveryOptionsRequest,
    NotificationSendRequest,
    NotificationTargetRequest,
    PushTokenRegisterRequest,
)
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

    async def queue_manual_async(self, payload: NotificationAsyncSendRequest) -> NotificationSendResponse:
        audience = self._audience_for_target(payload.target)
        data = self._build_notification_data(payload.notification)
        data["_target"] = payload.target.model_dump(mode="json")
        data["_delivery"] = self._delivery_with_defaults(payload.delivery, data)

        notification = await self.notifications.create(
            event_type=payload.notification.event_type,
            audience=audience,
            title=payload.notification.title,
            body=payload.notification.body,
            data=data,
            user_id=payload.target.user_id if payload.target.type == "parent_user" else None,
            child_id=payload.target.child_id if payload.target.type == "child" else None,
        )
        notification.status = NotificationDeliveryStatus.PENDING
        notification.target_count = 0
        notification.sent_count = 0
        notification.failed_count = 0
        await self.session.commit()
        return self._to_response(notification)

    async def deliver_queued(self, notification_id: UUID) -> NotificationSendResponse | None:
        notification = await self.notifications.get_by_id(notification_id)
        if notification is None:
            logger.warning("push_notification_queued_not_found: %s", notification_id)
            return None
        if notification.status != NotificationDeliveryStatus.PENDING:
            return self._to_response(notification)

        target = NotificationTargetRequest.model_validate((notification.data or {}).get("_target") or {})
        delivery = (notification.data or {}).get("_delivery") or {}
        tokens = await self._resolve_tokens(target)
        return await self._deliver_notification_to_tokens(
            notification=notification,
            tokens=tokens,
            delivery=self._delivery_with_defaults(delivery, notification.data or {}),
        )

    async def send_to_parent_user(
        self,
        *,
        user_id: UUID,
        event_type: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
        delivery: dict[str, Any] | None = None,
    ) -> NotificationSendResponse:
        tokens = await self.tokens.active_for_parent_user(user_id)
        return await self._send(
            tokens=tokens,
            audience=NotificationAudience.PARENT_USER,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            delivery=delivery,
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
        delivery: dict[str, Any] | None = None,
    ) -> NotificationSendResponse:
        tokens = await self.tokens.active_for_child(child_id)
        return await self._send(
            tokens=tokens,
            audience=NotificationAudience.CHILD,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            delivery=delivery,
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
        delivery: dict[str, Any] | None = None,
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
            delivery=delivery,
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
                title=f"{story_title} is ready",
                body="Tap to read it now.",
                data=self._build_deep_link_data(
                    event_type="custom_story_generated",
                    route="story_detail",
                    fallback_route="parent_dashboard",
                    params={"story_id": str(story_id)},
                ),
                delivery={"channelId": "story-updates", "priority": "high", "sound": "default"},
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
                title="New story in your library",
                body=f"{story_title} is ready to read.",
                data=self._build_deep_link_data(
                    event_type="story_added_to_child_library",
                    route="child_story_detail",
                    fallback_route="child_dashboard",
                    params={
                        "child_book_id": str(child_book_id),
                        "story_id": str(story_id),
                        "story_type": story_type,
                    },
                ),
                delivery={"channelId": "library-updates", "priority": "high", "sound": "default"},
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
                title="New audio is ready",
                body=f"{audio_name} is in your audio library.",
                data=self._build_deep_link_data(
                    event_type="audio_added_to_child_library",
                    route="child_audio_player",
                    fallback_route="child_dashboard",
                    params={
                        "child_audio_id": str(child_audio_id),
                        "audio_id": str(audio_id),
                    },
                ),
                delivery={"channelId": "library-updates", "priority": "high", "sound": "default"},
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
        delivery: dict[str, Any] | None,
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
        return await self._deliver_notification_to_tokens(
            notification=notification,
            tokens=tokens,
            delivery=self._delivery_with_defaults(delivery, data or {}),
        )

    async def _deliver_notification_to_tokens(
        self,
        *,
        notification: Notification,
        tokens: list[PushDeviceToken],
        delivery: dict[str, Any],
    ) -> NotificationSendResponse:
        tokens = self._dedupe_tokens(tokens)
        notification.target_count = len(tokens)
        if not tokens:
            notification.status = NotificationDeliveryStatus.SKIPPED
            await self.notifications.update(notification)
            await self.session.commit()
            return self._to_response(notification)

        public_data = self._public_data(notification.data or {})
        messages = [
            self._build_expo_message(
                token=token,
                title=notification.title,
                body=notification.body,
                data=public_data,
                delivery=delivery,
            )
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

    async def _resolve_tokens(self, target: NotificationTargetRequest) -> list[PushDeviceToken]:
        audience = NotificationAudience(target.type) if target.type != "custom" else NotificationAudience.CUSTOM
        if audience == NotificationAudience.PARENT_USER:
            return await self.tokens.active_for_parent_user(target.user_id)
        if audience == NotificationAudience.CHILD:
            return await self.tokens.active_for_child(target.child_id)
        if audience == NotificationAudience.CUSTOM:
            parent_tokens = await self.tokens.active_for_parent_users(target.user_ids)
            child_tokens = await self.tokens.active_for_children(target.child_ids)
            return self._dedupe_tokens([*parent_tokens, *child_tokens])
        return await self.tokens.active_for_audience(audience)

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
    def _audience_for_target(target: NotificationTargetRequest) -> NotificationAudience:
        if target.type == "custom":
            return NotificationAudience.CUSTOM
        return NotificationAudience(target.type)

    @classmethod
    def _build_notification_data(cls, notification) -> dict[str, Any]:
        payload = notification.model_dump(mode="json")
        return cls._build_deep_link_data(
            event_type=payload["event_type"],
            route=payload.get("route"),
            fallback_route=payload.get("fallback_route"),
            params=payload.get("params") or {},
            data=payload.get("data") or {},
        )

    @staticmethod
    def _build_deep_link_data(
        *,
        event_type: str,
        route: str | None,
        fallback_route: str | None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(data or {})
        merged["event_type"] = event_type
        if route:
            merged["route"] = route
            merged.setdefault("screen", route)
        if fallback_route:
            merged["fallback_route"] = fallback_route
        if params:
            merged["params"] = params
            for key, value in params.items():
                merged.setdefault(key, value)
        merged.setdefault("notification_version", 1)
        return merged

    @classmethod
    def _delivery_with_defaults(
        cls,
        delivery: NotificationDeliveryOptionsRequest | dict[str, Any] | None,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(delivery, NotificationDeliveryOptionsRequest):
            values = delivery.model_dump(mode="json", exclude_none=True)
        else:
            values = dict(delivery or {})
        channel_id = values.pop("channel_id", None) or values.pop("channelId", None) or cls._default_channel_id(data)
        values.setdefault("priority", "high")
        values.setdefault("sound", "default")
        values["channelId"] = channel_id
        return values

    @staticmethod
    def _default_channel_id(data: dict[str, Any]) -> str:
        route = str(data.get("route") or data.get("screen") or "")
        event_type = str(data.get("event_type") or "")
        if "story" in route or "story" in event_type:
            return "story-updates"
        return "library-updates"

    @staticmethod
    def _public_data(data: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in data.items() if not key.startswith("_")}

    @staticmethod
    def _build_expo_message(
        *,
        token: PushDeviceToken,
        title: str,
        body: str,
        data: dict[str, Any],
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "to": token.expo_push_token,
            "title": title,
            "body": body,
            "data": data,
        }
        sound = delivery.get("sound")
        if sound:
            message["sound"] = sound
        priority = delivery.get("priority")
        if priority:
            message["priority"] = priority
        channel_id = delivery.get("channelId") or delivery.get("channel_id")
        if channel_id:
            message["channelId"] = channel_id
        return message

    @staticmethod
    def _dedupe_tokens(tokens: list[PushDeviceToken]) -> list[PushDeviceToken]:
        seen: set[str] = set()
        deduped: list[PushDeviceToken] = []
        for token in tokens:
            if token.expo_push_token in seen:
                continue
            seen.add(token.expo_push_token)
            deduped.append(token)
        return deduped

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
