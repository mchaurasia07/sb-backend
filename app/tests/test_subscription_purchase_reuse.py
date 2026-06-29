from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.entity.subscription import BillingCycle, PurchaseStatus
from app.service.subscription_service import SubscriptionService


@pytest.mark.asyncio
async def test_create_paid_purchase_reuses_matching_pending_purchase():
    user_id = uuid4()
    child_id = uuid4()
    provider_payload = {
        "id": "sub_existing_123",
        "status": "created",
        "short_url": "https://rzp.io/rzp/existing",
        "expire_by": None,
    }
    pending_purchase = SimpleNamespace(
        purchase_order_id="PO_EXISTING_123",
        provider_subscription_id="sub_existing_123",
        plan_id="MONTHLY",
        status=PurchaseStatus.PENDING_PAYMENT,
        metadata_json={"razorpay_subscription": provider_payload},
    )
    plan = SimpleNamespace(
        plan_id="MONTHLY",
        is_paid=True,
        billing_cycle=BillingCycle.MONTHLY,
        price=Decimal("199.00"),
        currency="INR",
    )
    session = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    razorpay = SimpleNamespace(create_subscription=AsyncMock())
    service = SubscriptionService(session, razorpay=razorpay)
    service.ensure_default_plans = AsyncMock()
    service._ensure_child = AsyncMock()
    service._get_plan = AsyncMock(return_value=plan)
    service._get_current_subscription = AsyncMock(return_value=None)
    service._get_pending_paid_purchase = AsyncMock(
        return_value=pending_purchase
    )

    response = await service.create_paid_purchase(
        user_id=user_id,
        child_id=child_id,
        plan_id="MONTHLY",
    )

    assert response.model_dump() == {
        "purchase_type": "subscription",
        "purchase_id": "PO_EXISTING_123",
        "subscription": {
            "subscription_id": "sub_existing_123",
            "plan": "MONTHLY",
            "status": "PENDING_PAYMENT",
            "payment_url": "https://rzp.io/rzp/existing",
            "expires_at": None,
        },
    }
    service._get_pending_paid_purchase.assert_awaited_once_with(
        user_key=str(user_id),
        child_key=str(child_id),
        plan_id="MONTHLY",
        for_update=True,
    )
    razorpay.create_subscription.assert_not_awaited()
    session.add.assert_not_called()
