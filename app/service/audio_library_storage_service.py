import asyncio
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile, status

from app.core.config import settings
from app.core.exceptions import AppException


class AudioLibraryStorageService:
    """Stores generic audio library files under the public audio mount."""

    max_audio_bytes = 50 * 1024 * 1024
    max_image_bytes = 5 * 1024 * 1024

    image_content_types = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    audio_content_types = {
        "audio/aac": ".aac",
        "audio/m4a": ".m4a",
        "audio/mp3": ".mp3",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/wave": ".wav",
        "audio/webm": ".webm",
        "audio/x-m4a": ".m4a",
        "audio/x-wav": ".wav",
    }

    async def save_generic_audio_files(
        self,
        *,
        audio_id: UUID,
        audio_file: UploadFile,
        image_file: UploadFile,
        public_base_url: str,
    ) -> tuple[str, str]:
        audio_extension = self._extension(audio_file, self.audio_content_types, "UNSUPPORTED_AUDIO_TYPE")
        image_extension = self._extension(image_file, self.image_content_types, "UNSUPPORTED_IMAGE_TYPE")

        directory = settings.audio_root_path / "audio_lib" / str(audio_id)
        directory.mkdir(parents=True, exist_ok=True)

        audio_path = directory / f"audio{audio_extension}"
        image_path = directory / f"image{image_extension}"

        await self._save_upload(audio_file, audio_path, self.max_audio_bytes, "Audio file is empty", "AUDIO_TOO_LARGE")
        await self._save_upload(image_file, image_path, self.max_image_bytes, "Image file is empty", "IMAGE_TOO_LARGE")

        audio_url = f"{public_base_url}{settings.AUDIO_URL_PREFIX}/audio_lib/{audio_id}/audio{audio_extension}"
        image_url = f"{public_base_url}{settings.AUDIO_URL_PREFIX}/audio_lib/{audio_id}/image{image_extension}"
        return audio_url, image_url

    async def delete_generic_audio_directory(self, audio_id: UUID) -> None:
        directory = settings.audio_root_path / "audio_lib" / str(audio_id)
        if directory.exists():
            await asyncio.to_thread(shutil.rmtree, directory)

    async def _save_upload(
        self,
        upload: UploadFile,
        file_path: Path,
        max_bytes: int,
        empty_message: str,
        too_large_code: str,
    ) -> None:
        content = await upload.read()
        if not content:
            raise AppException(empty_message, status.HTTP_400_BAD_REQUEST, "EMPTY_UPLOAD")
        if len(content) > max_bytes:
            raise AppException("Uploaded file is too large", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, too_large_code)

        try:
            await asyncio.to_thread(file_path.write_bytes, content)
        except OSError as exc:
            raise AppException(
                f"Failed to save uploaded file: {exc}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORAGE_ERROR",
            ) from exc

    @staticmethod
    def _extension(upload: UploadFile, allowed_content_types: dict[str, str], code: str) -> str:
        if upload.content_type in allowed_content_types:
            return allowed_content_types[upload.content_type]

        suffix = Path(upload.filename or "").suffix.lower()
        if suffix and suffix in allowed_content_types.values():
            return suffix

        raise AppException("Unsupported upload file type", status.HTTP_400_BAD_REQUEST, code)


audio_library_storage_service = AudioLibraryStorageService()
