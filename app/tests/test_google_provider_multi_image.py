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
