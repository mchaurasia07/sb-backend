from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import AuthException
from app.entity.generic_audio import GenericAudioLanguage
from app.model.response.child_audio import ChildAudioResponse
from app.model.response.child_library import ChildLibraryBookResponse
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.service.child_audio_service import ChildAudioService
from app.service.child_library_service import ChildLibraryService

router = APIRouter()


def require_child_context(auth: AuthContext) -> AuthContext:
    if not auth.is_child or auth.child_id is None:
        raise AuthException("Child access required", status_code=403, code="CHILD_ACCESS_REQUIRED")
    return auth


@router.get("/generic-stories", response_model=ApiResponse[PaginatedResponse[ChildLibraryBookResponse]])
async def list_child_generic_books(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[ChildLibraryBookResponse]]:
    child_auth = require_child_context(auth)
    data = await ChildLibraryService(session).list_generic_books(
        child_id=child_auth.child_id,
        page=page,
        page_size=page_size,
    )
    return success_response(data, "Child generic books retrieved successfully")


@router.get("/custom-stories", response_model=ApiResponse[PaginatedResponse[ChildLibraryBookResponse]])
async def list_child_custom_books(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[ChildLibraryBookResponse]]:
    child_auth = require_child_context(auth)
    data = await ChildLibraryService(session).list_custom_books(
        child_id=child_auth.child_id,
        page=page,
        page_size=page_size,
    )
    return success_response(data, "Child custom books retrieved successfully")


@router.get("/audios", response_model=ApiResponse[PaginatedResponse[ChildAudioResponse]])
async def list_child_audios(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    language: GenericAudioLanguage | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[ChildAudioResponse]]:
    child_auth = require_child_context(auth)
    data = await ChildAudioService(session).list_for_child_library(
        child_id=child_auth.child_id,
        page=page,
        page_size=page_size,
        language=language,
    )
    return success_response(data, "Child audios retrieved successfully")


@router.get("/audios/{child_audio_id}", response_model=ApiResponse[ChildAudioResponse])
async def get_child_audio(
    child_audio_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildAudioResponse]:
    child_auth = require_child_context(auth)
    data = await ChildAudioService(session).get_for_child_library(
        child_id=child_auth.child_id,
        child_audio_id=child_audio_id,
    )
    return success_response(data, "Child audio retrieved successfully")
