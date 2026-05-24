from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.entity.user import User
from app.entity.generic_story import GenericStoryLanguage
from app.model.response.child_book import ChildBookResponse
from app.model.response.common import PaginatedResponse
from app.repository.child_book_repository import ChildBookRepository
from app.repository.child_repository import ChildRepository
from app.repository.generic_story_repository import GenericStoryRepository


class ChildBookService:
    """Child library use cases."""

    def __init__(self, session: AsyncSession):
        self.children = ChildRepository(session)
        self.child_books = ChildBookRepository(session)
        self.generic_stories = GenericStoryRepository(session)

    async def list_for_child(
        self,
        *,
        current_user: User,
        child_id: UUID,
        page: int,
        page_size: int,
        status_filter: str | None = None,
    ) -> PaginatedResponse[ChildBookResponse]:
        await self._get_child_for_user(current_user, child_id)
        books, total = await self.child_books.list_for_child_paginated(
            child_id=child_id,
            page=page,
            page_size=page_size,
            status=status_filter,
        )
        items = [ChildBookResponse.model_validate(book) for book in books]
        return PaginatedResponse[ChildBookResponse].create(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def add_generic_story(
        self,
        *,
        current_user: User,
        child_id: UUID,
        generic_story_id: UUID,
        language: GenericStoryLanguage = GenericStoryLanguage.EN,
    ) -> ChildBookResponse:
        await self._get_child_for_user(current_user, child_id)

        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        if generic_story.status != "active":
            raise AppException(
                "Only active generic stories can be added to a child",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_STORY_INACTIVE",
            )
        available_languages = {GenericStoryLanguage(content.language) for content in generic_story.contents}
        if language not in available_languages:
            raise AppException(
                "Generic story content is not available for the requested language",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_STORY_LANGUAGE_NOT_AVAILABLE",
            )

        existing = await self.child_books.get_by_child_story(
            child_id=child_id,
            story_id=generic_story.id,
            story_type="generic",
            language=language.value,
        )
        if existing is not None:
            raise ConflictException(
                "Generic story is already available for this child",
                status.HTTP_409_CONFLICT,
                "CHILD_BOOK_ALREADY_EXISTS",
            )

        child_book = await self.child_books.create(
            child_id=child_id,
            story_id=generic_story.id,
            story_type="generic",
            language=language.value,
            title=generic_story.title,
            cover_image=generic_story.cover_image,
            status="not_started",
            last_page_read=0,
            last_page_read_time=None,
        )
        return ChildBookResponse.model_validate(child_book)

    async def delete_child_book(
        self,
        *,
        current_user: User,
        child_id: UUID,
        child_book_id: UUID,
    ) -> None:
        await self._get_child_for_user(current_user, child_id)
        child_book = await self.child_books.get_for_child(child_id, child_book_id)
        if child_book is None:
            raise NotFoundException("Child book not found", "CHILD_BOOK_NOT_FOUND")
        await self.child_books.delete(child_book)

    async def _get_child_for_user(self, current_user: User, child_id: UUID):
        child = await self.children.get_for_user(current_user.id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")
        return child
