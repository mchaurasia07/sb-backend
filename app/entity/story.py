from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Index, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.age_groups import AGE_GROUP_0_2, AGE_GROUP_2_4, AGE_GROUP_4_6, AGE_GROUP_6_8
from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin


class StoryStatus(str, Enum):
    """Story workflow status."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    IMAGE_RETRY_REQUIRED = "IMAGE_RETRY_REQUIRED"
    AUDIO_RETRY_REQUIRED = "AUDIO_RETRY_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StoryGenerationMode(str, Enum):
    """Story generation mode."""

    INPUT_DRIVEN = "INPUT_DRIVEN"
    EVENT_DRIVEN = "EVENT_DRIVEN"


class AgeGroup(str, Enum):
    """Age group for story content."""

    INFANT_TODDLER = AGE_GROUP_0_2
    TODDLER = AGE_GROUP_2_4
    EARLY_READER = AGE_GROUP_4_6
    ADVANCED = AGE_GROUP_6_8


class Story(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Story generation record with complete workflow tracking."""

    __tablename__ = "stories"
    __table_args__ = (
        Index("ix_stories_user_id", "user_id"),
        Index("ix_stories_child_id", "child_id"),
        Index("ix_stories_status", "status"),
        Index("ix_stories_created_at", "created_at"),
    )

    # Ownership
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("child_profiles.id", ondelete="CASCADE"), nullable=False
    )

    # Story metadata
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moral: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Generation parameters
    generation_mode: Mapped[StoryGenerationMode] = mapped_column(
        SAEnum(StoryGenerationMode, native_enum=False), nullable=False
    )
    age_group: Mapped[AgeGroup] = mapped_column(SAEnum(AgeGroup, native_enum=False), nullable=False)

    # Input-driven parameters (nullable for event-driven)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    learning_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Event-driven parameters (nullable for input-driven)
    event_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Workflow tracking
    status: Mapped[StoryStatus] = mapped_column(
        SAEnum(StoryStatus, native_enum=False), nullable=False, default=StoryStatus.PENDING
    )
    current_step: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON storage for workflow planning data
    story_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    story_plan_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    image_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_plan_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    input_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # AI configuration locked at story creation so retries use the same provider/models.
    ai_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reference_image_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

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
        Uuid(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    story_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    story = relationship("Story", back_populates="contents", foreign_keys=[story_id])
