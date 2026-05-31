from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.entity.child_audio import ChildAudio
from app.entity.generic_audio import GenericAudioLanguage
from app.entity.user import User
from app.model.response.child_audio import ChildAudioResponse
from app.model.response.common import PaginatedResponse
from app.repository.child_audio_repository import ChildAudioRepository
from app.repository.child_repository import ChildRepository
from app.repository.generic_audio_repository import GenericAudioRepository


class ChildAudioService:
    """Parent and child audio library use cases."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.children = ChildRepository(session)
        self.generic_audios = GenericAudioRepository(session)
        self.child_audios = ChildAudioRepository(session)

    async def add_generic_audio(
        self,
        *,
        current_user: User,
        child_id: UUID,
        audio_id: UUID,
    ) -> ChildAudioResponse:
        await self._get_child_for_user(current_user.id, child_id)

        audio = await self.generic_audios.get_by_id(audio_id)
        if audio is None:
            raise NotFoundException("Generic audio not found", "GENERIC_AUDIO_NOT_FOUND")
        if audio.status != "active":
            raise AppException(
                "Only active generic audio can be added to a child",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_AUDIO_INACTIVE",
            )

        existing = await self.child_audios.get_by_child_audio(child_id=child_id, audio_id=audio.id)
        if existing is not None:
            raise ConflictException(
                "Generic audio is already available for this child",
                status.HTTP_409_CONFLICT,
                "CHILD_AUDIO_ALREADY_EXISTS",
            )

        child_audio = await self.child_audios.create(child_id=child_id, audio_id=audio.id, language=audio.language)
        child_audio.audio = audio
        return self._to_response(child_audio)

    async def list_for_child(
        self,
        *,
        current_user: User,
        child_id: UUID,
        page: int,
        page_size: int,
        language: GenericAudioLanguage | str | None = None,
    ) -> PaginatedResponse[ChildAudioResponse]:
        await self._get_child_for_user(current_user.id, child_id)
        return await self._list_child_audios(
            child_id=child_id,
            page=page,
            page_size=page_size,
            active_only=False,
            language=language,
        )

    async def list_for_child_library(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
        language: GenericAudioLanguage | str | None = None,
    ) -> PaginatedResponse[ChildAudioResponse]:
        return await self._list_child_audios(
            child_id=child_id,
            page=page,
            page_size=page_size,
            active_only=True,
            language=language,
        )

    async def get_for_child_library(self, *, child_id: UUID, child_audio_id: UUID) -> ChildAudioResponse:
        child_audio = await self.child_audios.get_for_child(child_id, child_audio_id)
        if child_audio is None or child_audio.audio is None or child_audio.audio.status != "active":
            raise NotFoundException("Child audio not found", "CHILD_AUDIO_NOT_FOUND")
        return self._to_response(child_audio)

    async def delete_child_audio(
        self,
        *,
        current_user: User,
        child_id: UUID,
        child_audio_id: UUID,
    ) -> None:
        await self._get_child_for_user(current_user.id, child_id)
        child_audio = await self.child_audios.get_for_child(child_id, child_audio_id)
        if child_audio is None:
            raise NotFoundException("Child audio not found", "CHILD_AUDIO_NOT_FOUND")
        await self.child_audios.delete(child_audio)

    async def _get_child_for_user(self, user_id: UUID, child_id: UUID):
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found", "CHILD_NOT_FOUND")
        return child

    async def _list_child_audios(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
        active_only: bool,
        language: GenericAudioLanguage | str | None,
    ) -> PaginatedResponse[ChildAudioResponse]:
        language_filter = self._language_value(language) if language is not None else None
        audios, total = await self.child_audios.list_for_child_paginated(
            child_id=child_id,
            page=page,
            page_size=page_size,
            active_only=active_only,
            language=language_filter,
        )
        return PaginatedResponse[ChildAudioResponse].create(
            items=[self._to_response(child_audio) for child_audio in audios if child_audio.audio is not None],
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _to_response(child_audio: ChildAudio) -> ChildAudioResponse:
        audio = child_audio.audio
        return ChildAudioResponse(
            child_audio_id=child_audio.id,
            child_id=child_audio.child_id,
            audio_id=child_audio.audio_id,
            name=audio.name,
            language=child_audio.language,
            audio_url=audio.audio_url,
            image_url=audio.image_url,
            description=audio.description,
            audio_status=audio.status,
            created_at=child_audio.created_at,
            updated_at=child_audio.updated_at,
        )

    @staticmethod
    def _language_value(language: GenericAudioLanguage | str) -> str:
        return language.value if isinstance(language, GenericAudioLanguage) else str(language).strip().lower()
