from io import BytesIO

import pytest
from PIL import Image

from app.core.config import settings
from app.service.image_optimizer import optimize_display_image


def _png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (42, 117, 189)).save(output, format="PNG")
    return output.getvalue()


def test_optimize_display_image_caps_size_and_preserves_aspect_ratio(monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_OPTIMIZATION_ENABLED", True)
    monkeypatch.setattr(settings, "IMAGE_MAX_DISPLAY_DIMENSION", 1600)
    monkeypatch.setattr(settings, "IMAGE_PNG_COMPRESS_LEVEL", 9)

    optimized = optimize_display_image(_png_bytes(3000, 2000), "page_1.png")

    with Image.open(BytesIO(optimized)) as image:
        assert image.size == (1600, 1067)
        assert image.width / image.height == pytest.approx(3000 / 2000, rel=0.001)
