import asyncio
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import UploadFile, status

from app.core.config import settings
from app.core.exceptions import AppException


class CloudflareR2ImageStorageService:
    """Cloudflare R2 image storage using the S3-compatible API.

    This service intentionally mirrors ImageStorageService's public methods so
    callers can switch providers without changing image folder semantics.
    """

    allowed_content_types = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    async def save_child_profile_photo(
        self,
        parent_id: UUID,
        child_id: UUID,
        photo: UploadFile,
        public_base_url: str = "",
    ) -> str:
        extension = self._get_extension(photo)
        content = await photo.read()
        self._validate_upload(content, "Photo file is empty", "PHOTO_TOO_LARGE")

        key = self._key(str(parent_id), str(child_id), f"profile{extension}")
        await self._put_object(key, content, photo.content_type or "application/octet-stream")
        return self.public_url(key)

    async def save_child_profile_photo_bytes(
        self,
        *,
        parent_id: UUID,
        child_id: UUID,
        image_bytes: bytes,
        extension: str,
    ) -> str:
        normalized_extension = extension.lower()
        if not normalized_extension.startswith("."):
            normalized_extension = f".{normalized_extension}"
        if normalized_extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise AppException("Profile photo must be a JPEG, PNG, or WEBP image", code="UNSUPPORTED_IMAGE_TYPE")

        self._validate_upload(image_bytes, "Photo file is empty", "PHOTO_TOO_LARGE")
        key = self._key(str(parent_id), str(child_id), f"profile{normalized_extension}")
        await self._put_object(key, image_bytes, self._content_type_for_filename(key))
        return self.public_url(key)

    async def save_character_image(
        self,
        parent_id: UUID,
        child_id: UUID,
        image_bytes: bytes,
        public_base_url: str = "",
    ) -> str:
        if not image_bytes:
            raise AppException("Character image is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")

        key = self._key(str(parent_id), str(child_id), "character.png")
        await self._put_object(key, image_bytes, "image/png")
        return self.public_url(key)

    async def save_story_image(
        self,
        story_id: UUID,
        image_bytes: bytes,
        filename: str,
        public_base_url: str = "",
    ) -> str:
        if not image_bytes:
            raise AppException("Story image is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")

        clean_filename = Path(filename).name
        if clean_filename != filename:
            raise AppException("Image filename must not contain a path", code="INVALID_IMAGE_FILENAME")

        key = self._key("stories", str(story_id), clean_filename)
        await self._put_object(key, image_bytes, self._content_type_for_filename(clean_filename))
        return self.public_url(key)

    async def get_image_bytes(self, key_or_url: str) -> bytes:
        key = self.object_key_from_url(key_or_url)

        def _get() -> bytes:
            response = self._client().get_object(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Key=key)
            return response["Body"].read()

        try:
            return await asyncio.to_thread(_get)
        except self._r2_exceptions() as exc:
            raise AppException(
                f"Failed to read image from Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_READ_FAILED",
            ) from exc

    async def delete_image(self, key_or_url: str) -> None:
        key = self.object_key_from_url(key_or_url)

        def _delete() -> None:
            self._client().delete_object(Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME, Key=key)

        try:
            await asyncio.to_thread(_delete)
        except self._r2_exceptions() as exc:
            raise AppException(
                f"Failed to delete image from Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_DELETE_FAILED",
            ) from exc

    async def delete_prefix(self, prefix: str) -> None:
        clean_prefix = prefix.strip("/")
        if not clean_prefix:
            raise AppException("R2 delete prefix cannot be empty", code="INVALID_R2_PREFIX")

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
                f"Failed to delete images from Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_DELETE_FAILED",
            ) from exc

    def public_url(self, key: str) -> str:
        clean_key = key.lstrip("/")
        return f"{self._public_base_url().rstrip('/')}/{clean_key}"

    def object_key_from_url(self, key_or_url: str) -> str:
        value = str(key_or_url).strip()
        if not value:
            raise AppException("R2 object key cannot be empty", code="INVALID_R2_KEY")

        if value.startswith("http://") or value.startswith("https://"):
            for base_url in self._known_public_base_urls():
                prefix = base_url.rstrip("/") + "/"
                if value.startswith(prefix):
                    return value[len(prefix) :].lstrip("/")
            raise AppException("Image URL does not match configured R2 public base URL", code="INVALID_R2_URL")

        return value.lstrip("/")

    def _key(self, *parts: str) -> str:
        key_parts = [settings.cloudflare_r2_image_key_prefix, *[part.strip("/") for part in parts if part]]
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
                f"Failed to upload image to Cloudflare R2: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "R2_UPLOAD_FAILED",
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
    def _r2_exceptions():
        try:
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            return (RuntimeError,)
        return (BotoCoreError, ClientError)

    def _validate_upload(self, content: bytes, empty_message: str, too_large_code: str) -> None:
        if not content:
            raise AppException(empty_message, status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")
        if len(content) > settings.IMAGE_MAX_UPLOAD_BYTES:
            raise AppException(
                "Image must be 5 MB or smaller",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                too_large_code,
            )

    def _get_extension(self, photo: UploadFile) -> str:
        if photo.content_type in self.allowed_content_types:
            return self.allowed_content_types[photo.content_type]

        suffix = Path(photo.filename or "").suffix.lower()
        if suffix in self.allowed_content_types.values():
            return suffix

        raise AppException(
            "Photo must be a JPEG, PNG, or WEBP image",
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_IMAGE_TYPE",
        )

    @staticmethod
    def _content_type_for_filename(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")

    @staticmethod
    def _known_public_base_urls() -> list[str]:
        urls = []
        if settings.CLOUDFLARE_R2_PUBLIC_BASE_URL:
            urls.append(settings.CLOUDFLARE_R2_PUBLIC_BASE_URL)
        return urls

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


cloudflare_r2_image_storage_service = CloudflareR2ImageStorageService()
