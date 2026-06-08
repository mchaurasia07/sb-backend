from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class StoryPage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Individual story page with text and image."""

    __tablename__ = "story_pages"
    __table_args__ = (
        Index("ix_story_pages_story_id", "story_id"),
        Index("ix_story_pages_story_id_page_number", "story_id", "page_number", unique=True),
    )

    story_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )

    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_type: Mapped[str] = mapped_column(String(20), nullable=False)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Relationships
    story = relationship("Story", back_populates="pages")
