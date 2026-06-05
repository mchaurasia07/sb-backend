from io import BytesIO
from pathlib import Path

from fastapi import status
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.exceptions import AppException


def optimize_display_image(image_bytes: bytes, filename: str, max_dimension: int | None = None) -> bytes:
    """Prepare generated images for phone/tablet display while preserving aspect ratio."""
    if not settings.IMAGE_OPTIMIZATION_ENABLED:
        return image_bytes
    if not image_bytes:
        raise AppException("Image is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")

    display_dimension = max_dimension or settings.IMAGE_MAX_DISPLAY_DIMENSION
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(
                (display_dimension, display_dimension),
                Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            suffix = Path(filename).suffix.lower()

            if suffix in {".jpg", ".jpeg"}:
                image = _flatten_alpha(image)
                image.save(
                    output,
                    format="JPEG",
                    quality=settings.IMAGE_JPEG_QUALITY,
                    optimize=True,
                    progressive=True,
                )
            elif suffix == ".webp":
                image.save(
                    output,
                    format="WEBP",
                    quality=settings.IMAGE_WEBP_QUALITY,
                    method=6,
                )
            else:
                image.save(
                    output,
                    format="PNG",
                    optimize=True,
                    compress_level=settings.IMAGE_PNG_COMPRESS_LEVEL,
                )

            optimized = output.getvalue()
            return optimized if optimized else image_bytes
    except (OSError, UnidentifiedImageError) as exc:
        raise AppException("Invalid image bytes", status.HTTP_400_BAD_REQUEST, "INVALID_IMAGE") from exc


def _flatten_alpha(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (255, 255, 255))
        alpha = image.convert("RGBA").getchannel("A")
        background.paste(image.convert("RGB"), mask=alpha)
        return background
    return image.convert("RGB")
