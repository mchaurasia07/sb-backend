"""Convert PNG image bytes to WebP format."""

import io
from PIL import Image


class ImageWebPConverter:
    """Converts PNG image bytes to WebP format for optimized storage."""

    @staticmethod
    def convert_to_webp(
        image_bytes: bytes,
        quality: int = 85,
    ) -> bytes:
        """Convert PNG image bytes to WebP format.

        Args:
            image_bytes: PNG image bytes
            quality: WebP quality 1-100 (default: 85)

        Returns:
            WebP image bytes
        """
        image = Image.open(io.BytesIO(image_bytes))

        if image.mode in ("RGBA", "LA", "P"):
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            rgb_image.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
            image = rgb_image

        webp_buffer = io.BytesIO()
        image.save(
            webp_buffer,
            "WEBP",
            quality=quality,
            method=6,
        )
        return webp_buffer.getvalue()
