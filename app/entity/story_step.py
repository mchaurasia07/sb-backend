from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class StoryStepName(str, Enum):
    """Workflow step names matching POC."""

    STORY_PLAN_GENERATION = "STORY_PLAN_GENERATION"
    STORY_PLAN_VALIDATION = "STORY_PLAN_VALIDATION"
    STORY_GENERATION = "STORY_GENERATION"
    IMAGE_PLAN_GENERATION = "IMAGE_PLAN_GENERATION"
    IMAGE_PLAN_VALIDATION = "IMAGE_PLAN_VALIDATION"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    NARRATION_GENERATION = "NARRATION_GENERATION"


class StepStatus(str, Enum):
    """Step execution status."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED_BATCH_JOB = "SUBMITTED_BATCH_JOB"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StoryStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit trail for each workflow step with retry tracking."""

    __tablename__ = "story_steps"
    __table_args__ = (
        Index("ix_story_steps_story_id", "story_id"),
        Index("ix_story_steps_step_name", "step_name"),
        Index("ix_story_steps_status", "status"),
    )

    story_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )

    step_name: Mapped[StoryStepName] = mapped_column(
        SAEnum(StoryStepName, native_enum=False), nullable=False
    )
    status: Mapped[StepStatus] = mapped_column(
        SAEnum(StepStatus, native_enum=False), nullable=False, default=StepStatus.PENDING
    )

    # Audit data
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    story = relationship("Story", back_populates="steps")
