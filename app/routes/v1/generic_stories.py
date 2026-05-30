from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user, get_auth_context, AuthContext
from app.entity.user import User
from app.model.request.generic_story import GenericStoryCreateRequest, GenericStoryUpdateRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_story import (
    GenericStoryResponse,
)
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.generic_story_service import GenericStoryService
from app.service.story_catalog_service import StoryCatalogService

router = APIRouter()


@router.post("", response_model=ApiResponse[GenericStoryResponse], status_code=status.HTTP_201_CREATED)
async def create_generic_story(
    payload: GenericStoryCreateRequest,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).create(payload)
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
    status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
    data = await StoryCatalogService(session).list_generic_paginated(
        page=page,
        page_size=page_size,
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
