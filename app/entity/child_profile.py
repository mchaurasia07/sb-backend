from datetime import date
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin


class ChildProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Child profile owned by a parent user."""

    __tablename__ = "child_profiles"
    __table_args__ = (
        CheckConstraint("age >= 0 AND age <= 18", name="ck_child_profiles_age_range"),
        Index("ix_child_profiles_user_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    first_name: Mapped[str] = mapped_column(String(60), nullable=False)
    last_name: Mapped[str] = mapped_column(String(60), nullable=False)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    avatar_image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    character_image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    character_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user = relationship("User", back_populates="child_profiles", foreign_keys=[user_id])
