import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_auth_context, AuthContext
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.story import StoryVideoResponse
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.story_catalog_service import StoryCatalogService
from app.service.story_service import StoryService
from app.service.story_video_service import StoryVideoService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=ApiResponse[PaginatedResponse[StoryCatalogResponse]])
async def list_custom_stories(
    child_id: UUID = Query(..., description="Child profile ID whose custom stories should be returned."),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
    data = await StoryCatalogService(session).list_custom_by_child_paginated(
        user_id=auth.user_id,
        child_id=child_id,
        page=page,
        page_size=page_size,
        status_filter="COMPLETED",
    )
    return success_response(data, "Custom stories retrieved successfully")


@router.get(
    "/{story_id}/content",
    response_model=ApiResponse[StoryContentResponse],
    response_model_exclude_none=True,
)
async def get_custom_story_content(
    story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryContentResponse]:
    data = await StoryService(session).get_story_content(
        user_id=auth.user_id,
        story_id=story_id,
        language=language,
    )
    return success_response(data, "Custom story content retrieved successfully")


@router.post(
    "/{story_id}/video",
    response_model=ApiResponse[StoryVideoResponse],
    status_code=status.HTTP_200_OK,
)
async def generate_custom_story_video(
    story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    overwrite: bool = Query(False),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryVideoResponse]:
    logger.info(
        "Custom story video generation request received: story_id=%s user_id=%s language=%s overwrite=%s",
        story_id,
        auth.user_id,
        language,
        overwrite,
    )
    try:
        data = await StoryVideoService(session).generate_video(
            user_id=auth.user_id,
            story_id=story_id,
            language=language,
            overwrite=overwrite,
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
        "Custom story video generation request completed: story_id=%s user_id=%s language=%s status=%s video_url=%s local_video_path=%s",
        story_id,
        auth.user_id,
        data.language,
        data.status,
        data.video_url,
        data.local_video_path,
    )
    return success_response(data, "Custom story video generated successfully")


@router.get(
    "/{story_id}/video",
    response_model=ApiResponse[StoryVideoResponse],
)
async def get_custom_story_video_status(
    story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryVideoResponse]:
    data = await StoryVideoService(session).get_video_status(
        user_id=auth.user_id,
        story_id=story_id,
        language=language,
    )
    return success_response(data, "Custom story video status retrieved successfully")
