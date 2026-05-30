from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.model.response.common import PaginatedResponse
from app.model.response.story_catalog import StoryCatalogResponse
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.story_repository import StoryRepository
from app.service.generic_story_service import DEFAULT_GENERIC_STORY_LANGUAGE


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


class StoryCatalogService:
    """Shared metadata mappers for generic and custom story list APIs."""

    def __init__(self, session: AsyncSession):
        self.generic_stories = GenericStoryRepository(session)
        self.stories = StoryRepository(session)

    async def list_generic_paginated(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str | None = None,
    ) -> PaginatedResponse[StoryCatalogResponse]:
        stories, total = await self.generic_stories.list_paginated(
            page=page,
            page_size=page_size,
            status=status_filter,
        )
        available_languages = await self.generic_stories.get_available_languages_by_story_ids(
            [story.id for story in stories]
        )
        items = [
            self._generic_to_catalog(
                story,
                available_languages=available_languages.get(story.id, []),
            )
            for story in stories
        ]
        return PaginatedResponse[StoryCatalogResponse].create(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def list_custom_by_child_paginated(
        self,
        *,
        user_id: UUID,
        child_id: UUID,
        page: int,
        page_size: int,
        status_filter: str | None = None,
    ) -> PaginatedResponse[StoryCatalogResponse]:
        stories, total = await self.stories.list_by_user_paginated(
            user_id,
            child_id,
            page=page,
            page_size=page_size,
            status_filter=status_filter,
        )
        available_languages = await self.stories.get_available_languages_by_story_ids(
            [story.id for story in stories]
        )
        return PaginatedResponse[StoryCatalogResponse].create(
            items=[
                self._custom_to_catalog(
                    story,
                    available_languages=available_languages.get(story.id, []),
                )
                for story in stories
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _generic_to_catalog(story, *, available_languages: list[str]) -> StoryCatalogResponse:
        return StoryCatalogResponse(
            id=story.id,
            story_type="generic",
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
            available_languages=available_languages or [DEFAULT_GENERIC_STORY_LANGUAGE],
            status=story.status,
            created_at=story.created_at,
            updated_at=story.updated_at,
        )

    @staticmethod
    def _custom_to_catalog(story, *, available_languages: list[str]) -> StoryCatalogResponse:
        pages = list(getattr(story, "pages", []) or [])
        default_content = next(
            (
                content
                for content in (getattr(story, "contents", []) or [])
                if str(content.language).lower() == "en"
            ),
            None,
        )
        story_json = default_content.story_json if default_content and isinstance(default_content.story_json, dict) else {}
        json_pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        return StoryCatalogResponse(
            id=story.id,
            story_type="custom",
            title=story.title,
            summary=story.summary,
            age_group=_enum_value(story.age_group),
            theme=story.category,
            genre=_enum_value(story.generation_mode),
            moral=story.moral,
            learning_goal=story.learning_goal,
            reading_time_minutes=None,
            character_type=None,
            total_pages=len(pages) or len(json_pages) or None,
            available_languages=available_languages or ["en"],
            status=_enum_value(story.status) or "",
            created_at=story.created_at,
            updated_at=story.updated_at,
        )
