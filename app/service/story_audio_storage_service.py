import asyncio
import shutil
from urllib.parse import urlparse
from uuid import UUID

from fastapi import status

from app.core.config import settings
from app.core.exceptions import AppException


class LocalStoryAudioStorageService:
    async def save_story_page_audio(
        self,
        *,
        story_id: UUID,
        language: str,
        page_number: int,
        audio_bytes: bytes,
        extension: str = ".wav",
        content_type: str = "audio/wav",
    ) -> str:
        _ = content_type
        extension = self._safe_audio_extension(extension)
        story_dir = settings.audio_root_path / "stories" / str(story_id) / language
        story_dir.mkdir(parents=True, exist_ok=True)

        file_path = story_dir / f"page_{page_number}{extension}"
        try:
            await asyncio.to_thread(file_path.write_bytes, audio_bytes)
        except OSError as exc:
            raise AppException(
                f"Failed to save audio file: {exc}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "AUDIO_STORAGE_ERROR",
            ) from exc

        return f"{settings.AUDIO_URL_PREFIX}/stories/{story_id}/{language}/page_{page_number}{extension}"

    @staticmethod
    def _safe_audio_extension(extension: str) -> str:
        suffix = (extension or "").strip().lower()
        if not suffix:
            return ".wav"
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        safe = "".join(character for character in suffix if character.isalnum() or character == ".")
        return safe if safe and safe != "." else ".wav"

    async def delete_story_directory(self, story_id: UUID) -> None:
        directory = (settings.audio_root_path / "stories" / str(story_id)).resolve()
        try:
            directory.relative_to(settings.audio_root_path)
        except ValueError as exc:
            raise AppException("Audio directory must be in audio directory", code="INVALID_AUDIO_PATH") from exc

        if not directory.exists():
            return
        if not directory.is_dir():
            raise AppException("Audio delete target must be a directory", code="INVALID_AUDIO_PATH")

        try:
            await asyncio.to_thread(shutil.rmtree, directory)
        except OSError as exc:
            raise AppException(f"Failed to delete audio directory: {directory}", code="AUDIO_DELETE_FAILED") from exc


class CloudflareR2StoryAudioStorageService:
    async def save_story_page_audio(
        self,
        *,
        story_id: UUID,
        language: str,
        page_number: int,
        audio_bytes: bytes,
        extension: str = ".wav",
        content_type: str = "audio/wav",
    ) -> str:
        if not audio_bytes:
            raise AppException("Audio file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_AUDIO")

        extension = LocalStoryAudioStorageService._safe_audio_extension(extension)
        key = self._key("stories", str(story_id), language, f"page_{page_number}{extension}")
        await self._put_object(key, audio_bytes, content_type or "application/octet-stream")
        return self.public_url(key)

    async def delete_story_directory(self, story_id: UUID) -> None:
        await self._delete_prefix(self._key("stories", str(story_id)) + "/")

    def public_url(self, key: str) -> str:
        clean_key = key.lstrip("/")
        return f"{self._public_base_url().rstrip('/')}/{clean_key}"

    def _key(self, *parts: str) -> str:
        key_parts = [settings.CLOUDFLARE_R2_AUDIO_KEY_PREFIX.strip("/"), *[part.strip("/") for part in parts if part]]
        return "/".join(part for part in key_parts if part)

    async def _put_object(self, key: str, body: bytes, content_type: str) -> None:
        self._validate_config()

        def _put() -> None:
            self._client().put_object(
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
                f"Failed to upload audio to Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_AUDIO_UPLOAD_FAILED",
            ) from exc

    async def _delete_prefix(self, prefix: str) -> None:
        clean_prefix = prefix.lstrip("/")
        if not clean_prefix.strip("/"):
            raise AppException("R2 audio delete prefix cannot be empty", code="INVALID_R2_AUDIO_PREFIX")

        self._validate_config()

        def _delete_prefix() -> None:
            client = self._client()
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Prefix=clean_prefix):
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if objects:
                    client.delete_objects(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Delete={"Objects": objects})

        try:
            await asyncio.to_thread(_delete_prefix)
        except self._r2_exceptions() as exc:
            raise AppException(
                f"Failed to delete audio from Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_AUDIO_DELETE_FAILED",
            ) from exc

    def _client(self):
        self._validate_config()
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
    def _public_base_url() -> str:
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
        return public_base_url

    @staticmethod
    def _r2_exceptions():
        try:
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            return (RuntimeError,)
        return (BotoCoreError, ClientError)

    @staticmethod
    def _validate_config() -> None:
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


local_story_audio_storage_service = LocalStoryAudioStorageService()
cloudflare_r2_story_audio_storage_service = CloudflareR2StoryAudioStorageService()
