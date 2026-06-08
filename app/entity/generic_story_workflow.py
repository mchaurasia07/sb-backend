from enum import StrEnum
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class GenericStoryWorkflowStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GenericStoryWorkflowStep(StrEnum):
    CHARACTER_EXTRACTION = "CHARACTER_EXTRACTION"
    SCENE_PLAN_GENERATION = "SCENE_PLAN_GENERATION"
    VISUAL_BIBLE_GENERATION = "VISUAL_BIBLE_GENERATION"
    STORY_GENERATION = "STORY_GENERATION"
    IMAGE_PLAN_GENERATION = "IMAGE_PLAN_GENERATION"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    NARRATION_GENERATION = "NARRATION_GENERATION"
    PUBLISH_GENERIC_STORY = "PUBLISH_GENERIC_STORY"


class GenericStoryWorkflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workflow state for converting an existing story into a generic story."""

    __tablename__ = "generic_story_workflows"
    __table_args__ = (
        Index("ix_generic_story_workflows_user_id", "user_id"),
        Index("ix_generic_story_workflows_status", "status"),
        Index("ix_generic_story_workflows_generic_story_id", "generic_story_id"),
        Index("ix_generic_story_workflows_created_at", "created_at"),
        Index("ix_generic_story_workflows_user_created_at", "user_id", "created_at"),
        Index(
            "ix_generic_story_workflows_user_story_created_at",
            "user_id",
            "generic_story_id",
            "created_at",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(HyphenatedUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    generic_story_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("generic_stories.id", ondelete="SET NULL"),
        nullable=True,
    )

    workflow_name: Mapped[str] = mapped_column(String(64), nullable=False, default="generic_story")
    actual_story: Mapped[str] = mapped_column(Text, nullable=False)
    age_group: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    requested_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=GenericStoryWorkflowStatus.PENDING.value)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    character_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scene_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visual_bible_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    story_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    theme: Mapped[str | None] = mapped_column(String(100), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moral: Mapped[str | None] = mapped_column(String(255), nullable=True)
    learning_goal: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    ai_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    text_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    generic_story = relationship("GenericStory", foreign_keys=[generic_story_id])
