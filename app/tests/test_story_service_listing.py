from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.entity.story import StoryType
from app.service.story_service import StoryService


class _StoryRow(SimpleNamespace):
    @property
    def pages(self):
        raise AssertionError("list_stories should not load or read page rows")


@pytest.mark.asyncio
async def test_list_stories_returns_languages_without_pages_or_content_json():
    story_id = uuid4()
    now = datetime.now(UTC)
    story = _StoryRow(
        id=story_id,
        title="Moon Bell",
        moral="Listen kindly.",
        summary="A child listens carefully.",
        status=SimpleNamespace(value="COMPLETED"),
        age_group=SimpleNamespace(value="0-3"),
        category="kindness",
        learning_goal="listening",
        total_pages=4,
        cover_image="https://cdn.test/cover.webp",
        video_created=False,
        video_metadata=None,
        created_at=now,
        updated_at=now,
    )
    calls = {}

    class _Stories:
        async def list_by_user_paginated(self, user_id, child_id, **kwargs):
            calls["list"] = {"user_id": user_id, "child_id": child_id, **kwargs}
            return [story], 1

        async def get_available_languages_by_story_ids(self, story_ids):
            calls["languages"] = story_ids
            return {story_id: ["en", "hi"]}

    service = StoryService.__new__(StoryService)
    service.stories = _Stories()
    user_id = uuid4()

    response = await service.list_stories(
        user_id,
        page=1,
        page_size=20,
        age_group="0-3",
        story_type=StoryType.GENERIC,
    )

    assert calls["list"]["include_details"] is False
    assert calls["list"]["age_group"] == "0-3"
    assert calls["list"]["story_type"] == StoryType.GENERIC
    assert calls["languages"] == [story_id]
    assert response.items[0].available_languages == ["en", "hi"]
    assert not hasattr(response.items[0], "pages")
