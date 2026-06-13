import asyncio
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import status

from app.core.config import settings
from app.core.exceptions import AppException


@dataclass(frozen=True, slots=True)
class StoryVideoStorageResult:
    video_url: str | None
    local_video_path: str | None = None


class LocalStoryVideoStorageService:
    async def save_story_video(
        self,
        *,
        story_id: UUID,
        language: str,
        video_bytes: bytes,
        filename: str = "story.mp4",
        content_type: str = "video/mp4",
    ) -> StoryVideoStorageResult:
        _ = content_type
        if not video_bytes:
            raise AppException("Video file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_VIDEO")

        clean_language = language.strip().lower()
        clean_filename = self._safe_filename(filename)
        story_dir = (settings.media_root_path / "video" / "stories" / str(story_id) / clean_language).resolve()
        try:
            story_dir.relative_to(settings.media_root_path)
        except ValueError as exc:
            raise AppException("Video directory must be in media directory", code="INVALID_VIDEO_PATH") from exc

        story_dir.mkdir(parents=True, exist_ok=True)
        file_path = story_dir / clean_filename
        try:
            await asyncio.to_thread(file_path.write_bytes, video_bytes)
        except OSError as exc:
            raise AppException(
                f"Failed to save video file: {exc}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "VIDEO_STORAGE_ERROR",
            ) from exc

        video_url = f"{settings.MEDIA_URL_PREFIX}/video/stories/{story_id}/{clean_language}/{clean_filename}"
        return StoryVideoStorageResult(video_url=video_url, local_video_path=str(file_path))

    @staticmethod
    def _safe_filename(filename: str) -> str:
        suffix = Path(filename or "story.mp4").name.strip()
        if not suffix or suffix in {".", ".."}:
            return "story.mp4"
        safe = "".join(character for character in suffix if character.isalnum() or character in {".", "_", "-"})
        return safe if safe not in {"", ".", ".."} else "story.mp4"


class CloudflareR2StoryVideoStorageService:
    async def save_story_video(
        self,
        *,
        story_id: UUID,
        language: str,
        video_bytes: bytes,
        filename: str = "story.mp4",
        content_type: str = "video/mp4",
    ) -> StoryVideoStorageResult:
        if not video_bytes:
            raise AppException("Video file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_VIDEO")

        clean_language = language.strip().lower()
        clean_filename = filename.strip().replace("\\", "/").split("/")[-1] or "story.mp4"
        key = self._key("stories", str(story_id), clean_language, clean_filename)
        await self._put_object(key, video_bytes, content_type)
        return StoryVideoStorageResult(video_url=self.public_url(key))

    def public_url(self, key: str) -> str:
        clean_key = key.lstrip("/")
        return f"{self._public_base_url().rstrip('/')}/{clean_key}"

    def _key(self, *parts: str) -> str:
        key_parts = [
            settings.CLOUDFLARE_R2_VIDEO_KEY_PREFIX.strip("/"),
            *[part.strip("/") for part in parts if part],
        ]
        return "/".join(part for part in key_parts if part)

    async def _put_object(self, key: str, body: bytes, content_type: str) -> None:
        self._validate_config()

        def _put() -> None:
            self._client().put_object(
                Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
                Key=key,
                Body=body,
                ContentType=content_type or "application/octet-stream",
                CacheControl=settings.CLOUDFLARE_R2_CACHE_CONTROL,
            )

        try:
            await asyncio.to_thread(_put)
        except self._r2_exceptions() as exc:
            raise AppException(
                f"Failed to upload video to Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_VIDEO_UPLOAD_FAILED",
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
        if settings.VIDEO_STORAGE_PROVIDER.strip().lower() != "r2":
            raise AppException(
                f"Unsupported video storage provider: {settings.VIDEO_STORAGE_PROVIDER}",
                code="VIDEO_STORAGE_PROVIDER_INVALID",
            )

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


local_story_video_storage_service = LocalStoryVideoStorageService()
cloudflare_r2_story_video_storage_service = CloudflareR2StoryVideoStorageService()
