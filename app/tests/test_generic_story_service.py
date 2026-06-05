from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import NotFoundException
from app.model.request.generic_story import GenericStoryStatusUpdateRequest
from app.service.generic_story_service import GenericStoryService


class _FakeGenericStoryRepository:
    def __init__(self, story=None):
        self.story = story
        self.requested_ids = []
        self.flush_called = False

    async def get_by_id(self, generic_story_id):
        self.requested_ids.append(generic_story_id)
        if self.story is None or self.story.id != generic_story_id:
            return None
        return self.story

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
                story_json={"title": "The Moon Bell", "pages": []},
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


def test_generic_story_status_update_request_rejects_invalid_status():
    with pytest.raises(ValidationError):
        GenericStoryStatusUpdateRequest(status="archived")
