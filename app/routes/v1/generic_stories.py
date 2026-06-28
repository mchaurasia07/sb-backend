import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile, status

from app.core.age_groups import validate_age_group
from app.core.config import settings
from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import AuthContext, get_auth_context, get_current_user
from app.core.exceptions import AppException
from app.entity.notification import NotificationAudience
from app.entity.user import User
from app.model.request.generic_story import (
    GenericStoryCreateRequest,
    GenericStoryPageTextUpdateRequest,
    GenericStoryStatusUpdateRequest,
    GenericStoryUpdateRequest,
)
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_story import (
    GenericStoryResponse,
)
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.image_optimizer import optimize_display_image

logger = logging.getLogger(__name__)

IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _upload_image_extension(upload: UploadFile) -> str:
    if upload.content_type in IMAGE_CONTENT_TYPES:
        return IMAGE_CONTENT_TYPES[upload.content_type]

    filename = upload.filename or ""
    for extension in {".jpg", ".jpeg", ".png", ".webp"}:
        if filename.lower().endswith(extension):
            return ".jpg" if extension == ".jpeg" else extension

    raise AppException("Image must be a JPEG, PNG, or WEBP image", status.HTTP_400_BAD_REQUEST, "UNSUPPORTED_IMAGE_TYPE")


def _content_type_for_extension(extension: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(extension.lower(), "application/octet-stream")


def _is_upload_value(value) -> bool:
    return hasattr(value, "read") and hasattr(value, "filename")


async def _read_uploads_from_form(request: Request, *, duplicate_code: str) -> dict[str, UploadFile]:
    form = await request.form()
    uploads: dict[str, UploadFile] = {}
    for field_name, value in form.multi_items():
        if not _is_upload_value(value):
            continue
        normalized_field_name = str(field_name).strip()
        if normalized_field_name in uploads:
            raise AppException(
                f"Duplicate upload field: {normalized_field_name}",
                code=duplicate_code,
            )
        uploads[normalized_field_name] = value
    return uploads


async def send_new_generic_story_notification_background(*, story_id: UUID, title: str) -> None:
    async with AsyncSessionLocal() as session:
        try:
            container = app_container.request(session)
            await container.notification.send_to_audience(
                audience=NotificationAudience.CHILDREN,
                event_type="new_generic_story_added",
                title="New story in the library",
                body=f"{title} is ready to read.",
                data=container.notification._build_deep_link_data(
                    event_type="new_generic_story_added",
                    route="generic_story_detail",
                    fallback_route="child_dashboard",
                    params={"generic_story_id": str(story_id)},
                ),
                delivery={"channelId": "story-updates", "priority": "high", "sound": "default"},
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send new generic story notification: story_id=%s", story_id)


class GenericStoriesRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "",
            self.create_generic_story,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/images/reduce",
            self.reduce_generic_story_image_for_preview,
            methods=["POST"],
            response_class=Response,
        )
        self.router.add_api_route(
            "/{generic_story_id}",
            self.update_generic_story,
            methods=["PUT"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}/status",
            self.update_generic_story_status,
            methods=["PATCH"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}",
            self.delete_generic_story,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "",
            self.list_generic_stories,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[StoryCatalogResponse]],
        )
        self.router.add_api_route(
            "/{generic_story_id}/content",
            self.get_generic_story_content,
            methods=["GET"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{generic_story_id}",
            self.get_generic_story,
            methods=["GET"],
            response_model=ApiResponse[GenericStoryResponse],
        )

    async def create_generic_story(
        self,
        payload: GenericStoryCreateRequest,
        background_tasks: BackgroundTasks,
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.create(payload)
        if data.status == "active":
            background_tasks.add_task(
                send_new_generic_story_notification_background,
                story_id=data.id,
                title=data.title,
            )
        return success_response(data, "Generic story created successfully")

    async def reduce_generic_story_image_for_preview(
        self,
        image: UploadFile = File(...),
        max_dimension: int = Query(1600, ge=320, le=4096),
        _: User = Depends(get_current_user),
    ) -> Response:
        extension = _upload_image_extension(image)
        content = await image.read()
        if not content:
            raise AppException("Image file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")
        if len(content) > settings.IMAGE_MAX_UPLOAD_BYTES:
            raise AppException(
                "Image must be 5 MB or smaller",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "IMAGE_TOO_LARGE",
            )

        filename = image.filename or f"image{extension}"
        if "." not in filename:
            filename = f"{filename}{extension}"
        reduced = optimize_display_image(content, filename, max_dimension=max_dimension)
        media_type = image.content_type if image.content_type in IMAGE_CONTENT_TYPES else _content_type_for_extension(extension)
        return Response(
            content=reduced,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="reduced_{filename}"'},
        )

    async def update_generic_story(
        self,
        generic_story_id: UUID,
        payload: GenericStoryUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.update(generic_story_id, payload, language=language)
        return success_response(data, "Generic story updated successfully")

    async def update_generic_story_status(
        self,
        generic_story_id: UUID,
        payload: GenericStoryStatusUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.update_status(generic_story_id, payload, language=language)
        return success_response(data, "Generic story status updated successfully")

    async def delete_generic_story(
        self,
        generic_story_id: UUID,
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.generic_story.delete(generic_story_id)
        return success_response(None, "Generic story deleted successfully")

    async def list_generic_stories(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        age_group: str = Query(..., min_length=1, max_length=32),
        theme: str | None = Query(default=None, min_length=1, max_length=100),
        language: str | None = Query(default=None, min_length=2, max_length=16),
        status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
        _: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
        age_group = validate_age_group(age_group)
        data = await container.story_catalog.list_generic_paginated(
            page=page,
            page_size=page_size,
            age_group=age_group,
            theme=theme,
            language=language,
            status_filter=status_filter,
        )
        return success_response(data, "Generic stories retrieved successfully")

    async def get_generic_story_content(
        self,
        generic_story_id: UUID,
        language: str = Query("en", min_length=2, max_length=16),
        _: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryContentResponse]:
        data = await container.generic_story.get_content(generic_story_id, language=language)
        return success_response(data, "Generic story content retrieved successfully")

    async def update_generic_story_page_text(
        self,
        generic_story_id: UUID,
        payload: GenericStoryPageTextUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.update_page_text(generic_story_id, payload, language=language)
        return success_response(data, "Generic story retrieved successfully")

    async def update_generic_story_page_images(
        self,
        generic_story_id: UUID,
        request: Request,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        page_image_uploads = await _read_uploads_from_form(
            request,
            duplicate_code="GENERIC_STORY_PAGE_IMAGE_DUPLICATE_FIELD",
        )
        data = await container.generic_story.update_page_images(
            generic_story_id,
            page_image_uploads,
            language=language,
            public_base_url=str(request.base_url).rstrip("/"),
        )
        return success_response(data, "Generic story page images updated successfully")

    async def update_generic_story_page_audio(
        self,
        generic_story_id: UUID,
        request: Request,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        page_audio_uploads = await _read_uploads_from_form(
            request,
            duplicate_code="GENERIC_STORY_PAGE_AUDIO_DUPLICATE_FIELD",
        )
        data = await container.generic_story.update_page_audio(
            generic_story_id,
            page_audio_uploads,
            language=language,
        )
        return success_response(data, "Generic story page audio updated successfully")

    async def get_generic_story(
        self,
        generic_story_id: UUID,
        language: str = Query("en", min_length=2, max_length=16),
        _: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.get(generic_story_id, language=language)
        return success_response(data, "Generic story retrieved successfully")


router = GenericStoriesRouter(app_container).router
