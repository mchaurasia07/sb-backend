from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.model.response.child_activity import ChildActivityResponse
from app.model.response.common import PaginatedResponse
from app.repository.child_activity_repository import ChildActivityRepository
from app.repository.child_repository import ChildRepository


class ChildActivityService:
    """Reusable service for recording and listing child activity events."""

    def __init__(self, session: AsyncSession):
        self.activities = ChildActivityRepository(session)
        self.children = ChildRepository(session)

    async def record_activity(
        self,
        *,
        child_id: UUID,
        activity_name: str,
        activity_type: str,
        resource_name: str | None = None,
        resource_id: UUID | None = None,
        resource_type: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> ChildActivityResponse:
        activity = await self.activities.create(
            child_id=child_id,
            activity_name=activity_name,
            activity_type=activity_type,
            occurred_at=occurred_at or datetime.now(UTC),
            resource_name=resource_name,
            resource_id=resource_id,
            resource_type=resource_type,
            description=description,
            metadata_json=metadata,
        )
        return ChildActivityResponse.model_validate(activity)

    async def list_for_child(
        self,
        *,
        user_id: UUID,
        child_id: UUID,
        page: int,
        page_size: int,
        activity_type: str | None = None,
    ) -> PaginatedResponse[ChildActivityResponse]:
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            from app.core.exceptions import NotFoundException

            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")

        activities, total = await self.activities.list_for_child_paginated(
            child_id=child_id,
            page=page,
            page_size=page_size,
            activity_type=activity_type,
        )
        return PaginatedResponse[ChildActivityResponse].create(
            items=[ChildActivityResponse.model_validate(activity) for activity in activities],
            total=total,
            page=page,
            page_size=page_size,
        )
