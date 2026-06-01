from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import AuthException
from app.model.request.notification import (
    NotificationSendRequest,
    PushTokenRegisterRequest,
    PushTokenUnregisterRequest,
)
from app.model.response.common import ApiResponse, success_response
from app.model.response.notification import NotificationSendResponse, PushTokenResponse
from app.service.notification_service import NotificationService

router = APIRouter()


def require_notification_admin(x_notification_admin_token: str | None = Header(default=None)) -> None:
    expected = settings.NOTIFICATION_ADMIN_TOKEN.strip()
    if not expected or x_notification_admin_token != expected:
        raise AuthException("Notification admin access required", status_code=403, code="NOTIFICATION_ADMIN_REQUIRED")


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
