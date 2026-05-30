from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import AuthException
from app.model.response.child_library import ChildLibraryBookResponse
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
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
