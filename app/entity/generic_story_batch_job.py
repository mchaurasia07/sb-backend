from uuid import UUID

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType


class GenericStoryBatchJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent audit record for generic story Google Gemini batch jobs."""

    __tablename__ = "generic_story_batch_jobs"
    __table_args__ = (
        Index("ix_generic_story_batch_jobs_generic_story_id", "generic_story_id"),
        Index("ix_generic_story_batch_jobs_workflow_id", "workflow_id"),
        Index("ix_generic_story_batch_jobs_provider_job_name", "provider_job_name"),
        Index("ix_generic_story_batch_jobs_status", "status"),
        Index("ix_generic_story_batch_jobs_job_type", "job_type"),
    )

    generic_story_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generic_stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generic_story_workflows.id", ondelete="CASCADE"),
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

    generic_story = relationship("GenericStory", foreign_keys=[generic_story_id])
    workflow = relationship("GenericStoryWorkflow", foreign_keys=[workflow_id])
