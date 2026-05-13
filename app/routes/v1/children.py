from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.entity.user import User
from app.model.request.child import ChildProfileCreateRequest, ChildProfileUpdateRequest
from app.model.response.child import ActiveChildResponse, ChildProfileResponse
from app.model.response.common import ApiResponse, success_response
from app.service.child_service import ChildService

router = APIRouter()


@router.post("", response_model=ApiResponse[ChildProfileResponse], status_code=status.HTTP_201_CREATED)
async def create_child_profile(
    payload: ChildProfileCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    data = await ChildService(session).create(current_user, payload)
    return success_response(data, "Child profile created successfully")


@router.get("", response_model=ApiResponse[list[ChildProfileResponse]])
async def get_child_profiles(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[ChildProfileResponse]]:
    data = await ChildService(session).list(current_user)
    return success_response(data, "Child profiles fetched successfully")


@router.put("/{child_id}", response_model=ApiResponse[ChildProfileResponse])
async def update_child_profile(
    child_id: UUID,
    payload: ChildProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    data = await ChildService(session).update(current_user, child_id, payload)
    return success_response(data, "Child profile updated successfully")


@router.post("/select/{child_id}", response_model=ApiResponse[ActiveChildResponse])
async def select_active_child_profile(
    child_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ActiveChildResponse]:
    data = await ChildService(session).select_active(current_user, child_id)
    return success_response(data, "Active child profile selected successfully")
