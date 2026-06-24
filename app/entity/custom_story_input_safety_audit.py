from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class CustomStoryInputSafetyAuditStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    ERROR = "ERROR"


class CustomStoryInputSafetyAudit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent audit record for custom story input safety validation."""

    __tablename__ = "custom_story_input_safety_audits"
    __table_args__ = (
        Index("ix_custom_story_input_safety_audits_user_id", "user_id"),
        Index("ix_custom_story_input_safety_audits_child_id", "child_id"),
        Index("ix_custom_story_input_safety_audits_workflow_id", "workflow_id"),
        Index("ix_custom_story_input_safety_audits_status", "status"),
        Index("ix_custom_story_input_safety_audits_created_at", "created_at"),
        Index("ix_custom_story_input_safety_audits_user_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(HyphenatedUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    child_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(), ForeignKey("child_profiles.id", ondelete="CASCADE"), nullable=True
    )
    workflow_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(), ForeignKey("custom_story_workflows.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[CustomStoryInputSafetyAuditStatus] = mapped_column(
        SAEnum(CustomStoryInputSafetyAuditStatus, native_enum=False),
        nullable=False,
        default=CustomStoryInputSafetyAuditStatus.IN_PROGRESS,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    request_idea_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    safe: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    blocked_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_rewrite: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow = relationship("CustomStoryWorkflowEntity", foreign_keys=[workflow_id])
