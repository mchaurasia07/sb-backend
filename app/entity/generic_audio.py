from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin


class GenericAudioLanguage(StrEnum):
    EN = "en"
    HI = "hi"
    MR = "mr"


class GenericAudio(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reusable audio content that can be assigned to child libraries."""

    __tablename__ = "generic_audios"
    __table_args__ = (
        CheckConstraint("language IN ('en', 'hi', 'mr')", name="ck_generic_audios_language"),
        UniqueConstraint("name", "language", name="uq_generic_audios_name_language"),
        Index("ix_generic_audios_status", "status"),
        Index("ix_generic_audios_language", "language"),
        Index("ix_generic_audios_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default=GenericAudioLanguage.EN.value)
    audio_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    child_audios = relationship(
        "ChildAudio",
        back_populates="audio",
        cascade="all, delete-orphan",
    )
