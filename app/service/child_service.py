from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.entity.user import User
from app.model.request.child import ChildProfileCreateRequest, ChildProfileUpdateRequest
from app.model.response.child import ActiveChildResponse, ChildProfileResponse
from app.repository.child_repository import ChildRepository
from app.repository.user_repository import UserRepository


class ChildService:
    """Child profile use cases."""

    def __init__(self, session: AsyncSession):
        self.children = ChildRepository(session)
        self.users = UserRepository(session)

    async def create(self, current_user: User, payload: ChildProfileCreateRequest) -> ChildProfileResponse:
        child = await self.children.create(
            user_id=current_user.id,
            child_name=payload.child_name,
            age=payload.age,
            gender=payload.gender,
            avatar_image_url=str(payload.avatar_image_url) if payload.avatar_image_url else None,
        )
        return ChildProfileResponse.model_validate(child)

    async def list(self, current_user: User) -> list[ChildProfileResponse]:
        children = await self.children.list_by_user(current_user.id)
        return [ChildProfileResponse.model_validate(child) for child in children]

    async def update(self, current_user: User, child_id: UUID, payload: ChildProfileUpdateRequest) -> ChildProfileResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", status.HTTP_404_NOT_FOUND, "CHILD_NOT_FOUND")
        update_data = payload.model_dump(exclude_unset=True)
        if "avatar_image_url" in update_data and update_data["avatar_image_url"] is not None:
            update_data["avatar_image_url"] = str(update_data["avatar_image_url"])
        for field, value in update_data.items():
            setattr(child, field, value)
        return ChildProfileResponse.model_validate(child)

    async def select_active(self, current_user: User, child_id: UUID) -> ActiveChildResponse:
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", status.HTTP_404_NOT_FOUND, "CHILD_NOT_FOUND")
        await self.users.set_active_child_profile(current_user, child_id)
        return ActiveChildResponse(active_child_profile_id=child_id)
