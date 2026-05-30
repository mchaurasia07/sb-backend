from datetime import date
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db_session
from app.core.dependencies import AuthContext, get_auth_context, get_current_user
from app.core.exceptions import AuthException
from app.entity.user import User
from app.model.request.character import CharacterGenerationRequest
from app.model.request.child import (
    ChildAccountStatusUpdateRequest,
    ChildPasswordUpdateRequest,
    ChildProfileCreateRequest,
    ChildProfileUpdateRequest,
    ChildUsernameUpdateRequest,
)
from app.model.request.child_book import ChildBookProgressUpdateRequest, ChildBookStatusUpdateRequest
from app.model.request.generic_story import AddCustomStoryToChildRequest, AddGenericStoryToChildRequest
from app.model.response.child_activity import ChildActivityResponse
from app.model.response.child_book import ChildBookResponse
from app.model.response.character import CharacterGenerationResponse
from app.model.response.child import ActiveChildResponse, ChildProfileResponse, ChildUsernameAvailabilityResponse
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.service.character_service import CharacterService
from app.service.child_activity_service import ChildActivityService
from app.service.child_book_service import ChildBookActivityEvent, ChildBookService
from app.service.child_service import ChildService

logger = logging.getLogger(__name__)
router = APIRouter()


def resolve_book_update_user_id(auth: AuthContext, child_id: UUID) -> UUID:
    if auth.is_child:
        if auth.child_id != child_id:
            raise AuthException("Child token cannot update another child profile", status_code=403, code="CHILD_ACCESS_DENIED")
        return auth.user_id
    return auth.user_id


async def record_child_activity_background(event: ChildBookActivityEvent) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await ChildActivityService(session).record_activity(
                child_id=event.child_id,
                activity_name=event.activity_name,
                activity_type=event.activity_type,
                resource_name=event.resource_name,
                resource_id=event.resource_id,
                resource_type=event.resource_type,
                description=event.description,
                metadata=event.metadata,
                occurred_at=event.occurred_at,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Failed to record child activity: child_id=%s activity=%s", event.child_id, event.activity_name)


async def update_child_book_status_background(
    *,
    user_id: UUID,
    child_id: UUID,
    child_book_id: UUID,
    payload: ChildBookStatusUpdateRequest,
) -> None:
    async with AsyncSessionLocal() as session:
        try:
            result = await ChildBookService(session).update_status(
                current_user=user_id,
                child_id=child_id,
                child_book_id=child_book_id,
                payload=payload,
            )
            if result.activity_event is not None:
                await ChildActivityService(session).record_activity(
                    child_id=result.activity_event.child_id,
                    activity_name=result.activity_event.activity_name,
                    activity_type=result.activity_event.activity_type,
                    resource_name=result.activity_event.resource_name,
                    resource_id=result.activity_event.resource_id,
                    resource_type=result.activity_event.resource_type,
                    description=result.activity_event.description,
                    metadata=result.activity_event.metadata,
                    occurred_at=result.activity_event.occurred_at,
                )
                await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update child book status: user_id=%s child_id=%s child_book_id=%s",
                user_id,
                child_id,
                child_book_id,
            )


async def update_child_book_progress_background(
    *,
    user_id: UUID,
    child_id: UUID,
    child_book_id: UUID,
    payload: ChildBookProgressUpdateRequest,
) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await ChildBookService(session).update_progress(
                current_user=user_id,
                child_id=child_id,
                child_book_id=child_book_id,
                payload=payload,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update child book progress: user_id=%s child_id=%s child_book_id=%s",
                user_id,
                child_id,
                child_book_id,
            )


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


@router.get("/username/availability", response_model=ApiResponse[ChildUsernameAvailabilityResponse])
async def check_child_username_availability(
    child_user_id: str = Query(..., min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$"),
    child_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildUsernameAvailabilityResponse]:
    data = await ChildService(session).check_child_username_availability(current_user, child_user_id, child_id)
    return success_response(data, "Child username availability checked successfully")


@router.put("/{child_id}", response_model=ApiResponse[ChildProfileResponse])
async def update_child_profile(
    child_id: UUID,
    payload: ChildProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    data = await ChildService(session).update(current_user, child_id, payload)
    return success_response(data, "Child profile updated successfully")


@router.put("/{child_id}/username", response_model=ApiResponse[ChildProfileResponse])
async def update_child_username(
    child_id: UUID,
    payload: ChildUsernameUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    data = await ChildService(session).update_child_username(current_user, child_id, payload)
    return success_response(data, "Child username updated successfully")


@router.put("/{child_id}/password", response_model=ApiResponse[ChildProfileResponse])
async def update_child_password(
    child_id: UUID,
    payload: ChildPasswordUpdateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    data = await ChildService(session).update_child_password(current_user, child_id, payload)
    return success_response(data, "Child password updated successfully")


@router.patch("/{child_id}/lock", response_model=ApiResponse[ChildProfileResponse])
async def update_child_account_status(
    child_id: UUID,
    payload: ChildAccountStatusUpdateRequest | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildProfileResponse]:
    status_payload = payload or ChildAccountStatusUpdateRequest(active=False)
    data = await ChildService(session).update_child_account_status(current_user, child_id, status_payload)
    message = "Child account unlocked successfully" if status_payload.active else "Child account locked successfully"
    return success_response(data, message)


@router.post("/select/{child_id}", response_model=ApiResponse[ActiveChildResponse])
async def select_active_child_profile(
    child_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ActiveChildResponse]:
    data = await ChildService(session).select_active(current_user, child_id)
    return success_response(data, "Active child profile selected successfully")


@router.get("/{child_id}/books", response_model=ApiResponse[PaginatedResponse[ChildBookResponse]])
async def list_child_books(
    child_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Literal["not_started", "in_progress", "completed"] | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[ChildBookResponse]]:
    data = await ChildBookService(session).list_for_child(
        current_user=current_user,
        child_id=child_id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
    )
    return success_response(data, "Child books fetched successfully")


@router.get("/{child_id}/activities", response_model=ApiResponse[PaginatedResponse[ChildActivityResponse]])
async def list_child_activities(
    child_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    activity_type: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[ChildActivityResponse]]:
    data = await ChildActivityService(session).list_for_child(
        user_id=current_user.id,
        child_id=child_id,
        page=page,
        page_size=page_size,
        activity_type=activity_type,
    )
    return success_response(data, "Child activities fetched successfully")


@router.post(
    "/{child_id}/books/generic",
    response_model=ApiResponse[ChildBookResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_generic_story_to_child(
    child_id: UUID,
    payload: AddGenericStoryToChildRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildBookResponse]:
    data = await ChildBookService(session).add_generic_story(
        current_user=current_user,
        child_id=child_id,
        generic_story_id=payload.generic_story_id,
        language=payload.language,
    )
    return success_response(data, "Generic story added to child successfully")


@router.post(
    "/{child_id}/books/custom",
    response_model=ApiResponse[ChildBookResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_custom_story_to_child(
    child_id: UUID,
    payload: AddCustomStoryToChildRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[ChildBookResponse]:
    data = await ChildBookService(session).add_custom_story(
        current_user=current_user,
        child_id=child_id,
        story_id=payload.story_id,
        language=payload.language,
    )
    return success_response(data, "Custom story added to child successfully")


@router.patch(
    "/{child_id}/books/{child_book_id}/status",
    response_model=ApiResponse[None],
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_child_book_status(
    child_id: UUID,
    child_book_id: UUID,
    payload: ChildBookStatusUpdateRequest,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse[None]:
    user_id = resolve_book_update_user_id(auth, child_id)
    background_tasks.add_task(
        update_child_book_status_background,
        user_id=user_id,
        child_id=child_id,
        child_book_id=child_book_id,
        payload=payload,
    )
    return success_response(None, "Child book status update accepted")


@router.patch(
    "/{child_id}/books/{child_book_id}/progress",
    response_model=ApiResponse[None],
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_child_book_progress(
    child_id: UUID,
    child_book_id: UUID,
    payload: ChildBookProgressUpdateRequest,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(get_auth_context),
) -> ApiResponse[None]:
    user_id = resolve_book_update_user_id(auth, child_id)
    background_tasks.add_task(
        update_child_book_progress_background,
        user_id=user_id,
        child_id=child_id,
        child_book_id=child_book_id,
        payload=payload,
    )
    return success_response(None, "Child book progress update accepted")


@router.delete("/{child_id}/books/{child_book_id}", response_model=ApiResponse[None])
async def delete_child_book(
    child_id: UUID,
    child_book_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await ChildBookService(session).delete_child_book(
        current_user=current_user,
        child_id=child_id,
        child_book_id=child_book_id,
    )
    return success_response(None, "Child book deleted successfully")


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
        ai_provider: Optional AI provider override ("openai" or "google") - defaults to AI_PROVIDER
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
