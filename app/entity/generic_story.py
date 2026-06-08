from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class GenericStoryLanguage(StrEnum):
    EN = "en"
    HI = "hi"
    MR = "mr"


class GenericStory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reusable story content that can be assigned to child libraries."""

    __tablename__ = "generic_stories"
    __table_args__ = (
        CheckConstraint("reading_time_minutes >= 0", name="ck_generic_stories_reading_time_non_negative"),
        CheckConstraint("total_pages >= 0", name="ck_generic_stories_total_pages_non_negative"),
        UniqueConstraint("title", name="uq_generic_stories_title"),
        Index("ix_generic_stories_status", "status"),
        Index("ix_generic_stories_age_group", "age_group"),
        Index("ix_generic_stories_theme", "theme"),
        Index("ix_generic_stories_genre", "genre"),
        Index("ix_generic_stories_created_at", "created_at"),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_group: Mapped[str] = mapped_column(String(32), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(100), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moral: Mapped[str | None] = mapped_column(String(255), nullable=True)
    learning_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reading_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    contents = relationship(
        "GenericStoryContent",
        back_populates="generic_story",
        cascade="all, delete-orphan",
    )


class GenericStoryContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Language-specific story JSON for a generic story."""

    __tablename__ = "generic_story_contents"
    __table_args__ = (
        UniqueConstraint("generic_story_id", "language", name="uq_generic_story_contents_story_language"),
        Index("ix_generic_story_contents_story_id", "generic_story_id"),
        Index("ix_generic_story_contents_language", "language"),
    )

    generic_story_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("generic_stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    story_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    generic_story = relationship("GenericStory", back_populates="contents", foreign_keys=[generic_story_id])
