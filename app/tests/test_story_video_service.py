from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.service.story_video_service import (
    VIDEO_STATUS_COMPLETED,
    VIDEO_STATUS_FAILED,
    VIDEO_STATUS_IN_PROGRESS,
    StoryVideoService,
)


def _story_json() -> dict:
    return {
        "title": "The King and the Whispering Tree",
        "cover_image_url": "https://cdn.example.test/cover.png",
        "back_cover_image_url": "https://cdn.example.test/back.png",
        "pages": [
            {
                "page_number": 2,
                "text": "Page two text.",
                "image_url": "https://cdn.example.test/page-2.png",
                "audio_url": "https://cdn.example.test/page-2.wav",
                "duration": 4.0,
            },
            {
                "page_number": 1,
                "text": "Page one text.",
                "image_url": "https://cdn.example.test/page-1.png",
                "audio_url": "https://cdn.example.test/page-1.wav",
                "duration": 3.0,
            },
        ],
        "moral": {"text": "Wisdom can come from anyone."},
    }


class _FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeStoryRepository:
    def __init__(self, story, content):
        self.story = story
        self.content = content
        self.updates = 0

    async def get_for_user(self, user_id, story_id):
        if self.story.user_id == user_id and self.story.id == story_id:
            return self.story
        return None

    async def get_for_user_for_update(self, user_id, story_id):
        return await self.get_for_user(user_id, story_id)

    async def get_content_by_story_and_language(self, *, story_id, language):
        if self.story.id == story_id and self.content.language == language:
            return self.content
        return None

    async def update(self, story):
        self.updates += 1
        return story


class _FastStoryVideoService(StoryVideoService):
    async def _render_and_upload_video(self, *, story_id, language, slides):
        self.rendered = {"story_id": story_id, "language": language, "slides": slides}
        return f"https://cdn.example.test/video/stories/{story_id}/{language}/story.mp4"


class _FailingStoryVideoService(StoryVideoService):
    async def _render_and_upload_video(self, *, story_id, language, slides):
        raise AppException("render failed", code="TEST_RENDER_FAILED")


def _service(service_cls=StoryVideoService, *, story_json=None, metadata=None):
    user_id = uuid4()
    story_id = uuid4()
    story = SimpleNamespace(
        id=story_id,
        user_id=user_id,
        title="Story Title",
        video_created=False,
        video_metadata=metadata,
    )
    content = SimpleNamespace(story_id=story_id, language="en", story_json=story_json or _story_json())
    session = _FakeSession()
    service = service_cls(session)
    service.stories = _FakeStoryRepository(story, content)
    return service, story, content, session, user_id, story_id


def test_build_slide_manifest_orders_cover_pages_and_end():
    service, *_ = _service()

    slides = service.build_slide_manifest(_story_json(), story_title="Fallback")

    assert [slide.kind for slide in slides] == ["cover", "page", "page", "end"]
    assert [slide.page_number for slide in slides if slide.kind == "page"] == [1, 2]
    assert slides[0].title == "The King and the Whispering Tree"
    assert slides[-1].title == "The End"
    assert slides[-1].text == "Wisdom can come from anyone."


def test_build_slide_manifest_rejects_missing_page_audio():
    service, *_ = _service()
    payload = _story_json()
    payload["pages"][0].pop("audio_url")

    with pytest.raises(AppException) as exc_info:
        service.build_slide_manifest(payload)

    assert exc_info.value.code == "STORY_VIDEO_PAGE_ASSETS_MISSING"
    assert exc_info.value.details["missing"] == ["audio_url"]


@pytest.mark.asyncio
async def test_prepare_generation_reuses_completed_video_without_overwrite():
    story_id = uuid4()
    metadata = {
        "en": {
            "status": VIDEO_STATUS_COMPLETED,
            "video_url": "https://cdn.example.test/story.mp4",
            "error_message": None,
        }
    }
    service, story, _, session, user_id, _ = _service(metadata=metadata)
    story.id = story_id

    response, should_start = await service.prepare_generation(
        user_id=user_id,
        story_id=story_id,
        language="en",
        overwrite=False,
    )

    assert should_start is False
    assert response.status == VIDEO_STATUS_COMPLETED
    assert response.video_url == "https://cdn.example.test/story.mp4"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_prepare_generation_marks_language_in_progress_when_starting():
    service, story, _, session, user_id, story_id = _service()

    response, should_start = await service.prepare_generation(
        user_id=user_id,
        story_id=story_id,
        language="EN",
        overwrite=False,
    )

    assert should_start is True
    assert response.status == VIDEO_STATUS_IN_PROGRESS
    assert story.video_metadata["en"]["status"] == VIDEO_STATUS_IN_PROGRESS
    assert story.video_created is False
    assert session.commits == 1


@pytest.mark.asyncio
async def test_generate_video_updates_completed_metadata():
    service, story, _, session, user_id, story_id = _service(_FastStoryVideoService)

    response = await service.generate_video(
        user_id=user_id,
        story_id=story_id,
        language="en",
        overwrite=True,
    )

    assert response.status == VIDEO_STATUS_COMPLETED
    assert response.video_url.endswith(f"/video/stories/{story_id}/en/story.mp4")
    assert story.video_created is True
    assert story.video_metadata["en"]["status"] == VIDEO_STATUS_COMPLETED
    assert story.video_metadata["en"]["completed_at"]
    assert session.commits == 2


@pytest.mark.asyncio
async def test_generate_video_records_failed_metadata():
    service, story, _, session, user_id, story_id = _service(_FailingStoryVideoService)

    with pytest.raises(AppException) as exc_info:
        await service.generate_video(
            user_id=user_id,
            story_id=story_id,
            language="en",
            overwrite=True,
        )

    assert exc_info.value.code == "TEST_RENDER_FAILED"
    assert story.video_created is False
    assert story.video_metadata["en"]["status"] == VIDEO_STATUS_FAILED
    assert story.video_metadata["en"]["error_message"] == "render failed"
    assert session.commits == 2
