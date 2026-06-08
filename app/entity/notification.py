from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class NotificationAccountType(str, Enum):
    PARENT = "parent"
    CHILD = "child"


class NotificationAudience(str, Enum):
    ALL = "all"
    PARENTS = "parents"
    CHILDREN = "children"
    PARENT_USER = "parent_user"
    CHILD = "child"


class NotificationDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class PushDeviceToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Expo push token registered by a parent or child device."""

    __tablename__ = "push_device_tokens"
    __table_args__ = (
        Index("ix_push_device_tokens_token", "expo_push_token", unique=True),
        Index("ix_push_device_tokens_user_id", "user_id"),
        Index("ix_push_device_tokens_child_id", "child_id"),
        Index("ix_push_device_tokens_account_type", "account_type"),
        Index("ix_push_device_tokens_active", "active"),
    )

    user_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    child_id: Mapped[UUID | None] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("child_profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    account_type: Mapped[NotificationAccountType] = mapped_column(
        SAEnum(NotificationAccountType, native_enum=False),
        nullable=False,
    )

    expo_push_token: Mapped[str] = mapped_column(String(255), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit log for push notifications sent through Expo."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_event_type", "event_type"),
        Index("ix_notifications_audience", "audience"),
        Index("ix_notifications_status", "status"),
        Index("ix_notifications_user_id", "user_id"),
        Index("ix_notifications_child_id", "child_id"),
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    audience: Mapped[NotificationAudience] = mapped_column(
        SAEnum(NotificationAudience, native_enum=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user_id: Mapped[UUID | None] = mapped_column(HyphenatedUUID(), nullable=True)
    child_id: Mapped[UUID | None] = mapped_column(HyphenatedUUID(), nullable=True)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        SAEnum(NotificationDeliveryStatus, native_enum=False),
        nullable=False,
        default=NotificationDeliveryStatus.PENDING,
    )
    target_count: Mapped[int] = mapped_column(default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(default=0, nullable=False)
    tickets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
