from copy import deepcopy
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException
from app.entity.generic_story import GenericStory
from app.model.request.generic_story import (
    GenericStoryCreateRequest,
    GenericStoryPageTextUpdateRequest,
    GenericStoryStatusUpdateRequest,
    GenericStoryUpdateRequest,
)
from app.model.response.common import PaginatedResponse
from app.model.response.generic_story import (
    GenericStoryListResponse,
    GenericStoryResponse,
)
from app.model.response.story_content import StoryContentResponse
from app.repository.child_book_repository import ChildBookRepository
from app.repository.generic_story_repository import GenericStoryRepository


DEFAULT_GENERIC_STORY_LANGUAGE = "en"


class GenericStoryService:
    """Generic story catalog use cases."""

    def __init__(self, session: AsyncSession):
        self.generic_stories = GenericStoryRepository(session)
        self.child_books = ChildBookRepository(session)

    async def create(self, payload: GenericStoryCreateRequest) -> GenericStoryResponse:
        data = payload.model_dump()
        story_contents = data.pop("story_contents", [])
        normalized_contents = self._normalize_contents(story_contents)
        if not normalized_contents:
            raise AppException("At least one story content item is required", code="GENERIC_STORY_CONTENT_REQUIRED")

        generic_story = await self.generic_stories.get_by_title(data["title"])
        if generic_story is None:
            generic_story = await self.generic_stories.create(**data)
        else:
            for field, value in data.items():
                setattr(generic_story, field, value)

        await self.generic_stories.upsert_contents(generic_story, normalized_contents)
        generic_story = await self.generic_stories.get_by_id(generic_story.id)
        if generic_story is None:
            raise NotFoundException("Generic story not found after creation", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=DEFAULT_GENERIC_STORY_LANGUAGE)

    async def get(
        self,
        generic_story_id: UUID,
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=language)

    async def get_content(
        self,
        generic_story_id: UUID,
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> StoryContentResponse:
        normalized_language = language.strip().lower()
        content = await self.generic_stories.get_content_by_story_and_language(
            generic_story_id=generic_story_id,
            language=normalized_language,
        )
        if content is None:
            raise NotFoundException("Generic story content not found", "GENERIC_STORY_CONTENT_NOT_FOUND")
        return StoryContentResponse(
            story_id=generic_story_id,
            story_type="generic",
            language=str(content.language),
            story_json=content.story_json,
        )

    async def update(
        self,
        generic_story_id: UUID,
        payload: GenericStoryUpdateRequest,
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        update_data = payload.model_dump(exclude_unset=True)
        story_contents = update_data.pop("story_contents", None)

        for field, value in update_data.items():
            setattr(generic_story, field, value)
        if story_contents is not None:
            await self.generic_stories.upsert_contents(
                generic_story,
                self._normalize_contents(story_contents),
            )
        else:
            await self.generic_stories.flush()

        generic_story = await self.generic_stories.get_by_id(generic_story.id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=language)

    async def update_status(
        self,
        generic_story_id: UUID,
        payload: GenericStoryStatusUpdateRequest,
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        generic_story.status = payload.status
        await self.generic_stories.flush()

        generic_story = await self.generic_stories.get_by_id(generic_story.id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=language)

    async def update_page_text(
        self,
        generic_story_id: UUID,
        payload: GenericStoryPageTextUpdateRequest,
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        normalized_language = language.strip().lower()
        content = await self.generic_stories.get_content_by_story_and_language(
            generic_story_id=generic_story_id,
            language=normalized_language,
        )
        if content is None:
            raise NotFoundException("Generic story content not found", "GENERIC_STORY_CONTENT_NOT_FOUND")

        story_json = deepcopy(content.story_json)
        pages = story_json.get("pages") if isinstance(story_json, dict) else None
        if not isinstance(pages, list):
            raise AppException(
                "Generic story content has no pages array",
                code="GENERIC_STORY_CONTENT_PAGES_MISSING",
            )

        pages_by_number = {
            page_number: page
            for page in pages
            if isinstance(page, dict) and (page_number := self._story_page_number(page)) is not None
        }
        for item in payload.pages:
            page = pages_by_number.get(item.page_number)
            if page is None:
                raise AppException(
                    f"Generic story page {item.page_number} not found",
                    code="GENERIC_STORY_PAGE_NOT_FOUND",
                    details={"page_number": item.page_number, "language": normalized_language},
                )
            page["text"] = item.text

        content.story_json = story_json
        await self.generic_stories.update_content(content)

        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=normalized_language)

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
    ) -> PaginatedResponse[GenericStoryListResponse]:
        stories, total = await self.generic_stories.list_paginated(
            page=page,
            page_size=page_size,
            status=status_filter,
        )
        available_languages = await self.generic_stories.get_available_languages_by_story_ids(
            [story.id for story in stories]
        )
        items = [
            GenericStoryListResponse.model_validate(story).model_copy(
                update={"available_languages": available_languages.get(story.id, [])}
            )
            for story in stories
        ]
        return PaginatedResponse[GenericStoryListResponse].create(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _normalize_contents(contents: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for content in contents:
            normalized.append(
                {
                    "language": str(content.get("language") or DEFAULT_GENERIC_STORY_LANGUAGE),
                    "story_json": content["story_json"],
                }
            )
        return normalized

    @staticmethod
    def _story_page_number(page: dict) -> int | None:
        raw_page_number = page.get("page_number", page.get("page"))
        if isinstance(raw_page_number, bool):
            return None
        if isinstance(raw_page_number, int):
            return raw_page_number
        if isinstance(raw_page_number, str) and raw_page_number.strip().isdigit():
            return int(raw_page_number.strip())
        return None

    @staticmethod
    def _to_response(
        generic_story: GenericStory,
        *,
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        content = next((item for item in generic_story.contents if str(item.language) == language), None)
        if content is None:
            content = next(
                (
                    item
                    for item in generic_story.contents
                    if str(item.language) == DEFAULT_GENERIC_STORY_LANGUAGE
                ),
                None,
            )
        if content is None:
            raise AppException("Generic story has no story content", code="GENERIC_STORY_CONTENT_MISSING")

        return GenericStoryResponse(
            id=generic_story.id,
            title=generic_story.title,
            summary=generic_story.summary,
            age_group=generic_story.age_group,
            theme=generic_story.theme,
            genre=generic_story.genre,
            language=str(content.language),
            moral=generic_story.moral,
            learning_goal=generic_story.learning_goal,
            reading_time_minutes=generic_story.reading_time_minutes,
            character_type=generic_story.character_type,
            total_pages=generic_story.total_pages,
            cover_image=generic_story.cover_image,
            story_json=content.story_json,
            available_languages=sorted({str(item.language) for item in generic_story.contents}),
            status=generic_story.status,
            created_at=generic_story.created_at,
            updated_at=generic_story.updated_at,
        )
