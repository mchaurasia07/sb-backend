import asyncio
from pathlib import Path
import shutil
from uuid import UUID
from urllib.parse import unquote, urlparse

from fastapi import UploadFile, status

from app.core.config import settings
from app.core.exceptions import AppException


class ImageStorageService:
    """Image storage boundary.

    This implementation writes to local disk. Replace this class with cloud
    storage later without changing child profile business logic.
    """

    allowed_content_types = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    async def save_child_profile_photo(
        self,
        parent_id: UUID,
        child_id: UUID,
        photo: UploadFile,
        public_base_url: str,
    ) -> str:
        extension = self._get_extension(photo)
        directory = settings.media_root_path / str(parent_id) / str(child_id)
        directory.mkdir(parents=True, exist_ok=True)

        file_path = directory / f"profile{extension}"
        content = await photo.read()
        if not content:
            raise AppException("Photo file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_PHOTO")
        if len(content) > settings.IMAGE_MAX_UPLOAD_BYTES:
            raise AppException(
                "Photo must be 5 MB or smaller",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "PHOTO_TOO_LARGE",
            )

        await asyncio.to_thread(file_path.write_bytes, content)
        public_path = f"{settings.MEDIA_URL_PREFIX}/{parent_id}/{child_id}/profile{extension}"
        return f"{public_base_url}{public_path}"

    async def save_character_image(
        self,
        parent_id: UUID,
        child_id: UUID,
        image_bytes: bytes,
        public_base_url: str,
    ) -> str:
        """Save AI-generated character image.

        Args:
            parent_id: Parent user ID
            child_id: Child profile ID
            image_bytes: Image data as bytes
            public_base_url: Base URL for constructing public URLs

        Returns:
            Public URL of saved character image

        Raises:
            AppException: If save operation fails
        """
        directory = settings.media_root_path / str(parent_id) / str(child_id)
        directory.mkdir(parents=True, exist_ok=True)

        file_path = directory / "character.png"

        try:
            await asyncio.to_thread(file_path.write_bytes, image_bytes)
        except IOError as e:
            raise AppException(
                f"Failed to save character image: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORAGE_ERROR",
            )

        public_path = f"{settings.MEDIA_URL_PREFIX}/{parent_id}/{child_id}/character.png"
        return f"{public_base_url}{public_path}"

    async def save_story_image(
        self,
        story_id: UUID,
        image_bytes: bytes,
        filename: str,
        public_base_url: str,
    ) -> str:
        """Save story-generated image (cover/page/back_cover).

        Args:
            story_id: Story UUID
            image_bytes: Image data as bytes
            filename: Filename (e.g., 'cover.png', 'page_1.png', 'back_cover.png')
            public_base_url: Base URL for constructing public URLs

        Returns:
            Public URL of saved image

        Raises:
            AppException: If save operation fails
        """
        directory = settings.media_root_path / "stories" / str(story_id)
        directory.mkdir(parents=True, exist_ok=True)

        file_path = directory / filename

        try:
            await asyncio.to_thread(file_path.write_bytes, image_bytes)
        except IOError as e:
            raise AppException(
                f"Failed to save story image: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORAGE_ERROR",
            )

        public_path = f"{settings.MEDIA_URL_PREFIX}/stories/{story_id}/{filename}"
        return f"{public_base_url}{public_path}"

    async def delete_child_profile_directory(self, parent_id: UUID, child_id: UUID) -> None:
        await self._delete_directory(settings.media_root_path / str(parent_id) / str(child_id))

    async def delete_story_directory(self, story_id: UUID) -> None:
        await self._delete_directory(settings.media_root_path / "stories" / str(story_id))

    async def get_image_bytes(self, url_or_path: str) -> bytes:
        file_path = self._resolve_media_path(url_or_path)
        try:
            content = await asyncio.to_thread(file_path.read_bytes)
        except OSError as exc:
            raise AppException(f"Failed to read image file: {file_path}", code="IMAGE_READ_FAILED") from exc
        if not content:
            raise AppException(f"Image file is empty: {file_path}", code="EMPTY_IMAGE")
        return content

    def _get_extension(self, photo: UploadFile) -> str:
        if photo.content_type in self.allowed_content_types:
            return self.allowed_content_types[photo.content_type]
        raise AppException(
            "Photo must be a JPEG, PNG, or WEBP image",
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_IMAGE_TYPE",
        )

    @staticmethod
    def _resolve_media_path(url_or_path: str) -> Path:
        raw_value = str(url_or_path)
        parsed = urlparse(raw_value)
        media_prefix = settings.MEDIA_URL_PREFIX.rstrip("/") + "/"

        if parsed.scheme in {"http", "https"}:
            url_path = unquote(parsed.path)
            if not url_path.startswith(media_prefix):
                raise AppException("Image URL must point to app media storage", code="INVALID_IMAGE_URL")
            relative_path = url_path[len(media_prefix) :]
            file_path = settings.media_root_path / relative_path
        elif raw_value.startswith(media_prefix):
            relative_path = raw_value[len(media_prefix) :]
            file_path = settings.media_root_path / relative_path
        else:
            file_path = Path(raw_value)

        file_path = file_path.resolve()
        try:
            file_path.relative_to(settings.media_root_path)
        except ValueError:
            raise AppException("Image file must be in media directory", code="INVALID_IMAGE_PATH")

        if not file_path.exists() and file_path.name == "child_character.png":
            legacy_path = file_path.with_name("character.png")
            if legacy_path.exists():
                file_path = legacy_path

        if not file_path.exists() and file_path.name == "character.png":
            legacy_path = file_path.with_name("child_character.png")
            if legacy_path.exists():
                file_path = legacy_path

        if not file_path.exists():
            raise AppException(f"Image file not found: {file_path}", code="FILE_NOT_FOUND")

        return file_path

    @staticmethod
    async def _delete_directory(path: Path) -> None:
        directory = path.resolve()
        try:
            directory.relative_to(settings.media_root_path)
        except ValueError as exc:
            raise AppException("Image directory must be in media directory", code="INVALID_IMAGE_PATH") from exc

        if not directory.exists():
            return
        if not directory.is_dir():
            raise AppException("Image delete target must be a directory", code="INVALID_IMAGE_PATH")

        try:
            await asyncio.to_thread(shutil.rmtree, directory)
        except OSError as exc:
            raise AppException(f"Failed to delete image directory: {directory}", code="IMAGE_DELETE_FAILED") from exc


image_storage_service = ImageStorageService()
