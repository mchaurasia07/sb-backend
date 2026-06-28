from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.model.response.child_library import ChildLibraryBookResponse
from app.model.response.common import PaginatedResponse
from app.repository.child_book_repository import ChildBookRepository


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


class ChildLibraryService:
    """Child dashboard library use cases."""

    def __init__(self, session: AsyncSession):
        self.child_books = ChildBookRepository(session)

    async def list_generic_books(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
    ) -> PaginatedResponse[ChildLibraryBookResponse]:
        rows, total = await self.child_books.list_generic_library_paginated(
            child_id=child_id,
            page=page,
            page_size=page_size,
        )
        return PaginatedResponse[ChildLibraryBookResponse].create(
            items=[self._generic_book_to_response(child_book, story) for child_book, story in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def list_custom_books(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
    ) -> PaginatedResponse[ChildLibraryBookResponse]:
        rows, total = await self.child_books.list_custom_library_paginated(
            child_id=child_id,
            page=page,
            page_size=page_size,
        )
        return PaginatedResponse[ChildLibraryBookResponse].create(
            items=[self._custom_book_to_response(child_book, story) for child_book, story in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _generic_book_to_response(child_book, story) -> ChildLibraryBookResponse:
        return ChildLibraryBookResponse(
            child_book_id=child_book.id,
            child_id=child_book.child_id,
            story_id=child_book.story_id,
            story_type="generic",
            language=child_book.language,
            title=story.title,
            summary=story.summary,
            age_group=story.age_group,
            theme=story.theme,
            genre=story.genre,
            moral=story.moral,
            learning_goal=story.learning_goal,
            reading_time_minutes=story.reading_time_minutes,
            character_type=story.character_type,
            total_pages=story.total_pages,
            cover_image=child_book.cover_image or story.cover_image,
            story_status=story.status,
            book_status=child_book.status,
            last_page_read=child_book.last_page_read,
            last_page_read_time=child_book.last_page_read_time,
            reading_started_at=child_book.reading_started_at,
            reading_completed_at=child_book.reading_completed_at,
            reading_started_count=child_book.reading_started_count,
            reading_completed_count=child_book.reading_completed_count,
            created_at=child_book.created_at,
            updated_at=child_book.updated_at,
        )

    @staticmethod
    def _custom_book_to_response(child_book, story) -> ChildLibraryBookResponse:
        pages = list(getattr(story, "pages", []) or [])
        cover_image = child_book.cover_image or getattr(story, "cover_image", None) or next(
            (page.image_url for page in pages if page.page_type == "cover"),
            None,
        )
        content = next(
            (
                item
                for item in (getattr(story, "contents", []) or [])
                if str(item.language).lower() == child_book.language.lower()
            ),
            None,
        )
        story_json = content.story_json if content and isinstance(content.story_json, dict) else {}
        json_pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        return ChildLibraryBookResponse(
            child_book_id=child_book.id,
            child_id=child_book.child_id,
            story_id=child_book.story_id,
            story_type="custom",
            language=child_book.language,
            title=story.title,
            summary=story.summary,
            age_group=_enum_value(story.age_group),
            theme=story.category,
            genre=None,
            moral=story.moral,
            learning_goal=story.learning_goal,
            reading_time_minutes=None,
            character_type=None,
            total_pages=getattr(story, "total_pages", 0) or len(pages) or len(json_pages) or None,
            cover_image=cover_image,
            story_status=_enum_value(story.status) or "",
            book_status=child_book.status,
            last_page_read=child_book.last_page_read,
            last_page_read_time=child_book.last_page_read_time,
            reading_started_at=child_book.reading_started_at,
            reading_completed_at=child_book.reading_completed_at,
            reading_started_count=child_book.reading_started_count,
            reading_completed_count=child_book.reading_completed_count,
            created_at=child_book.created_at,
            updated_at=child_book.updated_at,
        )
