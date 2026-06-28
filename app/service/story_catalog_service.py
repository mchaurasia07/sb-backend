from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.age_groups import validate_age_group
from app.model.response.common import PaginatedResponse
from app.model.response.story_catalog import StoryCatalogResponse
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.story_repository import StoryRepository
from app.service.generic_story_service import DEFAULT_GENERIC_STORY_LANGUAGE


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _content_for_language(contents, language: str):
    normalized_language = language.strip().lower()
    return next(
        (
            content
            for content in (contents or [])
            if str(getattr(content, "language", "")).lower() == normalized_language
        ),
        None,
    )


def _story_json_cover_image_url(story_json: dict) -> str | None:
    if not isinstance(story_json, dict):
        return None

    cover = story_json.get("cover") if isinstance(story_json.get("cover"), dict) else {}
    value = (
        story_json.get("cover_image_url")
        or story_json.get("coverImageUrl")
        or story_json.get("cover_image")
        or story_json.get("coverImage")
        or story_json.get("image_url")
        or story_json.get("imageUrl")
        or cover.get("image_url")
        or cover.get("imageUrl")
    )
    return str(value) if value else None


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
        age_group: str,
        status_filter: str | None = None,
        theme: str | None = None,
        language: str | None = None,
    ) -> PaginatedResponse[StoryCatalogResponse]:
        normalized_age_group = validate_age_group(age_group)
        normalized_theme = theme.strip().lower() if theme and theme.strip() else None
        normalized_language = language.strip().lower() if language and language.strip() else None
        stories, total = await self.generic_stories.list_paginated(
            page=page,
            page_size=page_size,
            age_group=normalized_age_group,
            status=status_filter,
            theme=normalized_theme,
            language=normalized_language,
        )
        available_languages = await self.generic_stories.get_available_languages_by_story_ids(
            [story.id for story in stories]
        )
        items = [
            self._generic_to_catalog(
                story,
                available_languages=available_languages.get(story.id, []),
                language=normalized_language or DEFAULT_GENERIC_STORY_LANGUAGE,
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
    def _generic_to_catalog(
        story,
        *,
        available_languages: list[str],
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
    ) -> StoryCatalogResponse:
        default_content = _content_for_language(
            getattr(story, "contents", []),
            language,
        )
        if default_content is None and language != DEFAULT_GENERIC_STORY_LANGUAGE:
            default_content = _content_for_language(
                getattr(story, "contents", []),
                DEFAULT_GENERIC_STORY_LANGUAGE,
            )
        story_json = default_content.story_json if default_content and isinstance(default_content.story_json, dict) else {}
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
            cover_image_url=_story_json_cover_image_url(story_json) or story.cover_image,
            available_languages=available_languages or [DEFAULT_GENERIC_STORY_LANGUAGE],
            video_created=False,
            video_metadata=None,
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
            genre=None,
            moral=story.moral,
            learning_goal=story.learning_goal,
            reading_time_minutes=None,
            character_type=None,
            total_pages=getattr(story, "total_pages", 0) or len(pages) or len(json_pages) or None,
            cover_image_url=getattr(story, "cover_image", None)
            or _story_json_cover_image_url(story_json)
            or next((page.image_url for page in pages if page.page_type == "cover"), None),
            available_languages=available_languages or ["en"],
            video_created=bool(getattr(story, "video_created", False)),
            video_metadata=getattr(story, "video_metadata", None),
            status=_enum_value(story.status) or "",
            created_at=story.created_at,
            updated_at=story.updated_at,
        )
