from uuid import UUID

from fastapi import UploadFile
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.entity.generic_audio import GenericAudioLanguage
from app.model.request.generic_audio import GenericAudioCreateRequest, GenericAudioUpdateRequest
from app.model.response.common import PaginatedResponse
from app.model.response.generic_audio import GenericAudioResponse
from app.repository.child_audio_repository import ChildAudioRepository
from app.repository.generic_audio_repository import GenericAudioRepository
from app.service.audio_library_storage_service import audio_library_storage_service


class GenericAudioService:
    """Generic audio catalog use cases."""

    def __init__(self, session: AsyncSession):
        self.generic_audios = GenericAudioRepository(session)
        self.child_audios = ChildAudioRepository(session)

    async def create(self, payload: GenericAudioCreateRequest) -> GenericAudioResponse:
        create_data = payload.model_dump()
        create_data["language"] = self._language_value(create_data["language"])

        existing = await self.generic_audios.get_by_name_and_language(create_data["name"], create_data["language"])
        if existing is not None:
            raise ConflictException(
                "Generic audio with this name and language already exists",
                status.HTTP_409_CONFLICT,
                "GENERIC_AUDIO_ALREADY_EXISTS",
            )
        audio = await self.generic_audios.create(**create_data)
        return GenericAudioResponse.model_validate(audio)

    async def create_with_uploads(
        self,
        *,
        name: str,
        language: GenericAudioLanguage | str,
        description: str | None,
        status_value: str,
        audio_file: UploadFile,
        image_file: UploadFile,
        public_base_url: str,
    ) -> GenericAudioResponse:
        language_value = self._language_value(language)
        existing = await self.generic_audios.get_by_name_and_language(name, language_value)
        if existing is not None:
            raise ConflictException(
                "Generic audio with this name and language already exists",
                status.HTTP_409_CONFLICT,
                "GENERIC_AUDIO_ALREADY_EXISTS",
            )

        audio = await self.generic_audios.create(
            name=name,
            language=language_value,
            audio_url="",
            image_url=None,
            description=description,
            status=status_value,
        )

        try:
            audio_url, image_url = await audio_library_storage_service.save_generic_audio_files(
                audio_id=audio.id,
                audio_file=audio_file,
                image_file=image_file,
                public_base_url=public_base_url,
            )
        except Exception:
            await audio_library_storage_service.delete_generic_audio_directory(audio.id)
            raise

        audio.audio_url = audio_url
        audio.image_url = image_url
        await self.generic_audios.update(audio)
        return GenericAudioResponse.model_validate(audio)

    async def get(self, audio_id: UUID) -> GenericAudioResponse:
        audio = await self.generic_audios.get_by_id(audio_id)
        if audio is None:
            raise NotFoundException("Generic audio not found", "GENERIC_AUDIO_NOT_FOUND")
        return GenericAudioResponse.model_validate(audio)

    async def update(self, audio_id: UUID, payload: GenericAudioUpdateRequest) -> GenericAudioResponse:
        audio = await self.generic_audios.get_by_id(audio_id)
        if audio is None:
            raise NotFoundException("Generic audio not found", "GENERIC_AUDIO_NOT_FOUND")

        update_data = payload.model_dump(exclude_unset=True)
        if "language" in update_data:
            update_data["language"] = self._language_value(update_data["language"])

        next_name = update_data.get("name", audio.name)
        next_language = update_data.get("language", audio.language)
        language_changed = next_language != audio.language
        if next_name != audio.name or next_language != audio.language:
            existing = await self.generic_audios.get_by_name_and_language(next_name, next_language)
            if existing is not None:
                raise ConflictException(
                    "Generic audio with this name and language already exists",
                    status.HTTP_409_CONFLICT,
                    "GENERIC_AUDIO_ALREADY_EXISTS",
                )

        for field, value in update_data.items():
            setattr(audio, field, value)
        await self.generic_audios.update(audio)
        if language_changed:
            await self.child_audios.update_language_by_audio(audio.id, next_language)
        return GenericAudioResponse.model_validate(audio)

    async def delete(self, audio_id: UUID) -> None:
        audio = await self.generic_audios.get_by_id(audio_id)
        if audio is None:
            raise NotFoundException("Generic audio not found", "GENERIC_AUDIO_NOT_FOUND")
        await self.child_audios.delete_by_audio(audio.id)
        await self.generic_audios.delete(audio)
        await audio_library_storage_service.delete_generic_audio_directory(audio.id)

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str | None = None,
        language: GenericAudioLanguage | str | None = None,
    ) -> PaginatedResponse[GenericAudioResponse]:
        if status_filter not in (None, "active", "inactive"):
            raise AppException("Invalid generic audio status", code="GENERIC_AUDIO_STATUS_INVALID")

        language_filter = self._language_value(language) if language is not None else None
        audios, total = await self.generic_audios.list_paginated(
            page=page,
            page_size=page_size,
            status=status_filter,
            language=language_filter,
        )
        return PaginatedResponse[GenericAudioResponse].create(
            items=[GenericAudioResponse.model_validate(audio) for audio in audios],
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _language_value(language: GenericAudioLanguage | str) -> str:
        return language.value if isinstance(language, GenericAudioLanguage) else str(language).strip().lower()
