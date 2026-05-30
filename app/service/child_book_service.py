from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.entity.story import StoryStatus
from app.entity.user import User
from app.entity.generic_story import GenericStoryLanguage
from app.model.request.child_book import ChildBookProgressUpdateRequest, ChildBookStatusUpdateRequest
from app.model.response.child_book import ChildBookResponse
from app.model.response.common import PaginatedResponse
from app.repository.child_book_repository import ChildBookRepository
from app.repository.child_repository import ChildRepository
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.story_repository import StoryRepository


BOOK_STATUS_STARTED = "in_progress"
BOOK_STATUS_COMPLETED = "completed"
BOOK_ACTIVITY_TYPE = "STORY_BOOK"
BOOK_READ_STARTED = "BOOK_READ_STARTED"
BOOK_READ_COMPLETED = "BOOK_READ_COMPLETED"


@dataclass
class ChildBookActivityEvent:
    child_id: UUID
    activity_name: str
    activity_type: str
    resource_name: str
    resource_id: UUID
    resource_type: str
    description: str
    metadata: dict[str, Any]
    occurred_at: datetime


@dataclass
class ChildBookStatusUpdateResult:
    child_book: ChildBookResponse
    activity_event: ChildBookActivityEvent | None = None


class ChildBookService:
    """Child library use cases."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.children = ChildRepository(session)
        self.child_books = ChildBookRepository(session)
        self.generic_stories = GenericStoryRepository(session)
        self.stories = StoryRepository(session)

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
        current_user: User | UUID,
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
        requested_language = language.value
        available_languages = {str(content.language) for content in generic_story.contents}
        if requested_language not in available_languages:
            raise AppException(
                "Generic story content is not available for the requested language",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_STORY_LANGUAGE_NOT_AVAILABLE",
            )

        existing = await self.child_books.get_by_child_story(
            child_id=child_id,
            story_id=generic_story.id,
            story_type="generic",
            language=requested_language,
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
            language=requested_language,
            title=generic_story.title,
            cover_image=generic_story.cover_image,
            status="not_started",
            last_page_read=0,
            last_page_read_time=None,
            reading_started_count=0,
            reading_completed_count=0,
        )
        return ChildBookResponse.model_validate(child_book)

    async def add_custom_story(
        self,
        *,
        current_user: User | UUID,
        child_id: UUID,
        story_id: UUID,
        language: GenericStoryLanguage = GenericStoryLanguage.EN,
    ) -> ChildBookResponse:
        await self._get_child_for_user(current_user, child_id)

        story = await self.stories.get_for_user(current_user.id, story_id)
        if story is None:
            raise NotFoundException("Custom story not found", "CUSTOM_STORY_NOT_FOUND")
        if story.child_id != child_id:
            raise AppException(
                "Custom story does not belong to this child",
                status.HTTP_400_BAD_REQUEST,
                "CUSTOM_STORY_CHILD_MISMATCH",
            )
        if story.status != StoryStatus.COMPLETED:
            raise AppException(
                "Only completed custom stories can be added to a child",
                status.HTTP_400_BAD_REQUEST,
                "CUSTOM_STORY_NOT_COMPLETED",
            )

        requested_language = language.value
        available_languages = {str(content.language) for content in story.contents}
        if requested_language not in available_languages:
            raise AppException(
                "Custom story content is not available for the requested language",
                status.HTTP_400_BAD_REQUEST,
                "CUSTOM_STORY_LANGUAGE_NOT_AVAILABLE",
            )

        existing = await self.child_books.get_by_child_story(
            child_id=child_id,
            story_id=story.id,
            story_type="custom",
            language=requested_language,
        )
        if existing is not None:
            raise ConflictException(
                "Custom story is already available for this child",
                status.HTTP_409_CONFLICT,
                "CHILD_BOOK_ALREADY_EXISTS",
            )

        cover_image = next((page.image_url for page in story.pages if page.page_type == "cover"), None)
        child_book = await self.child_books.create(
            child_id=child_id,
            story_id=story.id,
            story_type="custom",
            language=requested_language,
            title=story.title or "Untitled Story",
            cover_image=cover_image,
            status="not_started",
            last_page_read=0,
            last_page_read_time=None,
            reading_started_count=0,
            reading_completed_count=0,
        )
        return ChildBookResponse.model_validate(child_book)

    async def delete_child_book(
        self,
        *,
        current_user: User | UUID,
        child_id: UUID,
        child_book_id: UUID,
    ) -> None:
        await self._get_child_for_user(current_user, child_id)
        child_book = await self.child_books.get_for_child(child_id, child_book_id)
        if child_book is None:
            raise NotFoundException("Child book not found", "CHILD_BOOK_NOT_FOUND")
        await self.child_books.delete(child_book)

    async def update_status(
        self,
        *,
        current_user: User | UUID,
        child_id: UUID,
        child_book_id: UUID,
        payload: ChildBookStatusUpdateRequest,
    ) -> ChildBookStatusUpdateResult:
        await self._get_child_for_user(current_user, child_id)
        child_book = await self.child_books.get_for_child(child_id, child_book_id, for_update=True)
        if child_book is None:
            raise NotFoundException("Child book not found", "CHILD_BOOK_NOT_FOUND")

        now = datetime.now(UTC)
        if payload.status == "STARTED":
            child_book.reading_started_count = (child_book.reading_started_count or 0) + 1
            if child_book.status not in (BOOK_STATUS_STARTED, BOOK_STATUS_COMPLETED):
                child_book.status = BOOK_STATUS_STARTED
            if child_book.reading_started_at is None:
                child_book.reading_started_at = now
        else:
            child_book.reading_completed_count = (child_book.reading_completed_count or 0) + 1
            if child_book.status != BOOK_STATUS_COMPLETED:
                child_book.status = BOOK_STATUS_COMPLETED
            if child_book.reading_started_at is None:
                child_book.reading_started_at = now
            if child_book.reading_completed_at is None:
                child_book.reading_completed_at = now

        page_number = payload.page_number or payload.last_page_read
        if page_number is not None:
            self._apply_page_progress(child_book, page_number, now)
        elif payload.status == "STARTED" and not child_book.last_page_read:
            self._apply_page_progress(child_book, 1, now)
        else:
            child_book.last_page_read_time = now

        await self.child_books.update(child_book)

        activity_event = self._build_reading_activity_event(
            child_id=child_id,
            child_book_id=child_book.id,
            story_id=child_book.story_id,
            story_type=child_book.story_type,
            language=child_book.language,
            book_title=child_book.title,
            activity_name=BOOK_READ_STARTED if payload.status == "STARTED" else BOOK_READ_COMPLETED,
            description=(
                f"Started book reading for {child_book.title}"
                if payload.status == "STARTED"
                else f"Completed book reading for {child_book.title}"
            ),
            occurred_at=now,
            reading_started_count=child_book.reading_started_count,
            reading_completed_count=child_book.reading_completed_count,
        )

        await self.session.commit()
        return ChildBookStatusUpdateResult(
            child_book=ChildBookResponse.model_validate(child_book),
            activity_event=activity_event,
        )

    async def update_progress(
        self,
        *,
        current_user: User | UUID,
        child_id: UUID,
        child_book_id: UUID,
        payload: ChildBookProgressUpdateRequest,
    ) -> ChildBookResponse:
        await self._get_child_for_user(current_user, child_id)
        child_book = await self.child_books.get_for_child(child_id, child_book_id)
        if child_book is None:
            raise NotFoundException("Child book not found", "CHILD_BOOK_NOT_FOUND")

        self._apply_page_progress(child_book, payload.page_number, datetime.now(UTC))
        await self.child_books.update(child_book)
        await self.session.commit()
        return ChildBookResponse.model_validate(child_book)

    async def _get_child_for_user(self, current_user: User | UUID, child_id: UUID):
        user_id = current_user if isinstance(current_user, UUID) else current_user.id
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")
        return child

    @staticmethod
    def _apply_page_progress(child_book, page_number: int, read_time: datetime) -> None:
        child_book.last_page_read = page_number
        child_book.last_page_read_time = read_time

    @staticmethod
    def _build_reading_activity_event(
        *,
        child_id: UUID,
        child_book_id: UUID,
        story_id: UUID,
        story_type: str,
        language: str,
        book_title: str,
        activity_name: str,
        description: str,
        occurred_at: datetime,
        reading_started_count: int,
        reading_completed_count: int,
    ) -> ChildBookActivityEvent:
        return ChildBookActivityEvent(
            child_id=child_id,
            activity_name=activity_name,
            activity_type=BOOK_ACTIVITY_TYPE,
            resource_name=book_title,
            resource_id=child_book_id,
            resource_type="CHILD_BOOK",
            description=description,
            metadata={
                "story_id": str(story_id),
                "story_type": story_type,
                "language": language,
                "reading_started_count": reading_started_count,
                "reading_completed_count": reading_completed_count,
            },
            occurred_at=occurred_at,
        )
