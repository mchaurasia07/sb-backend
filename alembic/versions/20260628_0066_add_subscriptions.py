"""add child subscriptions and Razorpay billing tables

Revision ID: 20260628_0066
Revises: 20260628_0065
Create Date: 2026-06-28
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0066"
down_revision: str | None = "20260628_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("billing_cycle", sa.Enum("TRIAL", "MONTHLY", "YEARLY", native_enum=False), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("razorpay_plan_id", sa.String(length=100), nullable=True),
        sa.Column("stories_per_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("image_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("pdf_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscription_plans_plan_id", "subscription_plans", ["plan_id"], unique=True)

    op.create_table(
        "child_subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("child_id", sa.String(length=80), nullable=False),
        sa.Column("plan_id", sa.String(length=50), nullable=False),
        sa.Column("billing_cycle", sa.Enum("TRIAL", "MONTHLY", "YEARLY", native_enum=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "EXPIRED", "CANCELLED", "PENDING_PAYMENT", "PAYMENT_PENDING", "HALTED", "FAILED", native_enum=False),
            nullable=False,
        ),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewal_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("stories_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stories_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_child_subscription_provider_sub", "child_subscriptions", ["provider_subscription_id"], unique=False)
    op.create_index("idx_child_subscription_status", "child_subscriptions", ["status"], unique=False)
    op.create_index("idx_child_subscription_user_child", "child_subscriptions", ["user_id", "child_id"], unique=False)
    op.create_index("ix_child_subscriptions_subscription_id", "child_subscriptions", ["subscription_id"], unique=True)

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("purchase_order_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("child_id", sa.String(length=80), nullable=False),
        sa.Column("plan_id", sa.String(length=50), nullable=False),
        sa.Column("purchase_type", sa.Enum("FREE_TRIAL", "NEW_SUBSCRIPTION", "RENEWAL", "UPGRADE", "DOWNGRADE", native_enum=False), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("status", sa.Enum("PENDING_PAYMENT", "PAYMENT_SUCCESS", "PAYMENT_FAILED", "CANCELLED", "COMPLETED", native_enum=False), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("provider_order_id", sa.String(length=120), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_purchase_provider_sub", "purchase_orders", ["provider_subscription_id"], unique=False)
    op.create_index("idx_purchase_status", "purchase_orders", ["status"], unique=False)
    op.create_index("idx_purchase_user_child", "purchase_orders", ["user_id", "child_id"], unique=False)
    op.create_index("ix_purchase_orders_purchase_order_id", "purchase_orders", ["purchase_order_id"], unique=True)

    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.String(length=80), nullable=False),
        sa.Column("purchase_order_id", sa.String(length=80), nullable=True),
        sa.Column("subscription_id", sa.String(length=80), nullable=True),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("child_id", sa.String(length=80), nullable=False),
        sa.Column("plan_id", sa.String(length=50), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("status", sa.Enum("CREATED", "SUCCESS", "FAILED", "REFUNDED", native_enum=False), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=120), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("provider_invoice_id", sa.String(length=120), nullable=True),
        sa.Column("payment_type", sa.Enum("INITIAL", "RENEWAL", "UPGRADE", native_enum=False), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_payment_provider_payment", "payments", ["provider_payment_id"], unique=False)
    op.create_index("idx_payment_provider_sub", "payments", ["provider_subscription_id"], unique=False)
    op.create_index("idx_payment_subscription", "payments", ["subscription_id"], unique=False)
    op.create_index("idx_payment_user_child", "payments", ["user_id", "child_id"], unique=False)
    op.create_index("ix_payments_payment_id", "payments", ["payment_id"], unique=True)

    op.create_table(
        "subscription_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_event_provider_sub", "subscription_events", ["provider_subscription_id"], unique=False)
    op.create_index("idx_event_type", "subscription_events", ["event_type"], unique=False)
    op.create_index("ix_subscription_events_event_id", "subscription_events", ["event_id"], unique=True)

    now = datetime.now(UTC).replace(tzinfo=None)
    subscription_plans = sa.table(
        "subscription_plans",
        sa.column("plan_id", sa.String),
        sa.column("name", sa.String),
        sa.column("billing_cycle", sa.String),
        sa.column("duration_months", sa.Integer),
        sa.column("trial_days", sa.Integer),
        sa.column("price", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("is_paid", sa.Boolean),
        sa.column("razorpay_plan_id", sa.String),
        sa.column("stories_per_month", sa.Integer),
        sa.column("audio_enabled", sa.Boolean),
        sa.column("image_enabled", sa.Boolean),
        sa.column("pdf_enabled", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        subscription_plans,
        [
            {
                "plan_id": "FREE_TRIAL",
                "name": "Free Trial",
                "billing_cycle": "TRIAL",
                "duration_months": 0,
                "trial_days": 7,
                "price": Decimal("0.00"),
                "currency": "INR",
                "is_paid": False,
                "razorpay_plan_id": None,
                "stories_per_month": 5,
                "audio_enabled": True,
                "image_enabled": True,
                "pdf_enabled": False,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "plan_id": "MONTHLY",
                "name": "Monthly Premium",
                "billing_cycle": "MONTHLY",
                "duration_months": 1,
                "trial_days": 0,
                "price": Decimal("199.00"),
                "currency": "INR",
                "is_paid": True,
                "razorpay_plan_id": "RAZORPAY_MONTHLY_PLAN_ID",
                "stories_per_month": 20,
                "audio_enabled": True,
                "image_enabled": True,
                "pdf_enabled": True,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "plan_id": "YEARLY",
                "name": "Yearly Premium",
                "billing_cycle": "YEARLY",
                "duration_months": 12,
                "trial_days": 0,
                "price": Decimal("1999.00"),
                "currency": "INR",
                "is_paid": True,
                "razorpay_plan_id": "RAZORPAY_YEARLY_PLAN_ID",
                "stories_per_month": 20,
                "audio_enabled": True,
                "image_enabled": True,
                "pdf_enabled": True,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_subscription_events_event_id", table_name="subscription_events")
    op.drop_index("idx_event_type", table_name="subscription_events")
    op.drop_index("idx_event_provider_sub", table_name="subscription_events")
    op.drop_table("subscription_events")

    op.drop_index("ix_payments_payment_id", table_name="payments")
    op.drop_index("idx_payment_user_child", table_name="payments")
    op.drop_index("idx_payment_subscription", table_name="payments")
    op.drop_index("idx_payment_provider_sub", table_name="payments")
    op.drop_index("idx_payment_provider_payment", table_name="payments")
    op.drop_table("payments")

    op.drop_index("ix_purchase_orders_purchase_order_id", table_name="purchase_orders")
    op.drop_index("idx_purchase_user_child", table_name="purchase_orders")
    op.drop_index("idx_purchase_status", table_name="purchase_orders")
    op.drop_index("idx_purchase_provider_sub", table_name="purchase_orders")
    op.drop_table("purchase_orders")

    op.drop_index("ix_child_subscriptions_subscription_id", table_name="child_subscriptions")
    op.drop_index("idx_child_subscription_user_child", table_name="child_subscriptions")
    op.drop_index("idx_child_subscription_status", table_name="child_subscriptions")
    op.drop_index("idx_child_subscription_provider_sub", table_name="child_subscriptions")
    op.drop_table("child_subscriptions")

    op.drop_index("ix_subscription_plans_plan_id", table_name="subscription_plans")
    op.drop_table("subscription_plans")
