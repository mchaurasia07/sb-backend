import asyncio
from pathlib import Path
from uuid import UUID

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
        directory = Path(settings.MEDIA_ROOT) / str(parent_id) / str(child_id)
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
        directory = Path(settings.MEDIA_ROOT) / str(parent_id) / str(child_id)
        directory.mkdir(parents=True, exist_ok=True)

        file_path = directory / "child_character.png"

        try:
            await asyncio.to_thread(file_path.write_bytes, image_bytes)
        except IOError as e:
            raise AppException(
                f"Failed to save character image: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORAGE_ERROR",
            )

        public_path = f"{settings.MEDIA_URL_PREFIX}/{parent_id}/{child_id}/child_character.png"
        return f"{public_base_url}{public_path}"

    def _get_extension(self, photo: UploadFile) -> str:
        if photo.content_type in self.allowed_content_types:
            return self.allowed_content_types[photo.content_type]
        raise AppException(
            "Photo must be a JPEG, PNG, or WEBP image",
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_IMAGE_TYPE",
        )


image_storage_service = ImageStorageService()
