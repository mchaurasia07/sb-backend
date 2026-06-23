from datetime import date
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Query, Request, UploadFile, status

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import AuthContext, get_auth_context, get_current_user
from app.core.exceptions import AuthException
from app.entity.generic_audio import GenericAudioLanguage
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
from app.model.request.generic_audio import AddGenericAudioToChildRequest
from app.model.request.generic_story import AddCustomStoryToChildRequest, AddGenericStoryToChildRequest
from app.model.response.character import CharacterGenerationResponse
from app.model.response.child import ActiveChildResponse, ChildProfileResponse, ChildUsernameAvailabilityResponse
from app.model.response.child_activity import ChildActivityResponse
from app.model.response.child_audio import ChildAudioResponse
from app.model.response.child_book import ChildBookResponse
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.service.child_book_service import ChildBookActivityEvent

logger = logging.getLogger(__name__)


def resolve_book_update_user_id(auth: AuthContext, child_id: UUID) -> UUID:
    if auth.is_child:
        if auth.child_id != child_id:
            raise AuthException("Child token cannot update another child profile", status_code=403, code="CHILD_ACCESS_DENIED")
        return auth.user_id
    return auth.user_id


async def record_child_activity_background(event: ChildBookActivityEvent) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await app_container.request(session).child_activity.record_activity(
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
            container = app_container.request(session)
            result = await container.child_book.update_status(
                current_user=user_id,
                child_id=child_id,
                child_book_id=child_book_id,
                payload=payload,
            )
            if result.activity_event is not None:
                await container.child_activity.record_activity(
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
            await app_container.request(session).child_book.update_progress(
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


async def send_child_story_added_notification_background(
    *,
    child_id: UUID,
    child_book_id: UUID,
    story_id: UUID,
    story_title: str,
    story_type: str,
) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await app_container.request(session).notification.send_child_story_added(
                child_id=child_id,
                child_book_id=child_book_id,
                story_id=story_id,
                story_title=story_title,
                story_type=story_type,
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send child story notification: child_id=%s story_id=%s", child_id, story_id)


async def send_child_audio_added_notification_background(
    *,
    child_id: UUID,
    child_audio_id: UUID,
    audio_id: UUID,
    audio_name: str,
) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await app_container.request(session).notification.send_child_audio_added(
                child_id=child_id,
                child_audio_id=child_audio_id,
                audio_id=audio_id,
                audio_name=audio_name,
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send child audio notification: child_id=%s audio_id=%s", child_id, audio_id)


class ChildrenRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "",
            self.create_child_profile,
            methods=["POST"],
            response_model=ApiResponse[ChildProfileResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route("", self.get_child_profiles, methods=["GET"], response_model=ApiResponse[list[ChildProfileResponse]])
        self.router.add_api_route(
            "/username/availability",
            self.check_child_username_availability,
            methods=["GET"],
            response_model=ApiResponse[ChildUsernameAvailabilityResponse],
        )
        self.router.add_api_route(
            "/{child_id}",
            self.update_child_profile,
            methods=["PUT"],
            response_model=ApiResponse[ChildProfileResponse],
        )
        self.router.add_api_route(
            "/{child_id}/username",
            self.update_child_username,
            methods=["PUT"],
            response_model=ApiResponse[ChildProfileResponse],
        )
        self.router.add_api_route(
            "/{child_id}/password",
            self.update_child_password,
            methods=["PUT"],
            response_model=ApiResponse[ChildProfileResponse],
        )
        self.router.add_api_route(
            "/{child_id}/lock",
            self.update_child_account_status,
            methods=["PATCH"],
            response_model=ApiResponse[ChildProfileResponse],
        )
        self.router.add_api_route(
            "/select/{child_id}",
            self.select_active_child_profile,
            methods=["POST"],
            response_model=ApiResponse[ActiveChildResponse],
        )
        self.router.add_api_route("/{child_id}", self.delete_child_profile, methods=["DELETE"], response_model=ApiResponse[None])
        self.router.add_api_route(
            "/{child_id}/books",
            self.list_child_books,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[ChildBookResponse]],
        )
        self.router.add_api_route(
            "/{child_id}/activities",
            self.list_child_activities,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[ChildActivityResponse]],
        )
        self.router.add_api_route(
            "/{child_id}/audios",
            self.list_child_audios,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[ChildAudioResponse]],
        )
        self.router.add_api_route(
            "/{child_id}/audios",
            self.add_generic_audio_to_child,
            methods=["POST"],
            response_model=ApiResponse[ChildAudioResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/{child_id}/books/generic",
            self.add_generic_story_to_child,
            methods=["POST"],
            response_model=ApiResponse[ChildBookResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/{child_id}/books/custom",
            self.add_custom_story_to_child,
            methods=["POST"],
            response_model=ApiResponse[ChildBookResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/{child_id}/books/{child_book_id}/status",
            self.update_child_book_status,
            methods=["PATCH"],
            response_model=ApiResponse[None],
            status_code=status.HTTP_202_ACCEPTED,
        )
        self.router.add_api_route(
            "/{child_id}/books/{child_book_id}/progress",
            self.update_child_book_progress,
            methods=["PATCH"],
            response_model=ApiResponse[None],
            status_code=status.HTTP_202_ACCEPTED,
        )
        self.router.add_api_route(
            "/{child_id}/books/{child_book_id}",
            self.delete_child_book,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/{child_id}/audios/{child_audio_id}",
            self.delete_child_audio,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/{child_id}/generate-character",
            self.generate_character,
            methods=["POST"],
            response_model=ApiResponse[CharacterGenerationResponse],
        )

    async def create_child_profile(
        self,
        request: Request,
        first_name: str = Form(..., min_length=1, max_length=60),
        last_name: str = Form(..., min_length=1, max_length=60),
        dob: date = Form(...),
        age: int = Form(..., ge=0, le=12),
        gender: str | None = Form(default=None, max_length=32),
        photo: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildProfileResponse]:
        payload = ChildProfileCreateRequest(first_name=first_name, last_name=last_name, dob=dob, age=age, gender=gender)
        data = await container.child.create(current_user, payload, photo, str(request.base_url).rstrip("/"))
        return success_response(data, "Child profile created successfully")

    async def get_child_profiles(
        self,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[ChildProfileResponse]]:
        data = await container.child.list(current_user)
        return success_response(data, "Child profiles fetched successfully")

    async def check_child_username_availability(
        self,
        child_user_id: str = Query(..., min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$"),
        child_id: UUID | None = Query(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildUsernameAvailabilityResponse]:
        data = await container.child.check_child_username_availability(current_user, child_user_id, child_id)
        return success_response(data, "Child username availability checked successfully")

    async def update_child_profile(
        self,
        request: Request,
        child_id: UUID,
        first_name: str | None = Form(default=None, min_length=1, max_length=60),
        last_name: str | None = Form(default=None, min_length=1, max_length=60),
        dob: date | None = Form(default=None),
        age: int | None = Form(default=None, ge=0, le=12),
        gender: str | None = Form(default=None, max_length=32),
        photo: UploadFile | None = File(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildProfileResponse]:
        payload = ChildProfileUpdateRequest(
            **{
                key: value
                for key, value in {
                    "first_name": first_name,
                    "last_name": last_name,
                    "dob": dob,
                    "age": age,
                    "gender": gender,
                }.items()
                if value is not None
            }
        )
        data = await container.child.update(
            current_user,
            child_id,
            payload,
            photo=photo,
            public_base_url=str(request.base_url).rstrip("/"),
        )
        return success_response(data, "Child profile updated successfully")

    async def update_child_username(
        self,
        child_id: UUID,
        payload: ChildUsernameUpdateRequest,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildProfileResponse]:
        data = await container.child.update_child_username(current_user, child_id, payload)
        return success_response(data, "Child username updated successfully")

    async def update_child_password(
        self,
        child_id: UUID,
        payload: ChildPasswordUpdateRequest,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildProfileResponse]:
        data = await container.child.update_child_password(current_user, child_id, payload)
        return success_response(data, "Child password updated successfully")

    async def update_child_account_status(
        self,
        child_id: UUID,
        payload: ChildAccountStatusUpdateRequest | None = Body(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildProfileResponse]:
        status_payload = payload or ChildAccountStatusUpdateRequest(active=False)
        data = await container.child.update_child_account_status(current_user, child_id, status_payload)
        message = "Child account unlocked successfully" if status_payload.active else "Child account locked successfully"
        return success_response(data, message)

    async def select_active_child_profile(
        self,
        child_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ActiveChildResponse]:
        data = await container.child.select_active(current_user, child_id)
        return success_response(data, "Active child profile selected successfully")

    async def delete_child_profile(
        self,
        child_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.child.delete(current_user, child_id)
        return success_response(None, "Child profile and related custom stories deleted successfully")

    async def list_child_books(
        self,
        child_id: UUID,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status_filter: Literal["not_started", "in_progress", "completed"] | None = Query(default=None, alias="status"),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[ChildBookResponse]]:
        data = await container.child_book.list_for_child(
            current_user=current_user,
            child_id=child_id,
            page=page,
            page_size=page_size,
            status_filter=status_filter,
        )
        return success_response(data, "Child books fetched successfully")

    async def list_child_activities(
        self,
        child_id: UUID,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        activity_type: str | None = Query(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[ChildActivityResponse]]:
        data = await container.child_activity.list_for_child(
            user_id=current_user.id,
            child_id=child_id,
            page=page,
            page_size=page_size,
            activity_type=activity_type,
        )
        return success_response(data, "Child activities fetched successfully")

    async def list_child_audios(
        self,
        child_id: UUID,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        language: GenericAudioLanguage | None = Query(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[ChildAudioResponse]]:
        data = await container.child_audio.list_for_child(
            current_user=current_user,
            child_id=child_id,
            page=page,
            page_size=page_size,
            language=language,
        )
        return success_response(data, "Child audios fetched successfully")

    async def add_generic_audio_to_child(
        self,
        child_id: UUID,
        payload: AddGenericAudioToChildRequest,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildAudioResponse]:
        data = await container.child_audio.add_generic_audio(
            current_user=current_user,
            child_id=child_id,
            audio_id=payload.audio_id,
        )
        background_tasks.add_task(
            send_child_audio_added_notification_background,
            child_id=child_id,
            child_audio_id=data.child_audio_id,
            audio_id=data.audio_id,
            audio_name=data.name,
        )
        return success_response(data, "Generic audio added to child successfully")

    async def add_generic_story_to_child(
        self,
        child_id: UUID,
        payload: AddGenericStoryToChildRequest,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildBookResponse]:
        data = await container.child_book.add_generic_story(
            current_user=current_user,
            child_id=child_id,
            generic_story_id=payload.generic_story_id,
            language=payload.language,
        )
        background_tasks.add_task(
            send_child_story_added_notification_background,
            child_id=child_id,
            child_book_id=data.id,
            story_id=data.story_id,
            story_title=data.title,
            story_type=data.story_type,
        )
        return success_response(data, "Generic story added to child successfully")

    async def add_custom_story_to_child(
        self,
        child_id: UUID,
        payload: AddCustomStoryToChildRequest,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[ChildBookResponse]:
        data = await container.child_book.add_custom_story(
            current_user=current_user,
            child_id=child_id,
            story_id=payload.story_id,
            language=payload.language,
        )
        background_tasks.add_task(
            send_child_story_added_notification_background,
            child_id=child_id,
            child_book_id=data.id,
            story_id=data.story_id,
            story_title=data.title,
            story_type=data.story_type,
        )
        return success_response(data, "Custom story added to child successfully")

    async def update_child_book_status(
        self,
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

    async def update_child_book_progress(
        self,
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

    async def delete_child_book(
        self,
        child_id: UUID,
        child_book_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.child_book.delete_child_book(
            current_user=current_user,
            child_id=child_id,
            child_book_id=child_book_id,
        )
        return success_response(None, "Child book deleted successfully")

    async def delete_child_audio(
        self,
        child_id: UUID,
        child_audio_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.child_audio.delete_child_audio(
            current_user=current_user,
            child_id=child_id,
            child_audio_id=child_audio_id,
        )
        return success_response(None, "Child audio deleted successfully")

    async def generate_character(
        self,
        child_id: UUID,
        request: Request,
        payload: CharacterGenerationRequest,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[CharacterGenerationResponse]:
        """Generate AI character from child profile photo."""
        public_base_url = str(request.base_url).rstrip("/")
        data = await container.character.generate_character(
            child_id=child_id,
            user_id=current_user.id,
            public_base_url=public_base_url,
            payload=payload,
            ai_provider=payload.ai_provider,
        )
        return success_response(data, "Character generated successfully")


router = ChildrenRouter(app_container).router
