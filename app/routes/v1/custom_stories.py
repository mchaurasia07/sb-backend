from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_auth_context, AuthContext
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.story_catalog_service import StoryCatalogService
from app.service.story_service import StoryService

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
