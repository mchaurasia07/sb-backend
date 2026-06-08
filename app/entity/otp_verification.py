import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class OtpPurpose(str, enum.Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class OtpVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Secure OTP challenge record."""

    __tablename__ = "otp_verifications"
    __table_args__ = (
        Index("ix_otp_user_purpose", "user_id", "purpose"),
        Index("ix_otp_expires_at", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(HyphenatedUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[OtpPurpose] = mapped_column(Enum(OtpPurpose), nullable=False)
    otp_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user = relationship("User", back_populates="otp_verifications")
