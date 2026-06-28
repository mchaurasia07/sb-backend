from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class SubscriptionFeatures(BaseModel):
    stories_per_month: int
    audio_enabled: bool
    image_enabled: bool
    pdf_enabled: bool


class SubscriptionPlanResponse(BaseModel):
    plan_id: str
    name: str
    price: Decimal
    currency: str
    duration_label: str
    billing_cycle: str
    features: SubscriptionFeatures


class SubscriptionSummaryResponse(BaseModel):
    subscription_id: str
    plan_id: str
    status: str
    start_date: datetime
    current_period_start: datetime
    current_period_end: datetime
    renewal_date: datetime | None
    expiry_date: datetime
    billing_cycle: str
    auto_renew: bool


class PaidPurchaseResponse(BaseModel):
    purchase_order_id: str
    provider: str
    razorpay_key: str
    razorpay_subscription_id: str
    amount: Decimal
    currency: str
    plan_id: str


class CurrentSubscriptionResponse(SubscriptionSummaryResponse):
    plan_name: str
    cancel_at_period_end: bool
    days_remaining: int
    stories_used: int
    stories_limit: int
    can_create_story: bool


class PurchaseHistoryItem(BaseModel):
    purchase_order_id: str
    plan_id: str
    purchase_type: str
    amount: Decimal
    currency: str
    status: str
    created_at: datetime


class PaymentHistoryItem(BaseModel):
    payment_id: str
    plan_id: str
    amount: Decimal
    currency: str
    status: str
    payment_type: str
    provider: str
    paid_at: datetime | None


class SubscriptionPageResponse(BaseModel, Generic[T]):
    page: int
    size: int
    total_records: int
    total_pages: int
    items: list[T]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CancelSubscriptionResponse(BaseModel):
    subscription_id: str
    status: str
    cancel_at_period_end: bool
    expiry_date: datetime
    auto_renew: bool
