from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.otp_verification import OtpPurpose, OtpVerification


class OtpRepository:
    """Persistence operations for OTP challenges."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def invalidate_active(self, user_id: UUID, purpose: OtpPurpose) -> None:
        await self.session.execute(
            update(OtpVerification)
            .where(
                OtpVerification.user_id == user_id,
                OtpVerification.purpose == purpose,
                OtpVerification.is_used.is_(False),
            )
            .values(is_used=True)
        )

    async def create(self, user_id: UUID, purpose: OtpPurpose, otp_hash: str, expires_at: datetime) -> OtpVerification:
        otp = OtpVerification(user_id=user_id, purpose=purpose, otp_hash=otp_hash, expires_at=expires_at)
        self.session.add(otp)
        await self.session.flush()
        return otp

    async def get_active(self, user_id: UUID, purpose: OtpPurpose) -> OtpVerification | None:
        result = await self.session.execute(
            select(OtpVerification)
            .where(
                OtpVerification.user_id == user_id,
                OtpVerification.purpose == purpose,
                OtpVerification.is_used.is_(False),
                OtpVerification.expires_at > datetime.now(UTC),
            )
            .order_by(OtpVerification.created_at.desc())
        )
        return result.scalars().first()

    async def mark_used(self, otp: OtpVerification) -> None:
        otp.is_used = True
        await self.session.flush()

    async def increment_attempts(self, otp: OtpVerification) -> None:
        otp.attempts += 1
        await self.session.flush()
