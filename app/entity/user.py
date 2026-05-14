import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin


class AuthProvider(str, enum.Enum):
    LOCAL = "local"
    GOOGLE = "google"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Application user account."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_phone", "phone", unique=True),
        Index("ix_users_google_sub", "google_sub", unique=True),
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(Enum(AuthProvider), default=AuthProvider.LOCAL, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_child_profile_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("child_profiles.id", use_alter=True), nullable=True)

    child_profiles = relationship(
        "ChildProfile",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ChildProfile.user_id",
    )
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    otp_verifications = relationship("OtpVerification", back_populates="user", cascade="all, delete-orphan")
