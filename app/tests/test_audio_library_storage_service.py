from io import BytesIO
from uuid import UUID

import pytest

from app.core.config import settings
from app.service.audio_library_storage_service import AudioLibraryStorageService


class UploadStub:
    def __init__(self, *, filename: str, content_type: str, content: bytes):
        self.filename = filename
        self.content_type = content_type
        self._file = BytesIO(content)

    async def read(self) -> bytes:
        return self._file.read()


class R2ClientStub:
    def __init__(self):
        self.objects = []

    def put_object(self, **kwargs):
        self.objects.append(kwargs)


@pytest.mark.asyncio
async def test_save_generic_audio_files_to_r2_uses_audio_and_photo_prefixes(monkeypatch):
    audio_id = UUID("08c5206c-c503-40f3-a69d-e4aa72f7164e")
    service = AudioLibraryStorageService()
    client = R2ClientStub()

    monkeypatch.setattr(settings, "AUDIO_LIBRARY_STORAGE_PROVIDER", "r2")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_ACCOUNT_ID", "account-id")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_ACCESS_KEY_ID", "access-key")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_BUCKET_NAME", "storybook")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://pub-c2bbc7933325408a8f2d12ff895599a7.r2.dev")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_AUDIO_KEY_PREFIX", "audio")
    monkeypatch.setattr(settings, "CLOUDFLARE_R2_IMAGE_KEY_PREFIX", "photo")
    monkeypatch.setattr(service, "_r2_client", lambda: client)

    audio_url, image_url = await service.save_generic_audio_files(
        audio_id=audio_id,
        audio_file=UploadStub(filename="audio.wav", content_type="audio/wav", content=b"audio-bytes"),
        image_file=UploadStub(filename="image.png", content_type="image/png", content=b"image-bytes"),
        public_base_url="https://app.example.com",
    )

    assert audio_url == (
        "https://pub-c2bbc7933325408a8f2d12ff895599a7.r2.dev/"
        "audio/audio_lib/08c5206c-c503-40f3-a69d-e4aa72f7164e/audio.wav"
    )
    assert image_url == (
        "https://pub-c2bbc7933325408a8f2d12ff895599a7.r2.dev/"
        "photo/audio_lib/08c5206c-c503-40f3-a69d-e4aa72f7164e/image.png"
    )
    assert [item["Key"] for item in client.objects] == [
        "audio/audio_lib/08c5206c-c503-40f3-a69d-e4aa72f7164e/audio.wav",
        "photo/audio_lib/08c5206c-c503-40f3-a69d-e4aa72f7164e/image.png",
    ]
    assert [item["ContentType"] for item in client.objects] == ["audio/wav", "image/png"]
