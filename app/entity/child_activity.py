from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class ChildActivity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only activity feed for a child."""

    __tablename__ = "child_activity_logs"
    __table_args__ = (
        Index("ix_child_activity_logs_child_id", "child_id"),
        Index("ix_child_activity_logs_activity_type", "activity_type"),
        Index("ix_child_activity_logs_occurred_at", "occurred_at"),
        Index("ix_child_activity_logs_resource", "resource_type", "resource_id"),
    )

    child_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("child_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_name: Mapped[str] = mapped_column(String(100), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resource_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(HyphenatedUUID(), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    child = relationship("ChildProfile", foreign_keys=[child_id])
