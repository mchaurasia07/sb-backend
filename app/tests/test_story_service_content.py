from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.entity.story import StoryType
from app.model.request.generic_story import GenericStoryPageTextUpdateRequest
from app.service.generic_story_service import GenericStoryService
from app.service.story_service import StoryService


@pytest.mark.asyncio
async def test_get_story_content_allows_migrated_generic_story():
    user_id = uuid4()
    expected_story_id = uuid4()
    story_json = {"title": "Moon Bell", "pages": [{"page_number": 1, "text": "Hello"}]}

    class _Stories:
        async def get_for_user_or_generic(self, requested_user_id, requested_story_id):
            assert requested_user_id == user_id
            assert requested_story_id == expected_story_id
            return SimpleNamespace(id=expected_story_id, story_type=StoryType.GENERIC)

        async def get_content_by_story_and_language(self, *, story_id, language):
            assert story_id == expected_story_id
            assert language == "en"
            return SimpleNamespace(language="en", story_json=story_json)

    service = StoryService.__new__(StoryService)
    service.stories = _Stories()

    response = await service.get_story_content(user_id=user_id, story_id=expected_story_id, language="EN")

    assert response.story_id == expected_story_id
    assert response.story_type == "generic"
    assert response.language == "en"
    assert response.story_json.title == "Moon Bell"


@pytest.mark.asyncio
async def test_update_story_page_text_updates_story_content():
    user_id = uuid4()
    story_id = uuid4()
    story = SimpleNamespace(id=story_id, story_type=StoryType.GENERIC, updated_at=None)
    content = SimpleNamespace(
        language="en",
        story_json={"title": "Moon Bell", "pages": [{"page_number": 1, "text": "Old"}]},
    )
    calls = {"updated_content": 0, "updated_story": 0}

    class _Stories:
        async def get_for_user_or_generic_for_update(self, requested_user_id, requested_story_id):
            assert requested_user_id == user_id
            assert requested_story_id == story_id
            return story

        async def get_content_by_story_and_language(self, **kwargs):
            assert kwargs["story_id"] == story_id
            assert kwargs["language"] == "en"
            return content

        async def update_content(self, updated_content):
            calls["updated_content"] += 1
            return updated_content

        async def update(self, updated_story):
            calls["updated_story"] += 1
            return updated_story

    service = StoryService.__new__(StoryService)
    service.stories = _Stories()
    payload = GenericStoryPageTextUpdateRequest(pages=[{"page_number": 1, "text": "New text"}])

    response = await service.update_story_page_text(user_id, story_id, payload, language="en")

    assert content.story_json["pages"][0]["text"] == "New text"
    assert calls == {"updated_content": 1, "updated_story": 1}
    assert response.story_json.pages[0].text == "New text"


@pytest.mark.asyncio
async def test_update_story_page_images_updates_all_language_contents(monkeypatch):
    user_id = uuid4()
    story_id = uuid4()
    story = SimpleNamespace(id=story_id, story_type=StoryType.GENERIC, updated_at=None)
    en_content = SimpleNamespace(
        language="en",
        story_json={"pages": [{"page_number": 1, "text": "Hello", "image_url": "old-en"}]},
    )
    hi_content = SimpleNamespace(
        language="hi",
        story_json={"pages": [{"page_number": 1, "text": "Namaste", "image_url": "old-hi"}]},
    )
    calls = {"updated_content": 0, "updated_story": 0}

    class _Stories:
        async def get_for_user_or_generic_for_update(self, requested_user_id, requested_story_id):
            assert requested_user_id == user_id
            assert requested_story_id == story_id
            return story

        async def get_content_by_story_and_language(self, **kwargs):
            assert kwargs["story_id"] == story_id
            assert kwargs["language"] == "en"
            return en_content

        async def list_contents_by_story(self, requested_story_id):
            assert requested_story_id == story_id
            return [en_content, hi_content]

        async def update_content(self, updated_content):
            calls["updated_content"] += 1
            return updated_content

        async def update(self, updated_story):
            calls["updated_story"] += 1
            return updated_story

    async def _save_uploaded_page_image(*args, **kwargs):
        assert kwargs["story_id"] == story_id
        assert kwargs["page_number"] == 1
        return "https://cdn.test/page-1.webp"

    monkeypatch.setattr("app.service.story_service.get_image_storage_service", lambda: object())
    monkeypatch.setattr(GenericStoryService, "_save_uploaded_page_image", _save_uploaded_page_image)
    service = StoryService.__new__(StoryService)
    service.stories = _Stories()

    response = await service.update_story_page_images(
        user_id,
        story_id,
        {"page_image_1": object()},
        language="en",
    )

    assert en_content.story_json["pages"][0]["image_url"] == "https://cdn.test/page-1.webp"
    assert hi_content.story_json["pages"][0]["image_url"] == "https://cdn.test/page-1.webp"
    assert calls == {"updated_content": 2, "updated_story": 1}
    assert response.story_json.pages[0].image_url == "https://cdn.test/page-1.webp"


@pytest.mark.asyncio
async def test_update_story_page_audio_updates_requested_language(monkeypatch):
    user_id = uuid4()
    story_id = uuid4()
    story = SimpleNamespace(id=story_id, story_type=StoryType.GENERIC, updated_at=None)
    content = SimpleNamespace(
        language="en",
        story_json={"pages": [{"page_number": 1, "text": "Hello world"}]},
    )
    calls = {"updated_content": 0, "updated_story": 0}

    class _Stories:
        async def get_for_user_or_generic_for_update(self, requested_user_id, requested_story_id):
            assert requested_user_id == user_id
            assert requested_story_id == story_id
            return story

        async def get_content_by_story_and_language(self, **kwargs):
            assert kwargs["story_id"] == story_id
            assert kwargs["language"] == "en"
            return content

        async def update_content(self, updated_content):
            calls["updated_content"] += 1
            return updated_content

        async def update(self, updated_story):
            calls["updated_story"] += 1
            return updated_story

    class _AudioStorage:
        async def save_story_page_audio(self, **kwargs):
            assert kwargs["story_id"] == story_id
            assert kwargs["language"] == "en"
            assert kwargs["page_number"] == 1
            return "https://cdn.test/page-1.wav"

    async def _read_uploaded_page_audio(upload):
        assert upload is not None
        return b"audio"

    monkeypatch.setattr("app.service.story_service.get_story_audio_storage_service", lambda: _AudioStorage())
    monkeypatch.setattr(GenericStoryService, "_read_uploaded_page_audio", _read_uploaded_page_audio)
    monkeypatch.setattr(GenericStoryService, "_upload_audio_storage_metadata", lambda upload: (".wav", "audio/wav"))
    monkeypatch.setattr(GenericStoryService, "_uploaded_audio_duration_seconds", lambda audio_bytes: 2.0)
    service = StoryService.__new__(StoryService)
    service.stories = _Stories()

    response = await service.update_story_page_audio(
        user_id,
        story_id,
        {"page_audio_1": object()},
        language="en",
    )

    page = content.story_json["pages"][0]
    assert page["audio_url"] == "https://cdn.test/page-1.wav"
    assert page["duration"] == 2.0
    assert page["word_timestamps"]
    assert calls == {"updated_content": 1, "updated_story": 1}
    assert response.story_json.pages[0].audio_url == "https://cdn.test/page-1.wav"
