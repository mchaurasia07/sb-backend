from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.model.request.generic_story import GenericStoryCreateRequest, GenericStoryUpdateRequest
from app.model.response.common import PaginatedResponse
from app.model.response.generic_story import GenericStoryResponse
from app.repository.child_book_repository import ChildBookRepository
from app.repository.generic_story_repository import GenericStoryRepository


class GenericStoryService:
    """Generic story catalog use cases."""

    def __init__(self, session: AsyncSession):
        self.generic_stories = GenericStoryRepository(session)
        self.child_books = ChildBookRepository(session)

    async def create(self, payload: GenericStoryCreateRequest) -> GenericStoryResponse:
        generic_story = await self.generic_stories.create(**payload.model_dump())
        return GenericStoryResponse.model_validate(generic_story)

    async def update(self, generic_story_id: UUID, payload: GenericStoryUpdateRequest) -> GenericStoryResponse:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(generic_story, field, value)

        return GenericStoryResponse.model_validate(generic_story)

    async def delete(self, generic_story_id: UUID) -> None:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        await self.child_books.delete_by_story(story_id=generic_story.id, story_type="generic")
        await self.generic_stories.delete(generic_story)

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str | None = None,
    ) -> PaginatedResponse[GenericStoryResponse]:
        stories, total = await self.generic_stories.list_paginated(
            page=page,
            page_size=page_size,
            status=status_filter,
        )
        items = [GenericStoryResponse.model_validate(story) for story in stories]
        return PaginatedResponse[GenericStoryResponse].create(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
