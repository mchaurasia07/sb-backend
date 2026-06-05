import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import UploadFile, status

from app.core.config import settings
from app.core.exceptions import AppException


class AudioLibraryStorageService:
    """Stores generic audio library files locally or in Cloudflare R2."""

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

        if self._storage_provider() == "r2":
            return await self._save_generic_audio_files_to_r2(
                audio_id=audio_id,
                audio_file=audio_file,
                audio_extension=audio_extension,
                image_file=image_file,
                image_extension=image_extension,
            )

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
        if self._storage_provider() == "r2":
            await self._delete_r2_prefix(self._audio_key("audio_lib", str(audio_id)) + "/")
            await self._delete_r2_prefix(self._image_key("audio_lib", str(audio_id)) + "/")
            return

        directory = settings.audio_root_path / "audio_lib" / str(audio_id)
        if directory.exists():
            await asyncio.to_thread(shutil.rmtree, directory)

    async def _save_generic_audio_files_to_r2(
        self,
        *,
        audio_id: UUID,
        audio_file: UploadFile,
        audio_extension: str,
        image_file: UploadFile,
        image_extension: str,
    ) -> tuple[str, str]:
        audio_content = await self._read_upload(
            audio_file,
            self.max_audio_bytes,
            "Audio file is empty",
            "AUDIO_TOO_LARGE",
        )
        image_content = await self._read_upload(
            image_file,
            self.max_image_bytes,
            "Image file is empty",
            "IMAGE_TOO_LARGE",
        )

        audio_key = self._audio_key("audio_lib", str(audio_id), f"audio{audio_extension}")
        image_key = self._image_key("audio_lib", str(audio_id), f"image{image_extension}")

        await self._put_r2_object(audio_key, audio_content, self._content_type(audio_file, self.audio_content_types))
        await self._put_r2_object(image_key, image_content, self._content_type(image_file, self.image_content_types))
        return self._public_url(audio_key), self._public_url(image_key)

    async def _save_upload(
        self,
        upload: UploadFile,
        file_path: Path,
        max_bytes: int,
        empty_message: str,
        too_large_code: str,
    ) -> None:
        content = await self._read_upload(upload, max_bytes, empty_message, too_large_code)

        try:
            await asyncio.to_thread(file_path.write_bytes, content)
        except OSError as exc:
            raise AppException(
                f"Failed to save uploaded file: {exc}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORAGE_ERROR",
            ) from exc

    @staticmethod
    async def _read_upload(upload: UploadFile, max_bytes: int, empty_message: str, too_large_code: str) -> bytes:
        content = await upload.read()
        if not content:
            raise AppException(empty_message, status.HTTP_400_BAD_REQUEST, "EMPTY_UPLOAD")
        if len(content) > max_bytes:
            raise AppException("Uploaded file is too large", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, too_large_code)
        return content

    @staticmethod
    def _extension(upload: UploadFile, allowed_content_types: dict[str, str], code: str) -> str:
        if upload.content_type in allowed_content_types:
            return allowed_content_types[upload.content_type]

        suffix = Path(upload.filename or "").suffix.lower()
        if suffix and suffix in allowed_content_types.values():
            return suffix

        raise AppException("Unsupported upload file type", status.HTTP_400_BAD_REQUEST, code)

    @staticmethod
    def _content_type(upload: UploadFile, allowed_content_types: dict[str, str]) -> str:
        if upload.content_type in allowed_content_types:
            return upload.content_type or "application/octet-stream"

        suffix = Path(upload.filename or "").suffix.lower()
        for content_type, extension in allowed_content_types.items():
            if extension == suffix:
                return content_type
        return "application/octet-stream"

    @staticmethod
    def _storage_provider() -> str:
        provider = settings.AUDIO_LIBRARY_STORAGE_PROVIDER.strip().lower()
        if provider in {"local", "r2"}:
            return provider

        raise AppException(
            f"Unsupported audio library storage provider: {settings.AUDIO_LIBRARY_STORAGE_PROVIDER}",
            code="AUDIO_LIBRARY_STORAGE_PROVIDER_INVALID",
        )

    @staticmethod
    def _audio_key(*parts: str) -> str:
        key_parts = [
            settings.CLOUDFLARE_R2_AUDIO_KEY_PREFIX.strip("/"),
            *[part.strip("/") for part in parts if part],
        ]
        return "/".join(part for part in key_parts if part)

    @staticmethod
    def _image_key(*parts: str) -> str:
        key_parts = [
            settings.cloudflare_r2_image_key_prefix,
            *[part.strip("/") for part in parts if part],
        ]
        return "/".join(part for part in key_parts if part)

    async def _put_r2_object(self, key: str, body: bytes, content_type: str) -> None:
        self._validate_r2_config()

        def _put() -> None:
            self._r2_client().put_object(
                Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl=settings.CLOUDFLARE_R2_CACHE_CONTROL,
            )

        try:
            await asyncio.to_thread(_put)
        except self._r2_exceptions() as exc:
            raise AppException(
                f"Failed to upload audio library file to Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_AUDIO_LIBRARY_UPLOAD_FAILED",
            ) from exc

    async def _delete_r2_prefix(self, prefix: str) -> None:
        clean_prefix = prefix.lstrip("/")
        if not clean_prefix.strip("/"):
            raise AppException("R2 audio library delete prefix cannot be empty", code="INVALID_R2_PREFIX")

        self._validate_r2_config()

        def _delete_prefix() -> None:
            client = self._r2_client()
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Prefix=clean_prefix):
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if objects:
                    client.delete_objects(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Delete={"Objects": objects})

        try:
            await asyncio.to_thread(_delete_prefix)
        except self._r2_exceptions() as exc:
            raise AppException(
                f"Failed to delete audio library files from Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_AUDIO_LIBRARY_DELETE_FAILED",
            ) from exc

    def _r2_client(self):
        self._validate_r2_config()
        try:
            import boto3
        except ImportError as exc:
            raise AppException(
                "boto3 is required for Cloudflare R2 storage. Install requirements.txt.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "R2_DEPENDENCY_MISSING",
            ) from exc

        return boto3.client(
            "s3",
            endpoint_url=settings.cloudflare_r2_endpoint_url,
            aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
            region_name=settings.CLOUDFLARE_R2_REGION,
        )

    @staticmethod
    def _r2_exceptions():
        try:
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            return (RuntimeError,)
        return (BotoCoreError, ClientError)

    @staticmethod
    def _public_url(key: str) -> str:
        public_base_url = settings.CLOUDFLARE_R2_PUBLIC_BASE_URL.strip()
        if not public_base_url:
            raise AppException(
                "CLOUDFLARE_R2_PUBLIC_BASE_URL must be a public R2 custom domain or r2.dev URL",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "R2_PUBLIC_URL_MISSING",
            )

        host = urlparse(public_base_url).netloc.lower()
        if host.endswith(".r2.cloudflarestorage.com"):
            raise AppException(
                "CLOUDFLARE_R2_PUBLIC_BASE_URL cannot be the private r2.cloudflarestorage.com S3 API endpoint. "
                "Use an R2 custom domain or public r2.dev URL.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "R2_PUBLIC_URL_INVALID",
            )

        return f"{public_base_url.rstrip('/')}/{key.lstrip('/')}"

    @staticmethod
    def _validate_r2_config() -> None:
        missing = [
            name
            for name, value in {
                "CLOUDFLARE_R2_ACCOUNT_ID": settings.CLOUDFLARE_R2_ACCOUNT_ID,
                "CLOUDFLARE_R2_ACCESS_KEY_ID": settings.CLOUDFLARE_R2_ACCESS_KEY_ID,
                "CLOUDFLARE_R2_SECRET_ACCESS_KEY": settings.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
                "CLOUDFLARE_R2_BUCKET_NAME": settings.CLOUDFLARE_R2_BUCKET_NAME,
            }.items()
            if not value
        ]
        if missing:
            raise AppException(
                f"Missing Cloudflare R2 settings: {', '.join(missing)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "R2_CONFIG_MISSING",
            )


audio_library_storage_service = AudioLibraryStorageService()
