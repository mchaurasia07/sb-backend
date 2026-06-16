"""Batch PNG to WebP conversion with Cloudflare R2 upload and multi-language updates."""

from __future__ import annotations

import io
import logging
from typing import Any
from uuid import UUID

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException
from app.repository.story_repository import StoryRepository
from app.repository.generic_story_repository import GenericStoryRepository
from app.entity.generic_story import GenericStory, GenericStoryContent
from app.entity.story import Story, StoryContent
from app.service.image_storage_provider import get_image_storage_service

logger = logging.getLogger(__name__)


class ImageWebPBatchService:
    """Batch convert PNG images to WebP, upload to R2, delete PNGs, update all languages."""

    SUPPORTED_LANGUAGES = ["en", "hi", "mr"]

    def __init__(self, session: AsyncSession):
        self.session = session
        self.stories = StoryRepository(session)
        self.generic_stories = GenericStoryRepository(session)
        self.r2_service = get_image_storage_service()

    async def convert_stories_to_webp_batch(
        self,
        user_id: UUID | None,
        story_ids: list[UUID],
        quality: int = 85,
    ) -> dict[str, Any]:
        """Convert PNG images to WebP for multiple stories and update all languages.

        Args:
            user_id: User ID (for authorization) - can be None to allow converting any story
            story_ids: List of story IDs to convert (custom or generic stories)
            quality: WebP quality 1-100 (default: 85)

        Returns:
            Batch conversion results with per-story statistics
        """
        results = {
            "total_stories": len(story_ids),
            "successful": 0,
            "failed": 0,
            "results": [],
        }

        for story_id in story_ids:
            try:
                # Try custom story first
                story = await self.stories.get_by_id(story_id)
                is_generic = False

                if story is None:
                    # Try generic story
                    story = await self.generic_stories.get_by_id(story_id)
                    is_generic = True

                if story is None:
                    results["results"].append(
                        {
                            "story_id": story_id,
                            "status": "failed",
                            "error": "Story not found (custom or generic)",
                        }
                    )
                    results["failed"] += 1
                    continue

                # Get English content (required)
                if is_generic:
                    story_content_en = await self.generic_stories.get_content_by_story_and_language(
                        generic_story_id=story_id, language="en"
                    )
                else:
                    story_content_en = await self.stories.get_content_by_story_and_language(
                        story_id=story_id, language="en"
                    )

                if story_content_en is None:
                    results["results"].append(
                        {
                            "story_id": story_id,
                            "status": "failed",
                            "error": "No English content found",
                        }
                    )
                    results["failed"] += 1
                    continue

                # Process story
                story_result = await self._process_story_images(
                    story_id=story_id,
                    story_content_en=story_content_en,
                    quality=quality,
                    is_generic=is_generic,
                )
                results["results"].append(story_result)

                if story_result["status"] == "success":
                    results["successful"] += 1
                else:
                    results["failed"] += 1

            except Exception as exc:
                logger.error("Batch conversion failed for story %s: %s", story_id, str(exc))
                results["results"].append(
                    {
                        "story_id": story_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                results["failed"] += 1

        return results

    async def _process_story_images(
        self,
        story_id: UUID,
        story_content_en: Any,
        quality: int,
        is_generic: bool = False,
    ) -> dict[str, Any]:
        """Process all images for a story."""
        if not isinstance(story_content_en.story_json, dict):
            raise AppException("Story JSON is invalid", code="INVALID_STORY_JSON")

        story_json = story_content_en.story_json
        url_mapping = {}  # {old_url: new_url}
        original_size_mb = 0.0
        converted_size_mb = 0.0
        images_converted = 0
        errors = []

        # Extract all image URLs
        image_urls = []

        # Cover image
        cover_url = story_json.get("cover_image_url")
        if cover_url and not cover_url.endswith(".webp") and not cover_url.startswith("/webp_images/"):
            image_urls.append((cover_url, "cover.webp"))

        # Page images
        pages = story_json.get("pages", [])
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    page_url = page.get("image_url")
                    page_num = page.get("page_number", 0)
                    if page_url and not page_url.endswith(".webp") and not page_url.startswith("/webp_images/"):
                        image_urls.append((page_url, f"page_{page_num}.webp"))

        # Back cover image
        back_cover_url = story_json.get("back_cover_image_url")
        if back_cover_url and not back_cover_url.endswith(".webp") and not back_cover_url.startswith("/webp_images/"):
            image_urls.append((back_cover_url, "back_cover.webp"))

        # Convert each image
        for old_url, webp_filename in image_urls:
            try:
                result = await self._convert_and_upload_image(
                    image_url=old_url,
                    story_id=story_id,
                    filename=webp_filename,
                    quality=quality,
                )
                url_mapping[old_url] = result["webp_url"]
                original_size_mb += result["original_size_mb"]
                converted_size_mb += result["converted_size_mb"]
                images_converted += 1

                # Delete original PNG
                try:
                    await self.r2_service.delete_image(old_url)
                except Exception as e:
                    logger.warning("Failed to delete PNG %s: %s", old_url, str(e))
                    errors.append(f"Failed to delete PNG: {str(e)}")

            except Exception as exc:
                logger.error("Image conversion failed for %s: %s", old_url, str(exc))
                errors.append(f"Failed to convert {webp_filename}: {str(exc)}")

        if images_converted == 0:
            logger.warning("No PNG images found to convert for story %s (already WebP or invalid URLs)", story_id)
            # Still return success if no images need conversion
            pass

        # Update story_json with new URLs
        await self._update_all_language_versions(story_id, url_mapping, is_generic)

        compression_ratio = 0.0
        if original_size_mb > 0:
            compression_ratio = round(1 - (converted_size_mb / original_size_mb), 3)

        return {
            "story_id": story_id,
            "status": "success",
            "images_converted": images_converted,
            "languages_updated": self._get_available_languages(story_id),
            "original_size_mb": round(original_size_mb, 2),
            "converted_size_mb": round(converted_size_mb, 2),
            "compression_ratio": compression_ratio,
            "errors": errors if errors else None,
        }

    async def _convert_and_upload_image(
        self,
        image_url: str,
        story_id: UUID,
        filename: str,
        quality: int,
    ) -> dict[str, Any]:
        """Convert single image PNG → WebP and upload to R2."""
        # Skip if already webp
        if image_url.endswith(".webp"):
            raise AppException(f"Image already converted to WebP: {image_url}", code="ALREADY_WEBP")

        # Extract R2 key from URL (handle both full URLs and paths)
        try:
            # Try to get R2 key from URL
            r2_key = self.r2_service.object_key_from_url(image_url)
        except Exception:
            # If that fails, try using image_url directly as key
            r2_key = image_url.lstrip("/")

        # Download image from R2
        image_bytes = await self.r2_service.get_image_bytes(r2_key)
        if not image_bytes:
            raise AppException(f"Failed to download image from {r2_key}", code="IMAGE_DOWNLOAD_FAILED")

        original_size_bytes = len(image_bytes)

        # Convert to WebP
        image = Image.open(io.BytesIO(image_bytes))

        # Convert RGBA to RGB if necessary
        if image.mode in ("RGBA", "LA", "P"):
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            rgb_image.paste(image, mask=image.split()[-1] if image.mode == "RGBA" else None)
            image = rgb_image

        # Save as WebP to bytes
        webp_buffer = io.BytesIO()
        image.save(
            webp_buffer,
            "WEBP",
            quality=quality,
            method=6,
        )
        webp_bytes = webp_buffer.getvalue()
        converted_size_bytes = len(webp_bytes)

        # Upload WebP to R2
        webp_url = await self.r2_service.save_story_image(
            story_id=story_id,
            image_bytes=webp_bytes,
            filename=filename,
        )

        compression_ratio = round(1 - (converted_size_bytes / original_size_bytes), 3)

        return {
            "webp_url": webp_url,
            "original_size_mb": round(original_size_bytes / (1024 * 1024), 2),
            "converted_size_mb": round(converted_size_bytes / (1024 * 1024), 2),
            "compression_ratio": compression_ratio,
        }

    async def _update_all_language_versions(
        self,
        story_id: UUID,
        url_mapping: dict[str, str],
        is_generic: bool = False,
    ) -> None:
        """Update story_json for all language versions with new WebP URLs."""
        repo = self.generic_stories if is_generic else self.stories

        for language in self.SUPPORTED_LANGUAGES:
            if is_generic:
                content = await repo.get_content_by_story_and_language(
                    generic_story_id=story_id, language=language
                )
            else:
                content = await repo.get_content_by_story_and_language(
                    story_id=story_id, language=language
                )
            if content is None:
                continue

            story_json = content.story_json
            if not isinstance(story_json, dict):
                continue

            # Update cover
            if "cover_image_url" in story_json and story_json["cover_image_url"] in url_mapping:
                story_json["cover_image_url"] = url_mapping[story_json["cover_image_url"]]

            # Update back cover
            if "back_cover_image_url" in story_json and story_json["back_cover_image_url"] in url_mapping:
                story_json["back_cover_image_url"] = url_mapping[story_json["back_cover_image_url"]]

            # Update pages
            pages = story_json.get("pages", [])
            if isinstance(pages, list):
                for page in pages:
                    if isinstance(page, dict) and "image_url" in page and page["image_url"] in url_mapping:
                        page["image_url"] = url_mapping[page["image_url"]]

            # Save
            await repo.update_content(content)
            await self.session.commit()

    def _get_available_languages(self, story_id: UUID) -> list[str]:
        """Get available languages (will be updated when we query all at once)."""
        return self.SUPPORTED_LANGUAGES  # Simplified for now
