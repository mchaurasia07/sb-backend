from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.age_groups import AgeGroup
from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class StoryStatus(str, Enum):
    """Story workflow status."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    IMAGE_RETRY_REQUIRED = "IMAGE_RETRY_REQUIRED"
    AUDIO_RETRY_REQUIRED = "AUDIO_RETRY_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StoryType(str, Enum):
    """Story ownership/source type."""

    CUSTOM = "CUSTOM"
    GENERIC = "GENERIC"


class Story(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Story generation record with complete workflow tracking."""

    __tablename__ = "stories"
    __table_args__ = (
        Index("ix_stories_user_id", "user_id"),
        Index("ix_stories_child_id", "child_id"),
        Index("ix_stories_story_type", "story_type"),
        Index("ix_stories_status", "status"),
        Index("ix_stories_created_at", "created_at"),
    )

    # Ownership
    user_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    child_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(), ForeignKey("child_profiles.id", ondelete="CASCADE"), nullable=True
    )
    story_type: Mapped[StoryType] = mapped_column(
        SAEnum(StoryType, native_enum=False),
        nullable=False,
        default=StoryType.CUSTOM,
        server_default=StoryType.CUSTOM.value,
    )

    # Story metadata
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moral: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Generation parameters
    age_group: Mapped[AgeGroup] = mapped_column(
        SAEnum(AgeGroup, values_callable=lambda values: [item.value for item in values], native_enum=False),
        nullable=False,
    )

    # Input-driven parameters (nullable for event-driven)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    learning_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Workflow tracking
    status: Mapped[StoryStatus] = mapped_column(
        SAEnum(StoryStatus, native_enum=False), nullable=False, default=StoryStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    video_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    video_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    child = relationship("ChildProfile", foreign_keys=[child_id])
    steps = relationship("StoryStep", back_populates="story", cascade="all, delete-orphan")
    pages = relationship(
        "StoryPage",
        back_populates="story",
        cascade="all, delete-orphan",
        order_by="StoryPage.page_number",
    )
    contents = relationship(
        "StoryContent",
        back_populates="story",
        cascade="all, delete-orphan",
    )
    batch_jobs = relationship(
        "StoryBatchJob",
        back_populates="story",
        cascade="all, delete-orphan",
    )


class StoryContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Language-specific story JSON for a custom/generated story."""

    __tablename__ = "story_contents"
    __table_args__ = (
        UniqueConstraint("story_id", "language", name="uq_story_contents_story_language"),
        Index("ix_story_contents_story_id", "story_id"),
        Index("ix_story_contents_language", "language"),
    )

    story_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    story_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    story = relationship("Story", back_populates="contents", foreign_keys=[story_id])
