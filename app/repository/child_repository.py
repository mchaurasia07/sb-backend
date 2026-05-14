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
    ) -> ChildProfile:
        child = ChildProfile(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            age=age,
            gender=gender,
            avatar_image_url=avatar_image_url,
        )
        self.session.add(child)
        await self.session.flush()
        return child

    async def list_by_user(self, user_id: UUID) -> list[ChildProfile]:
        result = await self.session.execute(select(ChildProfile).where(ChildProfile.user_id == user_id).order_by(ChildProfile.created_at.desc()))
        return list(result.scalars().all())

    async def get_for_user(self, user_id: UUID, child_id: UUID) -> ChildProfile | None:
        result = await self.session.execute(select(ChildProfile).where(ChildProfile.id == child_id, ChildProfile.user_id == user_id))
        return result.scalar_one_or_none()

    async def exists_for_user(self, user_id: UUID) -> bool:
        result = await self.session.execute(select(ChildProfile.id).where(ChildProfile.user_id == user_id).limit(1))
        return result.scalar_one_or_none() is not None
