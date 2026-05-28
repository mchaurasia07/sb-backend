from uuid import UUID
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.child_profile import ChildProfile


class ChildRepository:
    """Persistence operations for child profiles."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        first_name: str,
        last_name: str,
        dob: date,
        age: int,
        gender: str | None,
        avatar_image_url: str | None,
        child_user_id: str,
        child_password: str,
    ) -> ChildProfile:
        child = ChildProfile(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            age=age,
            gender=gender,
            avatar_image_url=avatar_image_url,
            child_user_id=child_user_id,
            child_password=child_password,
            active=True,
        )
        self.session.add(child)
        await self.session.flush()
        return child

    async def get_by_child_user_id(self, child_user_id: str) -> ChildProfile | None:
        result = await self.session.execute(select(ChildProfile).where(ChildProfile.child_user_id == child_user_id))
        return result.scalar_one_or_none()

    async def get_active_by_child_user_id(self, child_user_id: str) -> ChildProfile | None:
        result = await self.session.execute(
            select(ChildProfile).where(ChildProfile.child_user_id == child_user_id, ChildProfile.active.is_(True))
        )
        return result.scalar_one_or_none()

    async def list_child_user_ids_by_prefix(self, prefix: str) -> list[str]:
        result = await self.session.execute(
            select(ChildProfile.child_user_id).where(ChildProfile.child_user_id.like(f"{prefix}_%"))
        )
        return list(result.scalars().all())

    async def list_by_user(self, user_id: UUID) -> list[ChildProfile]:
        result = await self.session.execute(select(ChildProfile).where(ChildProfile.user_id == user_id).order_by(ChildProfile.created_at.desc()))
        return list(result.scalars().all())

    async def get_for_user(self, user_id: UUID, child_id: UUID) -> ChildProfile | None:
        result = await self.session.execute(select(ChildProfile).where(ChildProfile.id == child_id, ChildProfile.user_id == user_id))
        return result.scalar_one_or_none()

    async def exists_for_user(self, user_id: UUID) -> bool:
        result = await self.session.execute(select(ChildProfile.id).where(ChildProfile.user_id == user_id).limit(1))
        return result.scalar_one_or_none() is not None

    async def update_character(self, child: ChildProfile, character_image_url: str, character_metadata: dict | None) -> None:
        child.character_image_url = character_image_url
        child.character_metadata = character_metadata
        await self.session.flush()

    async def update(self, child: ChildProfile) -> ChildProfile:
        await self.session.flush()
        return child
