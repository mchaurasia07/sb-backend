from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.generic_audio import GenericAudioLanguage


class ChildAudio(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An audio item available in a child's audio library."""

    __tablename__ = "child_audios"
    __table_args__ = (
        CheckConstraint("language IN ('en', 'hi', 'mr')", name="ck_child_audios_language"),
        UniqueConstraint("child_id", "audio_id", name="uq_child_audios_child_audio"),
        Index("ix_child_audios_child_id", "child_id"),
        Index("ix_child_audios_audio_id", "audio_id"),
        Index("ix_child_audios_language", "language"),
        Index("ix_child_audios_created_at", "created_at"),
    )

    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    audio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generic_audios.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default=GenericAudioLanguage.EN.value)

    child = relationship("ChildProfile", foreign_keys=[child_id])
    audio = relationship("GenericAudio", back_populates="child_audios", foreign_keys=[audio_id])
