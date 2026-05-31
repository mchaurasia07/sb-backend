from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.entity.generic_audio import GenericAudioLanguage
from app.entity.user import User
from app.model.request.generic_audio import GenericAudioUpdateRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_audio import GenericAudioResponse
from app.service.generic_audio_service import GenericAudioService

router = APIRouter()


@router.post("", response_model=ApiResponse[GenericAudioResponse], status_code=status.HTTP_201_CREATED)
async def create_generic_audio(
    request: Request,
    name: str = Form(..., min_length=1, max_length=255),
    language: GenericAudioLanguage = Form(GenericAudioLanguage.EN),
    description: str | None = Form(default=None),
    status_value: Literal["active", "inactive"] = Form(default="active", alias="status"),
    audio_file: UploadFile = File(..., alias="audio"),
    image_file: UploadFile = File(..., alias="image"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericAudioResponse]:
    data = await GenericAudioService(session).create_with_uploads(
        name=name,
        language=language,
        description=description,
        status_value=status_value,
        audio_file=audio_file,
        image_file=image_file,
        public_base_url=str(request.base_url).rstrip("/"),
    )
    return success_response(data, "Generic audio created successfully")


@router.get("", response_model=ApiResponse[PaginatedResponse[GenericAudioResponse]])
async def list_generic_audios(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    language: GenericAudioLanguage | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[GenericAudioResponse]]:
    data = await GenericAudioService(session).list_paginated(
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        language=language,
    )
    return success_response(data, "Generic audios retrieved successfully")


@router.get("/{audio_id}", response_model=ApiResponse[GenericAudioResponse])
async def get_generic_audio(
    audio_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericAudioResponse]:
    data = await GenericAudioService(session).get(audio_id)
    return success_response(data, "Generic audio retrieved successfully")


@router.put("/{audio_id}", response_model=ApiResponse[GenericAudioResponse])
async def update_generic_audio(
    audio_id: UUID,
    payload: GenericAudioUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericAudioResponse]:
    data = await GenericAudioService(session).update(audio_id, payload)
    return success_response(data, "Generic audio updated successfully")


@router.delete("/{audio_id}", response_model=ApiResponse[None])
async def delete_generic_audio(
    audio_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await GenericAudioService(session).delete(audio_id)
    return success_response(None, "Generic audio deleted successfully")
