from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppException, NotFoundException
from app.model.request.generic_story import GenericStoryPageTextUpdateRequest, GenericStoryStatusUpdateRequest
from app.service.generic_story_service import GenericStoryService


class _FakeGenericStoryRepository:
    def __init__(self, story=None):
        self.story = story
        self.requested_ids = []
        self.flush_called = False
        self.updated_content = None

    async def get_by_id(self, generic_story_id):
        self.requested_ids.append(generic_story_id)
        if self.story is None or self.story.id != generic_story_id:
            return None
        return self.story

    async def get_content_by_story_and_language(self, *, generic_story_id, language):
        if self.story is None or self.story.id != generic_story_id:
            return None
        return next((content for content in self.story.contents if content.language == language), None)

    async def update_content(self, content):
        self.updated_content = content
        return content

    async def flush(self):
        self.flush_called = True


def _generic_story(status="active"):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        title="The Moon Bell",
        summary="A child listens carefully.",
        age_group="3-6",
        theme="listening",
        genre="bedtime",
        moral="Listening helps.",
        learning_goal="Careful listening",
        reading_time_minutes=3,
        character_type="child",
        total_pages=8,
        cover_image="https://cdn.example.test/cover.png",
        status=status,
        created_at=now,
        updated_at=now,
        contents=[
            SimpleNamespace(
                language="en",
                story_json={
                    "title": "The Moon Bell",
                    "pages": [
                        {
                            "page_number": 1,
                            "text": "Mira heard the old bell.",
                            "emotion": "wonder",
                        },
                        {
                            "page_number": 2,
                            "text": "The moon glowed softly.",
                            "image_url": "https://cdn.example.test/page-2.png",
                        },
                    ],
                },
            )
        ],
    )


@pytest.mark.asyncio
async def test_update_generic_story_status_updates_only_status():
    story = _generic_story(status="active")
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)

    response = await service.update_status(
        story.id,
        GenericStoryStatusUpdateRequest(status="inactive"),
    )

    assert story.status == "inactive"
    assert service.generic_stories.flush_called is True
    assert response.id == story.id
    assert response.status == "inactive"
    assert response.title == "The Moon Bell"


@pytest.mark.asyncio
async def test_update_generic_story_status_raises_when_story_missing():
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(None)

    with pytest.raises(NotFoundException):
        await service.update_status(uuid4(), GenericStoryStatusUpdateRequest(status="inactive"))


@pytest.mark.asyncio
async def test_update_generic_story_page_text_updates_only_requested_page_text():
    story = _generic_story()
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)
    payload = GenericStoryPageTextUpdateRequest.model_validate(
        {
            "pages": [
                {"page_number": 1, "text": "Mira heard the new bell."},
                {"page_number": 2, "text": "The moon glowed brightly."},
            ]
        }
    )

    response = await service.update_page_text(story.id, payload, language="EN")

    pages = story.contents[0].story_json["pages"]
    assert pages[0] == {
        "page_number": 1,
        "text": "Mira heard the new bell.",
        "emotion": "wonder",
    }
    assert pages[1] == {
        "page_number": 2,
        "text": "The moon glowed brightly.",
        "image_url": "https://cdn.example.test/page-2.png",
    }
    assert service.generic_stories.updated_content is story.contents[0]
    assert response.language == "en"
    assert response.story_json["pages"] == pages


@pytest.mark.asyncio
async def test_update_generic_story_page_text_raises_when_language_content_missing():
    story = _generic_story()
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)
    payload = GenericStoryPageTextUpdateRequest.model_validate({"pages": [{"page_number": 1, "text": "Hindi text."}]})

    with pytest.raises(NotFoundException):
        await service.update_page_text(story.id, payload, language="hi")

    assert service.generic_stories.updated_content is None


@pytest.mark.asyncio
async def test_update_generic_story_page_text_raises_when_page_missing_without_mutating_content():
    story = _generic_story()
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)
    payload = GenericStoryPageTextUpdateRequest.model_validate({"pages": [{"page_number": 9, "text": "Missing page."}]})

    with pytest.raises(AppException) as exc_info:
        await service.update_page_text(story.id, payload, language="en")

    assert exc_info.value.code == "GENERIC_STORY_PAGE_NOT_FOUND"
    assert story.contents[0].story_json["pages"][0]["text"] == "Mira heard the old bell."
    assert service.generic_stories.updated_content is None


def test_generic_story_page_text_update_request_rejects_duplicate_pages():
    with pytest.raises(ValidationError):
        GenericStoryPageTextUpdateRequest.model_validate(
            {"pages": [{"page_number": 1, "text": "First."}, {"page_number": 1, "text": "Second."}]}
        )


def test_generic_story_page_text_update_request_requires_page_number_field():
    with pytest.raises(ValidationError):
        GenericStoryPageTextUpdateRequest.model_validate({"pages": [{"page": 1, "text": "Wrong field."}]})


def test_generic_story_page_text_update_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        GenericStoryPageTextUpdateRequest.model_validate(
            {"pages": [{"page_number": 1, "text": "Updated text.", "emotion": "joy"}]}
        )


def test_generic_story_status_update_request_rejects_invalid_status():
    with pytest.raises(ValidationError):
        GenericStoryStatusUpdateRequest(status="archived")
