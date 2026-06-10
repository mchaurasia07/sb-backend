from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.age_groups import AgeGroup
from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StepStatus
from app.entity.types import HyphenatedUUID


class CustomStoryWorkflowStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CustomStoryWorkflowStep(StrEnum):
    STORY_PLAN_GENERATION = "STORY_PLAN_GENERATION"
    STORY_PLAN_VALIDATION = "STORY_PLAN_VALIDATION"
    STORY_GENERATION = "STORY_GENERATION"
    IMAGE_PLAN_GENERATION = "IMAGE_PLAN_GENERATION"
    IMAGE_PLAN_VALIDATION = "IMAGE_PLAN_VALIDATION"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    NARRATION_GENERATION = "NARRATION_GENERATION"
    PUBLISH_STORY = "PUBLISH_STORY"


class CustomStoryWorkflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workflow state for customer-created custom stories."""

    __tablename__ = "custom_story_workflows"
    __table_args__ = (
        Index("ix_custom_story_workflows_user_id", "user_id"),
        Index("ix_custom_story_workflows_child_id", "child_id"),
        Index("ix_custom_story_workflows_story_id", "story_id"),
        Index("ix_custom_story_workflows_status", "status"),
        Index("ix_custom_story_workflows_created_at", "created_at"),
        Index("ix_custom_story_workflows_user_created_at", "user_id", "created_at"),
        UniqueConstraint("request_number", name="uq_custom_story_workflows_request_number"),
    )

    user_id: Mapped[UUID] = mapped_column(HyphenatedUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    child_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(), ForeignKey("child_profiles.id", ondelete="CASCADE"), nullable=False
    )
    story_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(), ForeignKey("stories.id", ondelete="SET NULL"), nullable=True
    )
    request_number: Mapped[int] = mapped_column(Integer, nullable=False)

    generation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="instant")
    age_group: Mapped[AgeGroup] = mapped_column(
        SAEnum(AgeGroup, values_callable=lambda values: [item.value for item in values], native_enum=False),
        nullable=False,
    )

    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    learning_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reader_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_child_character: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    execute_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    execute_narration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    skip_validation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    execute_workflow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    status: Mapped[CustomStoryWorkflowStatus] = mapped_column(
        SAEnum(CustomStoryWorkflowStatus, native_enum=False),
        nullable=False,
        default=CustomStoryWorkflowStatus.PENDING,
    )
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    story_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    story_plan_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    story_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_plan_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    moral: Mapped[str | None] = mapped_column(String(255), nullable=True)

    ai_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reference_image_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    steps = relationship("CustomStoryWorkflowStepRecord", back_populates="workflow", cascade="all, delete-orphan")
    batch_jobs = relationship("CustomStoryBatchJob", back_populates="workflow", cascade="all, delete-orphan")


class CustomStoryWorkflowStepRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit trail for custom story workflow steps."""

    __tablename__ = "custom_story_workflow_steps"
    __table_args__ = (
        Index("ix_custom_story_workflow_steps_workflow_id", "workflow_id"),
        Index("ix_custom_story_workflow_steps_step_name", "step_name"),
        Index("ix_custom_story_workflow_steps_status", "status"),
        Index("ix_custom_story_workflow_steps_workflow_created_at", "workflow_id", "created_at"),
        Index("ix_custom_story_workflow_steps_workflow_step_created_at", "workflow_id", "step_name", "created_at"),
    )

    workflow_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(), ForeignKey("custom_story_workflows.id", ondelete="CASCADE"), nullable=False
    )
    step_name: Mapped[CustomStoryWorkflowStep] = mapped_column(
        SAEnum(CustomStoryWorkflowStep, native_enum=False), nullable=False
    )
    status: Mapped[StepStatus] = mapped_column(
        SAEnum(StepStatus, native_enum=False), nullable=False, default=StepStatus.PENDING
    )
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow = relationship("CustomStoryWorkflow", back_populates="steps", foreign_keys=[workflow_id])

    @property
    def story_id(self) -> UUID:
        return self.workflow_id

    @property
    def response(self) -> dict | None:
        return self.output_json

    @response.setter
    def response(self, value: dict | None) -> None:
        self.output_json = value


class CustomStoryBatchJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider batch job attached to a workflow before final story publish."""

    __tablename__ = "custom_story_batch_jobs"
    __table_args__ = (
        Index("ix_custom_story_batch_jobs_workflow_id", "workflow_id"),
        Index("ix_custom_story_batch_jobs_story_id", "story_id"),
        Index("ix_custom_story_batch_jobs_status", "status"),
        Index("ix_custom_story_batch_jobs_provider_job_name", "provider_job_name"),
        Index("ix_custom_story_batch_jobs_workflow_created_at", "workflow_id", "created_at"),
        Index("ix_custom_story_batch_jobs_workflow_type_created_at", "workflow_id", "job_type", "created_at"),
        UniqueConstraint("provider_job_name", name="uq_custom_story_batch_jobs_provider_job_name"),
    )

    workflow_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(), ForeignKey("custom_story_workflows.id", ondelete="CASCADE"), nullable=False
    )
    story_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(), ForeignKey("stories.id", ondelete="SET NULL"), nullable=True
    )
    job_type: Mapped[StoryBatchJobType] = mapped_column(SAEnum(StoryBatchJobType, native_enum=False), nullable=False)
    status: Mapped[StoryBatchJobStatus] = mapped_column(
        SAEnum(StoryBatchJobStatus, native_enum=False), nullable=False, default=StoryBatchJobStatus.SUBMITTED
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    provider_job_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(nullable=False, default=1)
    expected_item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    completed_item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    failed_item_count: Mapped[int] = mapped_column(nullable=False, default=0)
    request_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow = relationship("CustomStoryWorkflow", back_populates="batch_jobs", foreign_keys=[workflow_id])
