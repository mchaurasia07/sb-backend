from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.service.image_optimizer import optimize_display_image
from app.service.image_storage_provider import get_image_storage_service


DEFAULT_GENERIC_STORY_LANGUAGE = "en"
PAGE_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


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
            self._require_story_page(pages_by_number, item.page_number, normalized_language)
            pages_by_number[item.page_number]["text"] = item.text

        content.story_json = story_json
        await self.generic_stories.update_content(content)

        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        return self._to_response(generic_story, language=normalized_language)

    async def update_page_images(
        self,
        generic_story_id: UUID,
        page_image_uploads: dict[str, UploadFile],
        language: str = DEFAULT_GENERIC_STORY_LANGUAGE,
        *,
        public_base_url: str = "",
    ) -> GenericStoryResponse:
        normalized_language = language.strip().lower()
        page_uploads = self._extract_page_image_uploads(page_image_uploads)
        if not page_uploads:
            raise AppException(
                "At least one page image upload is required",
                code="GENERIC_STORY_PAGE_IMAGE_UPLOAD_REQUIRED",
            )

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
        for page_number in page_uploads:
            self._require_story_page(pages_by_number, page_number, normalized_language)

        image_storage = get_image_storage_service()
        page_image_urls: dict[int, str] = {}
        for page_number, upload in page_uploads.items():
            page_image_urls[page_number] = await self._save_uploaded_page_image(
                image_storage,
                story_id=generic_story_id,
                page_number=page_number,
                upload=upload,
                public_base_url=public_base_url,
            )

        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        for story_content in generic_story.contents:
            content_story_json = deepcopy(story_content.story_json)
            if isinstance(content_story_json, dict):
                self._apply_page_image_urls(content_story_json, page_image_urls)
                story_content.story_json = content_story_json
                await self.generic_stories.update_content(story_content)
                if str(story_content.language).lower() == normalized_language:
                    story_json = content_story_json
            elif str(story_content.language).lower() == normalized_language:
                raise AppException(
                    "Generic story content has no pages array",
                    code="GENERIC_STORY_CONTENT_PAGES_MISSING",
                )

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

    @classmethod
    def _require_story_page(
        cls,
        pages_by_number: dict[int, dict],
        page_number: int,
        language: str,
    ) -> dict:
        page = pages_by_number.get(page_number)
        if page is None:
            raise AppException(
                f"Generic story page {page_number} not found",
                code="GENERIC_STORY_PAGE_NOT_FOUND",
                details={"page_number": page_number, "language": language},
            )
        return page

    @classmethod
    def _extract_page_image_uploads(cls, uploads: dict[str, UploadFile]) -> dict[int, UploadFile]:
        page_uploads: dict[int, UploadFile] = {}
        for field_name, upload in uploads.items():
            page_number = cls._page_image_upload_number(field_name)
            if page_number is None:
                continue
            if page_number in page_uploads:
                raise AppException(
                    f"Duplicate image upload provided for page {page_number}",
                    code="GENERIC_STORY_PAGE_IMAGE_DUPLICATE",
                )
            page_uploads[page_number] = upload
        return page_uploads

    @staticmethod
    def _page_image_upload_number(field_name: str) -> int | None:
        normalized = field_name.strip().lower()
        candidates = []
        if normalized.startswith("page_image_"):
            candidates.append(normalized.removeprefix("page_image_"))
        if normalized.startswith("image_page_"):
            candidates.append(normalized.removeprefix("image_page_"))
        if normalized.startswith("page_"):
            candidates.append(normalized.removeprefix("page_").removesuffix("_image"))
        if normalized.startswith("page"):
            candidates.append(normalized.removeprefix("page").removesuffix("_image"))

        for candidate in candidates:
            if candidate.isdigit():
                page_number = int(candidate)
                if page_number > 0:
                    return page_number
        return None

    @classmethod
    async def _save_uploaded_page_image(
        cls,
        image_storage: Any,
        *,
        story_id: UUID,
        page_number: int,
        upload: UploadFile,
        public_base_url: str,
    ) -> str:
        extension = cls._upload_image_extension(upload)
        content = await upload.read()
        if not content:
            raise AppException("Image file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")
        if len(content) > settings.IMAGE_MAX_UPLOAD_BYTES:
            raise AppException(
                "Image must be 5 MB or smaller",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "IMAGE_TOO_LARGE",
            )

        filename = f"page_{page_number}{extension}"
        image_url = await image_storage.save_story_image(story_id, content, filename, public_base_url)
        reduced_content = optimize_display_image(content, filename)
        await image_storage.save_story_reduced_image(story_id, reduced_content, filename, public_base_url)
        return image_url

    @staticmethod
    def _upload_image_extension(upload: UploadFile) -> str:
        content_type = str(upload.content_type or "").lower()
        if content_type in PAGE_IMAGE_CONTENT_TYPES:
            return PAGE_IMAGE_CONTENT_TYPES[content_type]

        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in set(PAGE_IMAGE_CONTENT_TYPES.values()):
            return suffix

        raise AppException(
            "Image must be a JPEG, PNG, or WEBP file",
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_IMAGE_TYPE",
        )

    @classmethod
    def _apply_page_image_urls(cls, story_json: dict[str, Any], page_image_urls: dict[int, str]) -> None:
        if not page_image_urls:
            return
        pages = story_json.get("pages") if isinstance(story_json, dict) else None
        if not isinstance(pages, list):
            return
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            page_number = cls._story_page_number(page) or index
            image_url = page_image_urls.get(page_number)
            if image_url:
                page["image_url"] = image_url
                page.pop("image_dummy", None)

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
