import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, UploadFile, status

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import AppException
from app.model.request.generic_story import GenericStoryPageTextUpdateRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.story import StoryVideoResponse
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse

logger = logging.getLogger(__name__)


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


async def generate_custom_story_video_background(
    *,
    user_id: UUID,
    story_id: UUID,
    language: str,
) -> None:
    """Generate custom story video with a fresh background database session."""
    logger.info(
        "Custom story video background task started: story_id=%s user_id=%s language=%s",
        story_id,
        user_id,
        language,
    )
    async with AsyncSessionLocal() as session:
        try:
            await app_container.request(session).story_video.generate_video(
                user_id=user_id,
                story_id=story_id,
                language=language,
                overwrite=False,
            )
            logger.info(
                "Custom story video background task completed: story_id=%s user_id=%s language=%s",
                story_id,
                user_id,
                language,
            )
        except Exception:
            logger.exception(
                "Custom story video background task failed: story_id=%s user_id=%s language=%s",
                story_id,
                user_id,
                language,
            )


class CustomStoriesRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "",
            self.list_custom_stories,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[StoryCatalogResponse]],
        )
        self.router.add_api_route(
            "/{story_id}/content",
            self.get_custom_story_content,
            methods=["GET"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{story_id}/content/page-text",
            self.update_custom_story_page_text,
            methods=["PATCH"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{story_id}/content/page-images",
            self.update_custom_story_page_images,
            methods=["PATCH"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{story_id}/content/page-audio",
            self.update_custom_story_page_audio,
            methods=["PATCH"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{story_id}",
            self.get_custom_story_content,
            methods=["GET"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{story_id}/video",
            self.generate_custom_story_video,
            methods=["POST"],
            response_model=ApiResponse[StoryVideoResponse],
            status_code=status.HTTP_200_OK,
        )
        self.router.add_api_route(
            "/{story_id}/video",
            self.get_custom_story_video_status,
            methods=["GET"],
            response_model=ApiResponse[StoryVideoResponse],
        )

    async def list_custom_stories(
        self,
        child_id: UUID = Query(..., description="Child profile ID whose custom stories should be returned."),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
        data = await container.story_catalog.list_custom_by_child_paginated(
            user_id=auth.user_id,
            child_id=child_id,
            page=page,
            page_size=page_size,
            status_filter="COMPLETED",
        )
        return success_response(data, "Custom stories retrieved successfully")

    async def get_custom_story_content(
        self,
        story_id: UUID,
        language: str = Query("en", min_length=2, max_length=16),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryContentResponse]:
        data = await container.story.get_story_content(
            user_id=auth.user_id,
            story_id=story_id,
            language=language,
        )
        return success_response(data, "Custom story content retrieved successfully")

    async def update_custom_story_page_text(
        self,
        story_id: UUID,
        payload: GenericStoryPageTextUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryContentResponse]:
        data = await container.story.update_story_page_text(
            user_id=auth.user_id,
            story_id=story_id,
            payload=payload,
            language=language,
        )
        return success_response(data, "Story page text updated successfully")

    async def update_custom_story_page_images(
        self,
        story_id: UUID,
        request: Request,
        language: str = Query("en", min_length=2, max_length=16),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryContentResponse]:
        page_image_uploads = await _read_uploads_from_form(
            request,
            duplicate_code="STORY_PAGE_IMAGE_DUPLICATE_FIELD",
        )
        data = await container.story.update_story_page_images(
            user_id=auth.user_id,
            story_id=story_id,
            page_image_uploads=page_image_uploads,
            language=language,
            public_base_url=str(request.base_url).rstrip("/"),
        )
        return success_response(data, "Story page images updated successfully")

    async def update_custom_story_page_audio(
        self,
        story_id: UUID,
        request: Request,
        language: str = Query("en", min_length=2, max_length=16),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryContentResponse]:
        page_audio_uploads = await _read_uploads_from_form(
            request,
            duplicate_code="STORY_PAGE_AUDIO_DUPLICATE_FIELD",
        )
        data = await container.story.update_story_page_audio(
            user_id=auth.user_id,
            story_id=story_id,
            page_audio_uploads=page_audio_uploads,
            language=language,
        )
        return success_response(data, "Story page audio updated successfully")

    async def generate_custom_story_video(
        self,
        story_id: UUID,
        background_tasks: BackgroundTasks,
        response: Response,
        language: str = Query("en", min_length=2, max_length=16),
        overwrite: bool = Query(False),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryVideoResponse]:
        logger.info(
            "Custom story video generation request received: story_id=%s user_id=%s language=%s overwrite=%s",
            story_id,
            auth.user_id,
            language,
            overwrite,
        )
        try:
            data, should_start = await container.story_video.prepare_generation(
                user_id=auth.user_id,
                story_id=story_id,
                language=language,
                overwrite=overwrite,
            )
            if should_start:
                response.status_code = status.HTTP_202_ACCEPTED
                background_tasks.add_task(
                    generate_custom_story_video_background,
                    user_id=auth.user_id,
                    story_id=story_id,
                    language=language,
                )
        except Exception:
            logger.exception(
                "Custom story video generation request failed: story_id=%s user_id=%s language=%s overwrite=%s",
                story_id,
                auth.user_id,
                language,
                overwrite,
            )
            raise
        logger.info(
            "Custom story video generation request accepted: story_id=%s user_id=%s language=%s status=%s should_start=%s video_url=%s local_video_path=%s",
            story_id,
            auth.user_id,
            data.language,
            data.status,
            should_start,
            data.video_url,
            data.local_video_path,
        )
        message = "Custom story video generation started" if should_start else "Custom story video status retrieved successfully"
        return success_response(data, message)

    async def get_custom_story_video_status(
        self,
        story_id: UUID,
        language: str = Query("en", min_length=2, max_length=16),
        auth: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryVideoResponse]:
        data = await container.story_video.get_video_status(
            user_id=auth.user_id,
            story_id=story_id,
            language=language,
        )
        return success_response(data, "Custom story video status retrieved successfully")


router = CustomStoriesRouter(app_container).router
