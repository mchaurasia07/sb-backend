import io
import wave
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
        self.updated_contents = []

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
        self.updated_contents.append(content)
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


class _FakeUpload:
    def __init__(self, content: bytes = b"image-bytes", filename: str = "page.png", content_type: str = "image/png"):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._content


class _FakeImageStorage:
    def __init__(self):
        self.saved_images = []
        self.saved_reduced_images = []

    async def save_story_image(self, story_id, image_bytes, filename, public_base_url):
        self.saved_images.append((story_id, image_bytes, filename, public_base_url))
        return f"https://cdn.example.test/stories/{story_id}/{filename}"

    async def save_story_reduced_image(self, story_id, image_bytes, filename, public_base_url):
        self.saved_reduced_images.append((story_id, image_bytes, filename, public_base_url))
        return f"https://cdn.example.test/stories/{story_id}/reduced/{filename}"


class _FakeAudioStorage:
    def __init__(self):
        self.saved_audio = []

    async def save_story_page_audio(
        self,
        *,
        story_id,
        language,
        page_number,
        audio_bytes,
        extension=".wav",
        content_type="audio/wav",
    ):
        self.saved_audio.append((story_id, language, page_number, audio_bytes, extension, content_type))
        return f"https://cdn.example.test/audio/stories/{story_id}/{language}/page_{page_number}{extension}"


def _wav_bytes(duration_seconds: float = 1.0, sample_rate: int = 8000) -> bytes:
    frame_count = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_update_generic_story_page_images_updates_all_languages_without_text_changes(monkeypatch):
    story = _generic_story()
    story.contents.append(
        SimpleNamespace(
            language="hi",
            story_json={
                "title": "Chaand ki Ghanti",
                "pages": [
                    {"page_number": 1, "text": "Mira ne purani ghanti suni.", "image_url": "old-page-1"},
                    {"page_number": 2, "text": "Chaand dheere chamka."},
                ],
            },
        )
    )
    storage = _FakeImageStorage()
    monkeypatch.setattr("app.service.generic_story_service.get_image_storage_service", lambda: storage)
    monkeypatch.setattr("app.service.generic_story_service.optimize_display_image", lambda content, filename: b"reduced")
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)

    response = await service.update_page_images(
        story.id,
        {"page_1": _FakeUpload()},
        language="en",
        public_base_url="https://api.example.test",
    )

    image_url = f"https://cdn.example.test/stories/{story.id}/page_1.png"
    assert storage.saved_images == [(story.id, b"image-bytes", "page_1.png", "https://api.example.test")]
    assert storage.saved_reduced_images == [(story.id, b"reduced", "page_1.png", "https://api.example.test")]
    assert story.contents[0].story_json["pages"][0]["text"] == "Mira heard the old bell."
    assert story.contents[0].story_json["pages"][0]["image_url"] == image_url
    assert story.contents[1].story_json["pages"][0]["text"] == "Mira ne purani ghanti suni."
    assert story.contents[1].story_json["pages"][0]["image_url"] == image_url
    assert service.generic_stories.updated_contents == story.contents
    assert response.story_json["pages"][0]["image_url"] == image_url


@pytest.mark.asyncio
async def test_update_generic_story_page_audio_updates_requested_language_only(monkeypatch):
    story = _generic_story()
    story.contents[0].story_json["pages"][0]["audio_url"] = "old-en-1"
    story.contents[0].story_json["pages"][1]["audio_url"] = "old-en-2"
    story.contents.append(
        SimpleNamespace(
            language="hi",
            story_json={
                "title": "Chaand ki Ghanti",
                "pages": [
                    {
                        "page_number": 1,
                        "text": "Hindi page 1.",
                        "audio_url": "old-hi-1",
                        "audio_dummy": True,
                    },
                    {
                        "page_number": 2,
                        "text": "Hindi page 2.",
                        "audio_url": "old-hi-2",
                        "tts_skipped": True,
                    },
                ],
            },
        )
    )
    page_1_audio = _wav_bytes(duration_seconds=2.0)
    page_2_audio = _wav_bytes(duration_seconds=4.0)
    storage = _FakeAudioStorage()
    monkeypatch.setattr("app.service.generic_story_service.get_story_audio_storage_service", lambda: storage)
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)

    response = await service.update_page_audio(
        story.id,
        {
            "page_audio_1": _FakeUpload(page_1_audio, "page-1.wav", "audio/wav"),
            "audio_page_2": _FakeUpload(page_2_audio, "page-2.wav", "audio/wav"),
        },
        language="HI",
    )

    page_1_url = f"https://cdn.example.test/audio/stories/{story.id}/hi/page_1.wav"
    page_2_url = f"https://cdn.example.test/audio/stories/{story.id}/hi/page_2.wav"
    hi_pages = story.contents[1].story_json["pages"]
    assert storage.saved_audio == [
        (story.id, "hi", 1, page_1_audio, ".wav", "audio/wav"),
        (story.id, "hi", 2, page_2_audio, ".wav", "audio/wav"),
    ]
    assert story.contents[0].story_json["pages"][0]["audio_url"] == "old-en-1"
    assert story.contents[0].story_json["pages"][1]["audio_url"] == "old-en-2"
    assert hi_pages[0]["audio_url"] == page_1_url
    assert hi_pages[0]["duration"] == 2.0
    assert hi_pages[0]["word_timestamps"] == [{"word": "Hindi page 1.", "start": 0.0, "end": 2.0}]
    assert "audio_dummy" not in hi_pages[0]
    assert hi_pages[1]["audio_url"] == page_2_url
    assert hi_pages[1]["duration"] == 4.0
    assert hi_pages[1]["word_timestamps"] == [{"word": "Hindi page 2.", "start": 0.0, "end": 4.0}]
    assert "tts_skipped" not in hi_pages[1]
    assert service.generic_stories.updated_content is story.contents[1]
    assert response.language == "hi"
    assert response.story_json["pages"][0]["audio_url"] == page_1_url


def test_extract_page_audio_uploads_accepts_aliases_and_rejects_duplicates():
    uploads = {
        "page_audio_1": object(),
        "audio_page_2": object(),
        "page_3_audio": object(),
        "page4_audio": object(),
        "ignored": object(),
    }

    page_uploads = GenericStoryService._extract_page_audio_uploads(uploads)

    assert sorted(page_uploads) == [1, 2, 3, 4]
    with pytest.raises(AppException) as exc_info:
        GenericStoryService._extract_page_audio_uploads(
            {
                "page_audio_1": object(),
                "page1_audio": object(),
            }
        )
    assert exc_info.value.code == "GENERIC_STORY_PAGE_AUDIO_DUPLICATE"


@pytest.mark.asyncio
async def test_update_generic_story_page_audio_accepts_non_wav_upload(monkeypatch):
    story = _generic_story()
    storage = _FakeAudioStorage()
    monkeypatch.setattr("app.service.generic_story_service.get_story_audio_storage_service", lambda: storage)
    monkeypatch.setattr(
        GenericStoryService,
        "_uploaded_audio_duration_seconds",
        staticmethod(lambda audio_bytes: 3.5),
    )
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)

    response = await service.update_page_audio(
        story.id,
        {"page_audio_1": _FakeUpload(b"mp3-bytes", "page-1.mp3", "audio/mpeg")},
        language="en",
    )

    audio_url = f"https://cdn.example.test/audio/stories/{story.id}/en/page_1.mp3"
    assert storage.saved_audio == [(story.id, "en", 1, b"mp3-bytes", ".mp3", "audio/mpeg")]
    assert response.story_json["pages"][0]["audio_url"] == audio_url
    assert response.story_json["pages"][0]["duration"] == 3.5
    assert response.story_json["pages"][0]["word_timestamps"] == [
        {"word": "Mira heard the old bell.", "start": 0.0, "end": 3.5}
    ]


@pytest.mark.asyncio
async def test_update_generic_story_page_audio_rejects_when_duration_unavailable(monkeypatch):
    story = _generic_story()
    story.contents[0].story_json["pages"][0]["audio_url"] = "old-en-1"
    story.contents[0].story_json["pages"][0]["duration"] = 9.9
    story.contents[0].story_json["pages"][0]["word_timestamps"] = [{"word": "old", "start": 0.0, "end": 9.9}]
    storage = _FakeAudioStorage()
    monkeypatch.setattr("app.service.generic_story_service.get_story_audio_storage_service", lambda: storage)
    monkeypatch.setattr(
        GenericStoryService,
        "_uploaded_audio_duration_seconds",
        staticmethod(
            lambda audio_bytes: (_ for _ in ()).throw(
                AppException(
                    "Audio duration could not be determined",
                    code="AUDIO_DURATION_UNAVAILABLE",
                )
            )
        ),
    )
    service = GenericStoryService.__new__(GenericStoryService)
    service.generic_stories = _FakeGenericStoryRepository(story)

    with pytest.raises(AppException) as exc_info:
        await service.update_page_audio(
            story.id,
            {"page_audio_1": _FakeUpload(b"bad-audio", "page-1.webm", "audio/webm")},
            language="en",
        )

    assert exc_info.value.code == "AUDIO_DURATION_UNAVAILABLE"
    assert storage.saved_audio == []
    assert story.contents[0].story_json["pages"][0]["audio_url"] == "old-en-1"
    assert story.contents[0].story_json["pages"][0]["duration"] == 9.9
    assert story.contents[0].story_json["pages"][0]["word_timestamps"] == [{"word": "old", "start": 0.0, "end": 9.9}]


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
