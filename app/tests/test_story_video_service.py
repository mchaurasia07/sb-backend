from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.core.config import settings
from app.core.exceptions import AppException
from app.service.story_video_service import (
    VIDEO_STATUS_COMPLETED,
    VIDEO_STATUS_FAILED,
    VIDEO_STATUS_IN_PROGRESS,
    StoryVideoService,
    VideoSlide,
    VideoSlideAssets,
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


def _png_bytes(width: int = 320, height: int = 180, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


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
        self._last_timing = {"slide_count": len(slides), "concat_seconds": 0.01}
        return f"https://cdn.example.test/video/stories/{story_id}/{language}/story.mp4"


class _FailingStoryVideoService(StoryVideoService):
    async def _render_and_upload_video(self, *, story_id, language, slides):
        self._last_timing = {"slide_count": len(slides), "asset_read_seconds": 0.01}
        raise AppException("render failed", code="TEST_RENDER_FAILED")


class _AssetOrderStoryVideoService(StoryVideoService):
    def __init__(self):
        self.events = []
        self._last_timing = {}

    async def _read_slide_assets(self, slides):
        self.events.append("assets")
        return [VideoSlideAssets(image_bytes=b"image", audio_bytes=None) for _ in slides]

    def _write_slide_image(self, output_path, image_bytes, slide):
        self.events.append("slide_image")
        output_path.write_bytes(b"image")

    def _render_segment(self, image_path, audio_path, output_path, duration_seconds):
        self.events.append("segment")
        output_path.write_bytes(b"segment")

    def _concat_segments(self, tmpdir, segment_paths, output_path):
        self.events.append("concat")
        output_path.write_bytes(b"video")


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
async def test_prepare_generation_reuses_in_progress_video_without_duplicate_start():
    metadata = {
        "en": {
            "status": VIDEO_STATUS_IN_PROGRESS,
            "requested_at": "2026-06-13T10:00:00+00:00",
            "started_at": "2026-06-13T10:00:01+00:00",
        }
    }
    service, story, _, session, user_id, story_id = _service(metadata=metadata)

    response, should_start = await service.prepare_generation(
        user_id=user_id,
        story_id=story_id,
        language="en",
        overwrite=False,
    )

    assert should_start is False
    assert response.status == VIDEO_STATUS_IN_PROGRESS
    assert session.commits == 0


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
    assert story.video_metadata["en"]["total_seconds"] >= 0
    assert story.video_metadata["en"]["queued_seconds"] == 0
    assert story.video_metadata["en"]["timing"]["slide_count"] == 4
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
    assert story.video_metadata["en"]["total_seconds"] >= 0
    assert story.video_metadata["en"]["timing"]["asset_read_seconds"] == 0.01
    assert session.commits == 2


def test_concat_segments_uses_stream_copy_without_reencoding(monkeypatch, tmp_path):
    captured = {}
    service = StoryVideoService.__new__(StoryVideoService)
    segment_path = tmp_path / "segment_000.mp4"
    output_path = tmp_path / "story.mp4"

    def _capture(cmd):
        captured["cmd"] = cmd

    monkeypatch.setattr(StoryVideoService, "_ffmpeg_executable", staticmethod(lambda: "ffmpeg"))
    monkeypatch.setattr(StoryVideoService, "_run_ffmpeg", staticmethod(_capture))

    service._concat_segments(tmp_path, [segment_path], output_path)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "libx264" not in cmd
    assert "aac" not in cmd


def test_cover_slide_does_not_draw_title_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORY_VIDEO_WIDTH", 320)
    monkeypatch.setattr(settings, "STORY_VIDEO_HEIGHT", 180)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("cover title overlay should not be drawn")

    monkeypatch.setattr(StoryVideoService, "_draw_center_title", staticmethod(_fail_if_called))
    output_path = tmp_path / "cover.png"
    slide = VideoSlide(
        kind="cover",
        image_url="",
        title="Do Not Show",
        text=None,
        page_number=None,
        audio_url=None,
        duration_seconds=1.0,
    )

    StoryVideoService.__new__(StoryVideoService)._write_slide_image(output_path, _png_bytes(), slide)

    with Image.open(output_path) as output:
        assert output.convert("RGB").getpixel((160, 90)) == (255, 255, 255)


def test_page_text_draws_without_black_background_panel(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORY_VIDEO_WIDTH", 320)
    monkeypatch.setattr(settings, "STORY_VIDEO_HEIGHT", 180)
    output_path = tmp_path / "page.png"
    slide = VideoSlide(
        kind="page",
        image_url="",
        title=None,
        text="Short page text.",
        page_number=1,
        audio_url=None,
        duration_seconds=1.0,
    )

    StoryVideoService.__new__(StoryVideoService)._write_slide_image(output_path, _png_bytes(), slide)

    with Image.open(output_path) as output:
        assert output.convert("RGB").getpixel((61, 150)) == (255, 255, 255)


@pytest.mark.asyncio
async def test_render_video_preloads_assets_before_slide_rendering():
    service = _AssetOrderStoryVideoService()
    slides = [
        VideoSlide(
            kind="cover",
            image_url="image",
            title=None,
            text=None,
            page_number=None,
            audio_url=None,
            duration_seconds=1.0,
        )
    ]

    video_bytes = await service._render_video(slides)

    assert video_bytes == b"video"
    assert service.events == ["assets", "slide_image", "segment", "concat"]
    assert service._last_timing["slide_count"] == 1
