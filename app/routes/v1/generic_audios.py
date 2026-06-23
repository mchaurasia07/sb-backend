import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile, status

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.entity.generic_audio import GenericAudioLanguage
from app.entity.notification import NotificationAudience
from app.entity.user import User
from app.model.request.generic_audio import GenericAudioUpdateRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_audio import GenericAudioResponse

logger = logging.getLogger(__name__)


async def send_new_generic_audio_notification_background(*, audio_id: UUID, name: str) -> None:
    async with AsyncSessionLocal() as session:
        try:
            container = app_container.request(session)
            await container.notification.send_to_audience(
                audience=NotificationAudience.CHILDREN,
                event_type="new_generic_audio_added",
                title="New audio is ready",
                body=f"{name} is now available in the audio library.",
                data=container.notification._build_deep_link_data(
                    event_type="new_generic_audio_added",
                    route="audio_library",
                    fallback_route="child_dashboard",
                    params={"audio_id": str(audio_id)},
                ),
                delivery={"channelId": "library-updates", "priority": "high", "sound": "default"},
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send new generic audio notification: audio_id=%s", audio_id)


class GenericAudiosRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "",
            self.create_generic_audio,
            methods=["POST"],
            response_model=ApiResponse[GenericAudioResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "",
            self.list_generic_audios,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[GenericAudioResponse]],
        )
        self.router.add_api_route(
            "/{audio_id}",
            self.get_generic_audio,
            methods=["GET"],
            response_model=ApiResponse[GenericAudioResponse],
        )
        self.router.add_api_route(
            "/{audio_id}",
            self.update_generic_audio,
            methods=["PUT"],
            response_model=ApiResponse[GenericAudioResponse],
        )
        self.router.add_api_route(
            "/{audio_id}",
            self.delete_generic_audio,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )

    async def create_generic_audio(
        self,
        request: Request,
        background_tasks: BackgroundTasks,
        name: str = Form(..., min_length=1, max_length=255),
        language: GenericAudioLanguage = Form(GenericAudioLanguage.EN),
        description: str | None = Form(default=None),
        status_value: Literal["active", "inactive"] = Form(default="active", alias="status"),
        audio_file: UploadFile = File(..., alias="audio"),
        image_file: UploadFile = File(..., alias="image"),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericAudioResponse]:
        _ = current_user
        data = await container.generic_audio.create_with_uploads(
            name=name,
            language=language,
            description=description,
            status_value=status_value,
            audio_file=audio_file,
            image_file=image_file,
            public_base_url=str(request.base_url).rstrip("/"),
        )
        if data.status == "active":
            background_tasks.add_task(
                send_new_generic_audio_notification_background,
                audio_id=data.id,
                name=data.name,
            )
        return success_response(data, "Generic audio created successfully")

    async def list_generic_audios(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
        language: GenericAudioLanguage | None = Query(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[GenericAudioResponse]]:
        _ = current_user
        data = await container.generic_audio.list_paginated(
            page=page,
            page_size=page_size,
            status_filter=status_filter,
            language=language,
        )
        return success_response(data, "Generic audios retrieved successfully")

    async def get_generic_audio(
        self,
        audio_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericAudioResponse]:
        _ = current_user
        data = await container.generic_audio.get(audio_id)
        return success_response(data, "Generic audio retrieved successfully")

    async def update_generic_audio(
        self,
        audio_id: UUID,
        payload: GenericAudioUpdateRequest,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericAudioResponse]:
        _ = current_user
        data = await container.generic_audio.update(audio_id, payload)
        return success_response(data, "Generic audio updated successfully")

    async def delete_generic_audio(
        self,
        audio_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        _ = current_user
        await container.generic_audio.delete(audio_id)
        return success_response(None, "Generic audio deleted successfully")


router = GenericAudiosRouter(app_container).router
