from uuid import UUID
import re

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.entity.user import User
from app.model.request.child import (
    ChildAccountStatusUpdateRequest,
    ChildPasswordUpdateRequest,
    ChildProfileCreateRequest,
    ChildProfileUpdateRequest,
    ChildUsernameUpdateRequest,
)
from app.model.response.child import ActiveChildResponse, ChildProfileResponse, ChildUsernameAvailabilityResponse
from app.repository.child_repository import ChildRepository
from app.repository.story_repository import StoryRepository
from app.repository.user_repository import UserRepository
from app.service.image_storage_provider import get_image_storage_service
from app.service.story_audio_storage_provider import get_story_audio_storage_service


class ChildService:
    """Child profile use cases."""

    BLOCKED_USERNAME_SUFFIXES = {69, 420, 666}

    def __init__(self, session: AsyncSession):
        self.children = ChildRepository(session)
        self.stories = StoryRepository(session)
        self.users = UserRepository(session)

    @staticmethod
    def _default_child_password(dob) -> str:
        return dob.strftime("%d%m%Y")

    @staticmethod
    def _username_base(first_name: str) -> str:
        raw_username = first_name.lower()
        username = re.sub(r"[^a-z0-9._-]+", "", raw_username)
        username = re.sub(r"[_-]+", "_", username)
        username = re.sub(r"\.+", "", username).strip("_-")
        return username or "child"

    @classmethod
    def _blocked_child_user_id_reason(cls, child_user_id: str) -> str | None:
        match = re.search(r"(\d+)$", child_user_id)
        if match and int(match.group(1)) in cls.BLOCKED_USERNAME_SUFFIXES:
            return "This child user id uses a blocked number. Please choose another one."
        return None

    async def _unique_child_user_id(self, first_name: str) -> str:
        base_username = self._username_base(first_name)
        existing_user_ids = set(await self.children.list_child_user_ids_by_prefix(base_username))

        suffix = 1
        while True:
            candidate = f"{base_username}_{suffix:02d}"
            if self._blocked_child_user_id_reason(candidate) is None and candidate not in existing_user_ids:
                return candidate
            suffix += 1

    async def create(
        self,
        current_user: User,
        payload: ChildProfileCreateRequest,
        photo: UploadFile,
        public_base_url: str,
    ) -> ChildProfileResponse:
        child_user_id = await self._unique_child_user_id(payload.first_name)
        child = await self.children.create(
            user_id=current_user.id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            dob=payload.dob,
            age=payload.age,
            gender=payload.gender,
            avatar_image_url=None,
            child_user_id=child_user_id,
            child_password=self._default_child_password(payload.dob),
        )
        child.avatar_image_url = await get_image_storage_service().save_child_profile_photo(
            current_user.id,
            child.id,
            photo,
            public_base_url,
        )
        return ChildProfileResponse.model_validate(child)

    async def list(self, current_user: User) -> list[ChildProfileResponse]:
        children = await self.children.list_by_user(current_user.id)
        return [ChildProfileResponse.model_validate(child) for child in children]

    async def update(self, current_user: User, child_id: UUID, payload: ChildProfileUpdateRequest) -> ChildProfileResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")
        update_data = payload.model_dump(exclude_unset=True)
        if "avatar_image_url" in update_data and update_data["avatar_image_url"] is not None:
            update_data["avatar_image_url"] = str(update_data["avatar_image_url"])
        for field, value in update_data.items():
            setattr(child, field, value)
        await self.children.update(child)
        return ChildProfileResponse.model_validate(child)

    async def update_child_username(
        self,
        current_user: User,
        child_id: UUID,
        payload: ChildUsernameUpdateRequest,
    ) -> ChildProfileResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")

        child_user_id = payload.child_user_id.strip().lower()
        blocked_reason = self._blocked_child_user_id_reason(child_user_id)
        if blocked_reason is not None:
            raise AppException(blocked_reason, code="CHILD_USERNAME_NOT_ALLOWED")

        existing = await self.children.get_by_child_user_id(child_user_id)
        if existing is not None and existing.id != child.id:
            raise ConflictException("Child username already exists", status.HTTP_409_CONFLICT, "CHILD_USERNAME_EXISTS")

        child.child_user_id = child_user_id
        await self.children.update(child)
        return ChildProfileResponse.model_validate(child)

    async def check_child_username_availability(
        self,
        current_user: User,
        child_user_id: str,
        child_id: UUID | None = None,
    ) -> ChildUsernameAvailabilityResponse:
        normalized_child_user_id = child_user_id.strip().lower()

        if child_id is not None:
            child = await self.children.get_for_user(current_user.id, child_id)
            if child is None:
                raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")

        blocked_reason = self._blocked_child_user_id_reason(normalized_child_user_id)
        if blocked_reason is not None:
            return ChildUsernameAvailabilityResponse(
                child_user_id=normalized_child_user_id,
                available=False,
                reason=blocked_reason,
            )

        existing = await self.children.get_by_child_user_id(normalized_child_user_id)
        if existing is not None and existing.id != child_id:
            return ChildUsernameAvailabilityResponse(
                child_user_id=normalized_child_user_id,
                available=False,
                reason="Child user id already exists.",
            )

        return ChildUsernameAvailabilityResponse(child_user_id=normalized_child_user_id, available=True)

    async def update_child_password(
        self,
        current_user: User,
        child_id: UUID,
        payload: ChildPasswordUpdateRequest,
    ) -> ChildProfileResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")

        child.child_password = payload.child_password
        await self.children.update(child)
        return ChildProfileResponse.model_validate(child)

    async def update_child_account_status(
        self,
        current_user: User,
        child_id: UUID,
        payload: ChildAccountStatusUpdateRequest,
    ) -> ChildProfileResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")

        child.active = payload.active
        await self.children.update(child)
        return ChildProfileResponse.model_validate(child)

    async def select_active(self, current_user: User, child_id: UUID) -> ActiveChildResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")
        await self.users.set_active_child_profile(current_user, child_id)
        return ActiveChildResponse(active_child_profile_id=child_id)

    async def delete(self, current_user: User, child_id: UUID) -> None:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")

        stories = await self.stories.list_by_user(current_user.id, child_id=child.id)
        story_ids = [story.id for story in stories]

        image_storage = get_image_storage_service()
        audio_storage = get_story_audio_storage_service()

        for story_id in story_ids:
            await image_storage.delete_story_directory(story_id)
            await audio_storage.delete_story_directory(story_id)

        await image_storage.delete_child_profile_directory(current_user.id, child.id)

        if current_user.active_child_profile_id == child.id:
            current_user.active_child_profile_id = None

        await self.children.delete(child)
