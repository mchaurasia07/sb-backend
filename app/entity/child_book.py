from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class ChildBook(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A book available in a child's library."""

    __tablename__ = "child_books"
    __table_args__ = (
        CheckConstraint("language IN ('en', 'hi', 'mr')", name="ck_child_books_language"),
        UniqueConstraint("child_id", "story_id", "story_type", "language", name="uq_child_books_child_story_type_language"),
        Index("ix_child_books_child_id", "child_id"),
        Index("ix_child_books_story_id", "story_id"),
        Index("ix_child_books_language", "language"),
        Index("ix_child_books_status", "status"),
        Index("ix_child_books_created_at", "created_at"),
    )

    child_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("child_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    story_id: Mapped[UUID] = mapped_column(HyphenatedUUID(), nullable=False)
    story_type: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    last_page_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_page_read_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reading_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reading_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reading_started_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reading_completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    child = relationship("ChildProfile", foreign_keys=[child_id])
