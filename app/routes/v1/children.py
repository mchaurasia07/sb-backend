from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.entity.user import User
from app.model.request.character import CharacterGenerationRequest
from app.model.request.child import ChildProfileCreateRequest, ChildProfileUpdateRequest
from app.model.response.character import CharacterGenerationResponse
from app.model.response.child import ActiveChildResponse, ChildProfileResponse
from app.model.response.common import ApiResponse, success_response
from app.service.character_service import CharacterService
from app.service.child_service import ChildService

router = APIRouter()


@router.post("", response_model=ApiResponse[ChildProfileResponse], status_code=status.HTTP_201_CREATED)
async def create_child_profile(
    request: Request,
    first_name: str = Form(..., min_length=1, max_length=60),
    last_name: str = Form(..., min_length=1, max_length=60),
    dob: date = Form(...),
    age: int = Form(..., ge=0, le=18),
    gender: str | None = Form(default=None, max_length=32),
    photo: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    payload = ChildProfileCreateRequest(first_name=first_name, last_name=last_name, dob=dob, age=age, gender=gender)
    data = await ChildService(session).create(current_user, payload, photo, str(request.base_url).rstrip("/"))
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


@router.post("/{child_id}/generate-character", response_model=ApiResponse[CharacterGenerationResponse])
async def generate_character(
    child_id: UUID,
    request: Request,
    payload: CharacterGenerationRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CharacterGenerationResponse]:
    """Generate AI character from child profile photo.

    Generates a stylized storybook character from the child's profile photo
    and stores it alongside the original. Also generates a character description
    for maintaining visual consistency across story scenes.

    Args:
        ai_provider: AI provider to use ("openai" or "google") - default: "openai"
        additional_context: Optional context like hobbies, personality traits, or styling preferences
    """
    public_base_url = str(request.base_url).rstrip("/")
    data = await CharacterService(session).generate_character(
        child_id=child_id,
        user_id=current_user.id,
        public_base_url=public_base_url,
        payload=payload,
        ai_provider=payload.ai_provider,
    )
    return success_response(data, "Character generated successfully")
