from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SAEnum, Index, Integer, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.entity.base import TimestampMixin


class BillingCycle(str, Enum):
    TRIAL = "TRIAL"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"


class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    HALTED = "HALTED"
    FAILED = "FAILED"


class PurchaseStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class PurchaseType(str, Enum):
    FREE_TRIAL = "FREE_TRIAL"
    NEW_SUBSCRIPTION = "NEW_SUBSCRIPTION"
    RENEWAL = "RENEWAL"
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"


class PaymentStatus(str, Enum):
    CREATED = "CREATED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class PaymentType(str, Enum):
    INITIAL = "INITIAL"
    RENEWAL = "RENEWAL"
    UPGRADE = "UPGRADE"


class PaymentProvider(str, Enum):
    RAZORPAY = "RAZORPAY"


class SubscriptionPlan(TimestampMixin, Base):
    __tablename__ = "subscription_plans"
    __table_args__ = (Index("ix_subscription_plans_plan_id", "plan_id", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    billing_cycle: Mapped[BillingCycle] = mapped_column(SAEnum(BillingCycle, native_enum=False), nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    razorpay_plan_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stories_per_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audio_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pdf_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ChildSubscription(TimestampMixin, Base):
    __tablename__ = "child_subscriptions"
    __table_args__ = (
        Index("idx_child_subscription_user_child", "user_id", "child_id"),
        Index("idx_child_subscription_status", "status"),
        Index("idx_child_subscription_provider_sub", "provider_subscription_id"),
        Index(
            "ux_child_subscription_provider_sub",
            "provider_subscription_id",
            unique=True,
        ),
        Index("ix_child_subscriptions_subscription_id", "subscription_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    child_id: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_cycle: Mapped[BillingCycle] = mapped_column(SAEnum(BillingCycle, native_enum=False), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(SAEnum(SubscriptionStatus, native_enum=False), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    renewal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stories_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stories_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        Index("idx_purchase_user_child", "user_id", "child_id"),
        Index("idx_purchase_provider_sub", "provider_subscription_id"),
        Index(
            "ux_purchase_provider_sub",
            "provider_subscription_id",
            unique=True,
        ),
        Index("idx_purchase_status", "status"),
        Index("ix_purchase_orders_purchase_order_id", "purchase_order_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    purchase_order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    child_id: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False)
    purchase_type: Mapped[PurchaseType] = mapped_column(SAEnum(PurchaseType, native_enum=False), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    status: Mapped[PurchaseStatus] = mapped_column(SAEnum(PurchaseStatus, native_enum=False), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("idx_payment_user_child", "user_id", "child_id"),
        Index("idx_payment_subscription", "subscription_id"),
        Index("idx_payment_provider_payment", "provider_payment_id"),
        Index(
            "ux_payment_provider_payment",
            "provider_payment_id",
            unique=True,
        ),
        Index("idx_payment_provider_sub", "provider_subscription_id"),
        Index("ix_payments_payment_id", "payment_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(String(80), nullable=False)
    purchase_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    child_id: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    status: Mapped[PaymentStatus] = mapped_column(SAEnum(PaymentStatus, native_enum=False), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_invoice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_type: Mapped[PaymentType] = mapped_column(SAEnum(PaymentType, native_enum=False), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SubscriptionEvent(Base):
    __tablename__ = "subscription_events"
    __table_args__ = (
        Index("idx_event_type", "event_type"),
        Index("idx_event_provider_sub", "provider_subscription_id"),
        Index("ix_subscription_events_event_id", "event_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
