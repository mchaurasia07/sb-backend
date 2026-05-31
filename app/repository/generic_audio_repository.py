from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.generic_audio import GenericAudio


class GenericAudioRepository:
    """Persistence operations for reusable audio catalog items."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> GenericAudio:
        audio = GenericAudio(**data)
        self.session.add(audio)
        await self.session.flush()
        return audio

    async def get_by_id(self, audio_id: UUID) -> GenericAudio | None:
        result = await self.session.execute(select(GenericAudio).where(GenericAudio.id == audio_id))
        return result.scalar_one_or_none()

    async def get_by_name_and_language(self, name: str, language: str) -> GenericAudio | None:
        result = await self.session.execute(
            select(GenericAudio).where(GenericAudio.name == name, GenericAudio.language == language)
        )
        return result.scalar_one_or_none()

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        language: str | None = None,
    ) -> tuple[list[GenericAudio], int]:
        query: Select[tuple[GenericAudio]] = select(GenericAudio)
        count_query = select(func.count()).select_from(GenericAudio)

        if status:
            query = query.where(GenericAudio.status == status)
            count_query = count_query.where(GenericAudio.status == status)

        if language:
            query = query.where(GenericAudio.language == language)
            count_query = count_query.where(GenericAudio.language == language)

        total = await self.session.scalar(count_query)
        result = await self.session.execute(
            query.order_by(GenericAudio.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def delete(self, audio: GenericAudio) -> None:
        await self.session.delete(audio)
        await self.session.flush()

    async def update(self, audio: GenericAudio) -> GenericAudio:
        await self.session.flush()
        return audio
