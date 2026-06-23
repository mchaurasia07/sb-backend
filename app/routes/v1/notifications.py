import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, status

from app.core.config import settings
from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import AuthException
from app.model.request.notification import (
    NotificationAsyncSendRequest,
    NotificationSendRequest,
    PushTokenRegisterRequest,
    PushTokenUnregisterRequest,
)
from app.model.response.common import ApiResponse, success_response
from app.model.response.notification import NotificationSendResponse, PushTokenResponse

logger = logging.getLogger(__name__)


def require_notification_admin(x_notification_admin_token: str | None = Header(default=None)) -> None:
    expected = settings.NOTIFICATION_ADMIN_TOKEN.strip()
    if not expected or x_notification_admin_token != expected:
        raise AuthException("Notification admin access required", status_code=403, code="NOTIFICATION_ADMIN_REQUIRED")


async def send_queued_notification_background(notification_id: UUID) -> None:
    """Deliver a queued notification with a fresh database session."""
    logger.info("[NOTIFICATION_BACKGROUND] Starting send for notification %s", notification_id)
    async with AsyncSessionLocal() as session:
        try:
            await app_container.request(session).notification.deliver_queued(notification_id)
            logger.info("[NOTIFICATION_BACKGROUND] Finished send for notification %s", notification_id)
        except Exception as exc:
            logger.exception("[NOTIFICATION_BACKGROUND] Failed send for notification %s: %s", notification_id, exc)


class NotificationsRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "/tokens",
            self.register_push_token,
            methods=["POST"],
            response_model=ApiResponse[PushTokenResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/tokens",
            self.unregister_push_token,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/send",
            self.send_notification,
            methods=["POST"],
            response_model=ApiResponse[NotificationSendResponse],
            dependencies=[Depends(require_notification_admin)],
        )
        self.router.add_api_route(
            "/send-async",
            self.send_notification_async,
            methods=["POST"],
            response_model=ApiResponse[NotificationSendResponse],
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_notification_admin)],
        )

    async def register_push_token(
        self,
        payload: PushTokenRegisterRequest,
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PushTokenResponse]:
        data = await container.notification.register_push_token(auth=auth, payload=payload)
        return success_response(data, "Push token registered successfully")

    async def unregister_push_token(
        self,
        payload: PushTokenUnregisterRequest,
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        _ = auth
        await container.notification.unregister_push_token(payload.expo_push_token)
        return success_response(None, "Push token unregistered successfully")

    async def send_notification(
        self,
        payload: NotificationSendRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[NotificationSendResponse]:
        data = await container.notification.send_manual(payload)
        return success_response(data, "Notification send requested successfully")

    async def send_notification_async(
        self,
        payload: NotificationAsyncSendRequest,
        background_tasks: BackgroundTasks,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[NotificationSendResponse]:
        data = await container.notification.queue_manual_async(payload)
        background_tasks.add_task(send_queued_notification_background, data.notification_id)
        return success_response(data, "Notification queued successfully")


router = NotificationsRouter(app_container).router
