from typing import Literal
from uuid import UUID
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db_session
from app.core.dependencies import get_current_user, get_auth_context, AuthContext
from app.entity.notification import NotificationAudience
from app.entity.user import User
from app.model.request.generic_story import GenericStoryCreateRequest, GenericStoryUpdateRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_story import (
    GenericStoryResponse,
)
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.generic_story_service import GenericStoryService
from app.service.notification_service import NotificationService
from app.service.story_catalog_service import StoryCatalogService

router = APIRouter()
logger = logging.getLogger(__name__)


async def send_new_generic_story_notification_background(*, story_id: UUID, title: str) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await NotificationService(session).send_to_audience(
                audience=NotificationAudience.CHILDREN,
                event_type="new_generic_story_added",
                title="New story available",
                body=f"{title} is now available in the story library.",
                data={
                    "event_type": "new_generic_story_added",
                    "generic_story_id": str(story_id),
                    "screen": "generic_story_detail",
                },
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send new generic story notification: story_id=%s", story_id)


@router.post("", response_model=ApiResponse[GenericStoryResponse], status_code=status.HTTP_201_CREATED)
async def create_generic_story(
    payload: GenericStoryCreateRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).create(payload)
    if data.status == "active":
        background_tasks.add_task(
            send_new_generic_story_notification_background,
            story_id=data.id,
            title=data.title,
        )
    return success_response(data, "Generic story created successfully")


@router.put("/{generic_story_id}", response_model=ApiResponse[GenericStoryResponse])
async def update_generic_story(
    generic_story_id: UUID,
    payload: GenericStoryUpdateRequest,
    language: str = Query("en", min_length=2, max_length=16),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).update(generic_story_id, payload, language=language)
    return success_response(data, "Generic story updated successfully")


@router.delete("/{generic_story_id}", response_model=ApiResponse[None])
async def delete_generic_story(
    generic_story_id: UUID,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await GenericStoryService(session).delete(generic_story_id)
    return success_response(None, "Generic story deleted successfully")


@router.get("", response_model=ApiResponse[PaginatedResponse[StoryCatalogResponse]])
async def list_generic_stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    age_group: str = Query(..., min_length=1, max_length=32),
    theme: str | None = Query(default=None, min_length=1, max_length=100),
    language: str | None = Query(default=None, min_length=2, max_length=16),
    status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
    data = await StoryCatalogService(session).list_generic_paginated(
        page=page,
        page_size=page_size,
        age_group=age_group,
        theme=theme,
        language=language,
        status_filter=status_filter,
    )
    return success_response(data, "Generic stories retrieved successfully")


@router.get(
    "/{generic_story_id}/content",
    response_model=ApiResponse[StoryContentResponse],
    response_model_exclude_none=True,
)
async def get_generic_story_content(
    generic_story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryContentResponse]:
    data = await GenericStoryService(session).get_content(generic_story_id, language=language)
    return success_response(data, "Generic story content retrieved successfully")


@router.get("/{generic_story_id}", response_model=ApiResponse[GenericStoryResponse])
async def get_generic_story(
    generic_story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).get(generic_story_id, language=language)
    return success_response(data, "Generic story retrieved successfully")
