from sqlalchemy import CheckConstraint, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin


class GenericStory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reusable story content that can be assigned to child libraries."""

    __tablename__ = "generic_stories"
    __table_args__ = (
        CheckConstraint("reading_time_minutes >= 0", name="ck_generic_stories_reading_time_non_negative"),
        CheckConstraint("total_pages >= 0", name="ck_generic_stories_total_pages_non_negative"),
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
    language: Mapped[str] = mapped_column(String(50), nullable=False, default="en")
    moral: Mapped[str | None] = mapped_column(String(255), nullable=True)
    learning_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reading_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    story_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
