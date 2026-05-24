from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.entity.generic_story import GenericStoryLanguage
from app.entity.user import User
from app.model.request.generic_story import GenericStoryCreateRequest, GenericStoryUpdateRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_story import GenericStoryResponse
from app.service.generic_story_service import GenericStoryService

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
    language: GenericStoryLanguage = Query(GenericStoryLanguage.EN),
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


@router.get("", response_model=ApiResponse[PaginatedResponse[GenericStoryResponse]])
async def list_generic_stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    language: GenericStoryLanguage = Query(GenericStoryLanguage.EN),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[GenericStoryResponse]]:
    data = await GenericStoryService(session).list_paginated(
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        language=language,
    )
    return success_response(data, "Generic stories retrieved successfully")


@router.get("/{generic_story_id}", response_model=ApiResponse[GenericStoryResponse])
async def get_generic_story(
    generic_story_id: UUID,
    language: GenericStoryLanguage = Query(GenericStoryLanguage.EN),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).get(generic_story_id, language=language)
    return success_response(data, "Generic story retrieved successfully")
