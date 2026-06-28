from __future__ import annotations

from pydantic import BaseModel, Field


class CreateSubscriptionPurchaseRequest(BaseModel):
    plan_id: str = Field(..., min_length=1, max_length=50)


class VerifySubscriptionPaymentRequest(BaseModel):
    purchase_order_id: str = Field(..., min_length=1, max_length=80)
    razorpay_payment_id: str = Field(..., min_length=1, max_length=120)
    razorpay_subscription_id: str = Field(..., min_length=1, max_length=120)
    razorpay_signature: str = Field(..., min_length=1, max_length=256)


class CancelSubscriptionRequest(BaseModel):
    cancel_type: str = Field(default="END_OF_PERIOD", max_length=40)
    reason: str | None = Field(default=None, max_length=500)
