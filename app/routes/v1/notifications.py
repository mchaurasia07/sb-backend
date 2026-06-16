import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db_session
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
from app.service.notification_service import NotificationService

logger = logging.getLogger(__name__)
router = APIRouter()


def require_notification_admin(x_notification_admin_token: str | None = Header(default=None)) -> None:
    expected = settings.NOTIFICATION_ADMIN_TOKEN.strip()
    if not expected or x_notification_admin_token != expected:
        raise AuthException("Notification admin access required", status_code=403, code="NOTIFICATION_ADMIN_REQUIRED")


async def send_queued_notification_background(notification_id: UUID) -> None:
    """Deliver a queued notification with a fresh database session."""
    logger.info("[NOTIFICATION_BACKGROUND] Starting send for notification %s", notification_id)
    async with AsyncSessionLocal() as session:
        try:
            await NotificationService(session).deliver_queued(notification_id)
            logger.info("[NOTIFICATION_BACKGROUND] Finished send for notification %s", notification_id)
        except Exception as exc:
            logger.exception("[NOTIFICATION_BACKGROUND] Failed send for notification %s: %s", notification_id, exc)


@router.post(
    "/tokens",
    response_model=ApiResponse[PushTokenResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_push_token(
    payload: PushTokenRegisterRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PushTokenResponse]:
    data = await NotificationService(session).register_push_token(auth=auth, payload=payload)
    return success_response(data, "Push token registered successfully")


@router.delete("/tokens", response_model=ApiResponse[None])
async def unregister_push_token(
    payload: PushTokenUnregisterRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await NotificationService(session).unregister_push_token(payload.expo_push_token)
    return success_response(None, "Push token unregistered successfully")


@router.post(
    "/send",
    response_model=ApiResponse[NotificationSendResponse],
    dependencies=[Depends(require_notification_admin)],
)
async def send_notification(
    payload: NotificationSendRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[NotificationSendResponse]:
    data = await NotificationService(session).send_manual(payload)
    return success_response(data, "Notification send requested successfully")


@router.post(
    "/send-async",
    response_model=ApiResponse[NotificationSendResponse],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_notification_admin)],
)
async def send_notification_async(
    payload: NotificationAsyncSendRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[NotificationSendResponse]:
    data = await NotificationService(session).queue_manual_async(payload)
    background_tasks.add_task(send_queued_notification_background, data.notification_id)
    return success_response(data, "Notification queued successfully")
