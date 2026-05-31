from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.entity.child_audio import ChildAudio
from app.entity.generic_audio import GenericAudio


class ChildAudioRepository:
    """Persistence operations for child audio library assignments."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> ChildAudio:
        child_audio = ChildAudio(**data)
        self.session.add(child_audio)
        await self.session.flush()
        return child_audio

    async def get_for_child(self, child_id: UUID, child_audio_id: UUID) -> ChildAudio | None:
        result = await self.session.execute(
            select(ChildAudio)
            .options(selectinload(ChildAudio.audio))
            .where(ChildAudio.id == child_audio_id, ChildAudio.child_id == child_id)
        )
        return result.scalar_one_or_none()

    async def get_by_child_audio(self, *, child_id: UUID, audio_id: UUID) -> ChildAudio | None:
        result = await self.session.execute(
            select(ChildAudio)
            .options(selectinload(ChildAudio.audio))
            .where(ChildAudio.child_id == child_id, ChildAudio.audio_id == audio_id)
        )
        return result.scalar_one_or_none()

    async def list_for_child_paginated(
        self,
        *,
        child_id: UUID,
        page: int,
        page_size: int,
        active_only: bool = False,
        language: str | None = None,
    ) -> tuple[list[ChildAudio], int]:
        filters = [ChildAudio.child_id == child_id]
        query: Select[tuple[ChildAudio]] = select(ChildAudio).options(selectinload(ChildAudio.audio))
        count_query = select(func.count()).select_from(ChildAudio)

        if language:
            filters.append(ChildAudio.language == language)

        if active_only:
            filters.append(GenericAudio.status == "active")
            query = query.join(GenericAudio, GenericAudio.id == ChildAudio.audio_id)
            count_query = count_query.join(GenericAudio, GenericAudio.id == ChildAudio.audio_id)

        query = query.where(*filters)
        count_query = count_query.where(*filters)

        total = await self.session.scalar(count_query)
        result = await self.session.execute(
            query.order_by(ChildAudio.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def delete(self, child_audio: ChildAudio) -> None:
        await self.session.delete(child_audio)
        await self.session.flush()

    async def delete_by_audio(self, audio_id: UUID) -> None:
        await self.session.execute(delete(ChildAudio).where(ChildAudio.audio_id == audio_id))
        await self.session.flush()

    async def update_language_by_audio(self, audio_id: UUID, language: str) -> None:
        await self.session.execute(
            update(ChildAudio)
            .where(ChildAudio.audio_id == audio_id)
            .values(language=language)
        )
        await self.session.flush()
