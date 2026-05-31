from app.core.config import settings
from app.core.exceptions import AppException
from app.service.cloudflare_r2_image_storage_service import cloudflare_r2_image_storage_service
from app.service.image_storage_service import image_storage_service


def get_image_storage_service():
    provider = settings.IMAGE_STORAGE_PROVIDER.strip().lower()
    if provider == "local":
        return image_storage_service
    if provider == "r2":
        return cloudflare_r2_image_storage_service

    raise AppException(
        f"Unsupported image storage provider: {settings.IMAGE_STORAGE_PROVIDER}",
        code="IMAGE_STORAGE_PROVIDER_INVALID",
    )
