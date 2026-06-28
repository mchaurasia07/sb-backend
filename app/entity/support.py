from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.entity.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.entity.types import HyphenatedUUID


class SupportQueryStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESPONDED = "RESPONDED"
    CLOSED = "CLOSED"


class SupportMessageSender(str, Enum):
    USER = "USER"
    SUPPORT = "SUPPORT"
    JUGNI = "JUGNI"


def _public_id(prefix: str) -> str:
    return f"{prefix}{int(uuid4()) % 10_000_000_000_000:013d}"


class SupportQuery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_queries"
    __table_args__ = (
        Index("ix_support_queries_query_id", "query_id", unique=True),
        Index("ix_support_queries_user_updated", "user_id", "updated_at"),
        Index("ix_support_queries_status", "status"),
        Index(
            "ix_support_queries_pending_status_created",
            "pending_at_jugni",
            "pending_at_user",
            "status",
            "created_at",
        ),
    )

    query_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default=lambda: _public_id("QRY_")
    )
    user_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SupportQueryStatus] = mapped_column(
        SAEnum(SupportQueryStatus, native_enum=False),
        nullable=False,
        default=SupportQueryStatus.OPEN,
    )
    pending_at_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pending_at_jugni: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="query",
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at",
    )


class SupportMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        Index("ix_support_messages_message_id", "message_id", unique=True),
        Index("ix_support_messages_query_created", "support_query_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default=lambda: _public_id("MSG")
    )
    support_query_id: Mapped[UUID] = mapped_column(
        HyphenatedUUID(),
        ForeignKey("support_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender: Mapped[SupportMessageSender] = mapped_column(
        SAEnum(SupportMessageSender, native_enum=False), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    query: Mapped[SupportQuery] = relationship(back_populates="messages")
