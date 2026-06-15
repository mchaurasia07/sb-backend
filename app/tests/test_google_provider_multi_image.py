from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.service.ai.google_provider import DEFAULT_GEMINI_IMAGE_MODEL, GoogleProvider


def _text_part(text: str):
    return SimpleNamespace(text=text, inline_data=None)


def _image_part(data: bytes, mime_type: str = "image/png"):
    return SimpleNamespace(
        text=None,
        inline_data=SimpleNamespace(data=data, mime_type=mime_type),
    )


def test_extract_images_from_content_response_returns_all_inline_images_in_order():
    response = SimpleNamespace(
        parts=[
            _text_part("IMAGE_ITEM: page_1"),
            _image_part(b"one"),
            _text_part("IMAGE_ITEM: page_2"),
            _image_part(b"two"),
        ]
    )

    images, response_text = GoogleProvider._extract_images_from_content_response(response)

    assert [image.image_bytes for image in images] == [b"one", b"two"]
    assert images[0].mime_type == "image/png"
    assert images[0].preceding_text == "IMAGE_ITEM: page_1"
    assert images[1].preceding_text == "IMAGE_ITEM: page_2"
    assert response_text == "IMAGE_ITEM: page_1\nIMAGE_ITEM: page_2"


def test_story_reference_inputs_prefers_named_multi_reference_list():
    references = GoogleProvider._story_reference_inputs(
        "legacy-base64",
        {
            "reference_images_base64": [
                {
                    "character_id": "hero_child",
                    "name": "Mira",
                    "role": "hero_child",
                    "image_base64": "hero-base64",
                    "image_url": "/media/mira.png",
                },
                {
                    "character_id": "uncle_raj",
                    "name": "Uncle Raj",
                    "role": "mentor",
                    "image_base64": "side-base64",
                },
            ]
        },
    )

    assert [reference["character_id"] for reference in references] == ["hero_child", "uncle_raj"]
    assert [reference["image_base64"] for reference in references] == ["hero-base64", "side-base64"]


@pytest.mark.asyncio
async def test_generate_interleaved_images_raises_on_exact_count_mismatch():
    class FakeModels:
        async def generate_content(self, **kwargs):
            return SimpleNamespace(
                parts=[
                    _text_part("IMAGE_ITEM: page_1"),
                    _image_part(b"one"),
                ],
                usage_metadata=None,
            )

    provider = GoogleProvider.__new__(GoogleProvider)
    provider.reference_image_model = DEFAULT_GEMINI_IMAGE_MODEL
    provider.client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))

    with pytest.raises(AppException) as exc_info:
        await provider.generate_interleaved_images(
            "prompt",
            expected_count=2,
            aspect_ratio="1:1",
            model=DEFAULT_GEMINI_IMAGE_MODEL,
        )

    assert exc_info.value.code == "GOOGLE_MULTI_IMAGE_COUNT_MISMATCH"
    assert exc_info.value.details["expected_count"] == 2
    assert exc_info.value.details["received_count"] == 1


@pytest.mark.asyncio
async def test_generate_text_retries_transient_google_unavailable(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("503 UNAVAILABLE. The service is currently unavailable.")
            return SimpleNamespace(text='{"ok":true}', candidates=[], prompt_feedback=None, usage_metadata=None)

    fake_models = FakeModels()
    provider = GoogleProvider.__new__(GoogleProvider)
    provider.text_model = "gemini-2.5-flash"
    provider.client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr("app.service.ai.google_provider.settings.STORY_MOCK_LLM_RESPONSES", False)

    result = await provider.generate_text(
        "prompt",
        transient_error_retries=2,
        transient_error_retry_base_delay_seconds=0,
    )

    assert result.text == '{"ok":true}'
    assert fake_models.calls == 3


@pytest.mark.asyncio
async def test_generate_text_uses_zero_configured_transient_retries(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.calls = 0

        async def generate_content(self, **kwargs):
            _ = kwargs
            self.calls += 1
            raise RuntimeError("503 UNAVAILABLE. The service is currently unavailable.")

    fake_models = FakeModels()
    provider = GoogleProvider.__new__(GoogleProvider)
    provider.text_model = "gemini-2.5-flash"
    provider.client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    monkeypatch.setattr("app.service.ai.google_provider.settings.STORY_MOCK_LLM_RESPONSES", False)
    monkeypatch.setattr("app.service.ai.google_provider.settings.GOOGLE_TEXT_TRANSIENT_RETRIES", 0)

    with pytest.raises(AppException) as exc_info:
        await provider.generate_text("prompt")

    assert exc_info.value.code == "GOOGLE_ERROR"
    assert fake_models.calls == 1
