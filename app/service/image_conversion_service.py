"""Service for converting PNG images to WebP format and reducing file size."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.entity.story import Story, StoryContent
from app.repository.story_repository import StoryRepository

logger = logging.getLogger(__name__)


class ImageConversionService:
    """Converts PNG images to WebP format for better compression."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.stories = StoryRepository(session)
        self.webp_output_dir = settings.media_root_path / "webp_images"
        self.webp_output_dir.mkdir(parents=True, exist_ok=True)

    async def convert_story_images_to_webp(
        self,
        user_id: UUID,
        story_id: UUID,
        language: str = "en",
        quality: int = 85,
    ) -> dict[str, Any]:
        """Convert all PNG images in a story to WebP format.

        Args:
            user_id: User ID (for authorization)
            story_id: Story ID to convert
            language: Language code (default: "en")
            quality: WebP quality (1-100, default: 85)

        Returns:
            Conversion result with statistics
        """
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise AppException("Story not found", code="STORY_NOT_FOUND")

        story_content = await self.stories.get_content_by_story_and_language(story_id=story_id, language=language)
        if story_content is None:
            raise AppException(f"Story content not found for language {language}", code="INVALID_STORY_CONTENT")

        if not isinstance(story_content.story_json, dict):
            raise AppException("Story JSON is invalid or missing", code="INVALID_STORY_JSON")

        story_json = story_content.story_json
        pages = story_json.get("pages", [])

        if not isinstance(pages, list):
            raise AppException("Story pages are invalid", code="INVALID_PAGES")

        conversion_results = {
            "story_id": story_id,
            "language": language,
            "total_pages": len(pages),
            "converted_pages": 0,
            "failed_pages": 0,
            "original_size_mb": 0,
            "converted_size_mb": 0,
            "compression_ratio": 0.0,
            "conversions": [],
            "errors": [],
        }

        # Convert each page's image
        for page in pages:
            if not isinstance(page, dict):
                continue

            image_url = page.get("image_url")
            if not image_url:
                continue

            page_number = page.get("page_number", 0)

            try:
                result = await self._convert_single_image(
                    image_url=image_url,
                    page_number=page_number,
                    story_id=story_id,
                    quality=quality,
                )
                conversion_results["conversions"].append(result)
                conversion_results["converted_pages"] += 1
                conversion_results["original_size_mb"] += result["original_size_mb"]
                conversion_results["converted_size_mb"] += result["converted_size_mb"]

                # Update page URL to WebP
                page["image_url"] = result["webp_url"]

            except Exception as exc:
                error_msg = f"Page {page_number}: {str(exc)}"
                conversion_results["errors"].append(error_msg)
                conversion_results["failed_pages"] += 1
                logger.error("Image conversion failed: %s", error_msg)

        if conversion_results["original_size_mb"] > 0:
            conversion_results["compression_ratio"] = round(
                1 - (conversion_results["converted_size_mb"] / conversion_results["original_size_mb"]),
                3,
            )

        # Update story content with new WebP URLs
        if conversion_results["converted_pages"] > 0:
            story_content.story_json = story_json
            await self.stories.update_content(story_content)
            await self.session.commit()
            logger.info(
                "Story %s: Converted %d pages to WebP, compression ratio: %.1f%%",
                story_id,
                conversion_results["converted_pages"],
                conversion_results["compression_ratio"] * 100,
            )

        return conversion_results

    async def _convert_single_image(
        self,
        image_url: str,
        page_number: int,
        story_id: UUID,
        quality: int = 85,
    ) -> dict[str, Any]:
        """Convert a single PNG image to WebP.

        Args:
            image_url: URL of the PNG image
            page_number: Page number for naming
            story_id: Story ID for organization
            quality: WebP quality (1-100)

        Returns:
            Conversion result with size metrics and new URL
        """
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(image_url)
            response.raise_for_status()

        image_data = response.content
        original_size_bytes = len(image_data)

        # Open image with PIL
        image = Image.open(io.BytesIO(image_data))

        # Convert RGBA to RGB if necessary (WebP quality can be better with RGB)
        if image.mode in ("RGBA", "LA", "P"):
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            rgb_image.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
            image = rgb_image

        # Generate output filename
        webp_filename = f"story_{story_id}_page_{page_number}.webp"
        webp_path = self.webp_output_dir / webp_filename

        # Save as WebP with optimized quality
        image.save(
            webp_path,
            "WEBP",
            quality=quality,
            method=6,  # Slowest but best compression
        )

        converted_size_bytes = os.path.getsize(webp_path)
        compression_ratio = round(
            1 - (converted_size_bytes / original_size_bytes),
            3,
        )

        return {
            "page_number": page_number,
            "original_url": image_url,
            "original_size_mb": round(original_size_bytes / (1024 * 1024), 2),
            "converted_size_mb": round(converted_size_bytes / (1024 * 1024), 2),
            "compression_ratio": compression_ratio,
            "webp_url": f"/webp_images/{webp_filename}",
            "local_path": str(webp_path),
        }

    async def get_conversion_stats(
        self,
        user_id: UUID,
        story_id: UUID,
        language: str = "en",
    ) -> dict[str, Any]:
        """Get WebP conversion statistics for a story.

        Returns info about converted images.
        """
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise AppException("Story not found", code="STORY_NOT_FOUND")

        story_content = await self.stories.get_content_by_story_and_language(story_id=story_id, language=language)
        if story_content is None or not isinstance(story_content.story_json, dict):
            return {"story_id": story_id, "language": language, "pages": 0, "webp_images": 0}

        pages = story_content.story_json.get("pages", [])
        webp_count = sum(1 for p in pages if isinstance(p, dict) and p.get("image_url", "").endswith(".webp"))

        return {
            "story_id": story_id,
            "language": language,
            "total_pages": len(pages),
            "webp_images": webp_count,
            "still_png": len(pages) - webp_count,
        }
