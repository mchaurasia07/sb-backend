from io import BytesIO

from PIL import Image, ImageDraw

from app.core.config import settings
from app.service.story_service import StoryService
from app.service.story_video_service import StoryVideoService, VideoSlide


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_story_image_aspect_adjustment_pads_without_cropping_edges():
    image = Image.new("RGB", (100, 100), (24, 76, 145))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 9, 99), fill=(255, 0, 0))
    draw.rectangle((90, 0, 99, 99), fill=(0, 255, 0))

    framed = StoryService._crop_image_bytes_to_aspect_ratio(_png_bytes(image), "3:4")

    with Image.open(BytesIO(framed)) as output:
        assert output.width * 4 == output.height * 3
        pixels = output.convert("RGB")
        assert any(
            pixels.getpixel((x, y)) == (255, 0, 0)
            for x in range(pixels.width)
            for y in range(pixels.height)
        )
        assert any(
            pixels.getpixel((x, y)) == (0, 255, 0)
            for x in range(pixels.width)
            for y in range(pixels.height)
        )


def test_video_slide_image_contains_tall_art_without_cropping(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORY_VIDEO_WIDTH", 160)
    monkeypatch.setattr(settings, "STORY_VIDEO_HEIGHT", 90)

    image = Image.new("RGB", (60, 80), (24, 76, 145))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 59, 7), fill=(255, 0, 0))
    draw.rectangle((0, 72, 59, 79), fill=(0, 255, 0))
    output_path = tmp_path / "slide.png"
    slide = VideoSlide(
        kind="page",
        image_url="",
        title=None,
        text=None,
        page_number=1,
        audio_url=None,
        duration_seconds=1.0,
    )

    StoryVideoService.__new__(StoryVideoService)._write_slide_image(output_path, _png_bytes(image), slide)

    with Image.open(output_path) as output:
        assert output.size == (160, 90)
        assert output.convert("RGB").getpixel((80, 0)) == (255, 0, 0)
        assert output.convert("RGB").getpixel((80, 89)) == (0, 255, 0)
