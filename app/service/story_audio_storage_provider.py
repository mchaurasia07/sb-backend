from app.core.config import settings
from app.core.exceptions import AppException
from app.service.story_audio_storage_service import (
    cloudflare_r2_story_audio_storage_service,
    local_story_audio_storage_service,
)


def get_story_audio_storage_service():
    provider = settings.AUDIO_STORAGE_PROVIDER.strip().lower()
    if provider == "local":
        return local_story_audio_storage_service
    if provider == "r2":
        return cloudflare_r2_story_audio_storage_service

    raise AppException(
        f"Unsupported audio storage provider: {settings.AUDIO_STORAGE_PROVIDER}",
        code="AUDIO_STORAGE_PROVIDER_INVALID",
    )
