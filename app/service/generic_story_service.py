from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException
from app.entity.generic_story import GenericStory, GenericStoryLanguage
from app.model.request.generic_story import GenericStoryCreateRequest, GenericStoryUpdateRequest
from app.model.response.common import PaginatedResponse
from app.model.response.generic_story import (
    GenericStoryListResponse,
    GenericStoryResponse,
)
from app.repository.child_book_repository import ChildBookRepository
from app.repository.generic_story_repository import GenericStoryRepository


DEFAULT_GENERIC_STORY_LANGUAGE = GenericStoryLanguage.EN


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
        language: GenericStoryLanguage = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=language)

    async def get_content(
        self,
        generic_story_id: UUID,
        language: GenericStoryLanguage = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> dict[str, Any]:
        content = await self.generic_stories.get_content_by_story_and_language(
            generic_story_id=generic_story_id,
            language=language,
        )
        if content is None:
            raise NotFoundException("Generic story content not found", "GENERIC_STORY_CONTENT_NOT_FOUND")
        return content.story_json

    async def update(
        self,
        generic_story_id: UUID,
        payload: GenericStoryUpdateRequest,
        language: GenericStoryLanguage = DEFAULT_GENERIC_STORY_LANGUAGE,
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

        generic_story = await self.generic_stories.get_by_id(generic_story.id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=language)

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
                    "language": GenericStoryLanguage(content.get("language") or DEFAULT_GENERIC_STORY_LANGUAGE).value,
                    "story_json": content["story_json"],
                }
            )
        return normalized

    @staticmethod
    def _to_response(
        generic_story: GenericStory,
        *,
        language: GenericStoryLanguage = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> GenericStoryResponse:
        content = next((item for item in generic_story.contents if GenericStoryLanguage(item.language) == language), None)
        if content is None:
            content = next(
                (
                    item
                    for item in generic_story.contents
                    if GenericStoryLanguage(item.language) == DEFAULT_GENERIC_STORY_LANGUAGE
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
            language=GenericStoryLanguage(content.language),
            moral=generic_story.moral,
            learning_goal=generic_story.learning_goal,
            reading_time_minutes=generic_story.reading_time_minutes,
            character_type=generic_story.character_type,
            total_pages=generic_story.total_pages,
            cover_image=generic_story.cover_image,
            story_json=content.story_json,
            available_languages=sorted({GenericStoryLanguage(item.language) for item in generic_story.contents}),
            status=generic_story.status,
            created_at=generic_story.created_at,
            updated_at=generic_story.updated_at,
        )
