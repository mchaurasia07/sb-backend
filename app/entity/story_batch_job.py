from enum import Enum
from uuid import UUID

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class StoryBatchJobType(str, Enum):
    """Batch job categories used by delayed story generation."""

    IMAGE = "IMAGE"
    AUDIO = "AUDIO"


class StoryBatchJobStatus(str, Enum):
    """Internal status for provider-backed batch jobs."""

    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StoryBatchJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent audit record for a Google Gemini batch job attempt."""

    __tablename__ = "story_batch_jobs"
    __table_args__ = (
        Index("ix_story_batch_jobs_story_id", "story_id"),
        Index("ix_story_batch_jobs_provider_job_name", "provider_job_name"),
        Index("ix_story_batch_jobs_status", "status"),
        Index("ix_story_batch_jobs_job_type", "job_type"),
    )

    story_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[StoryBatchJobType] = mapped_column(
        SAEnum(StoryBatchJobType, native_enum=False),
        nullable=False,
    )
    status: Mapped[StoryBatchJobStatus] = mapped_column(
        SAEnum(StoryBatchJobStatus, native_enum=False),
        nullable=False,
        default=StoryBatchJobStatus.SUBMITTED,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    provider_job_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_state: Mapped[str | None] = mapped_column(String(64), nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expected_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    request_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    story = relationship("Story", back_populates="batch_jobs", foreign_keys=[story_id])
