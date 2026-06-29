from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.entity.subscription import BillingCycle, PurchaseStatus, SubscriptionStatus
from app.model.response.subscription import PaidPurchaseResponse, PaidSubscriptionDetails
from app.service.subscription_service import SubscriptionService


def _purchase(user_id: str, child_id: str):
    return SimpleNamespace(
        purchase_order_id="PO_TEST_123",
        user_id=user_id,
        child_id=child_id,
        plan_id="MONTHLY",
        status=PurchaseStatus.PENDING_PAYMENT,
        provider_subscription_id="sub_test_123",
        metadata_json={},
    )


def _subscription(user_id: str, child_id: str):
    now = datetime.now(UTC)
    return SimpleNamespace(
        subscription_id="SUB_LOCAL_123",
        user_id=user_id,
        child_id=child_id,
        plan_id="MONTHLY",
        billing_cycle=BillingCycle.MONTHLY,
        status=SubscriptionStatus.ACTIVE,
        start_date=now,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        renewal_date=now + timedelta(days=30),
        expiry_date=now + timedelta(days=30),
        auto_renew=True,
    )


def _provider_subscription(purchase, status: str):
    return {
        "id": purchase.provider_subscription_id,
        "status": status,
        "expire_by": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        "notes": {
            "purchase_order_id": purchase.purchase_order_id,
            "user_id": purchase.user_id,
            "child_id": purchase.child_id,
            "app_plan_id": purchase.plan_id,
        },
    }


def _service(razorpay):
    session = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    return SubscriptionService(session, razorpay=razorpay), session


def test_paid_purchase_response_has_only_the_new_contract():
    response = PaidPurchaseResponse(
        purchase_id="PO_TEST_123",
        subscription=PaidSubscriptionDetails(
            subscription_id="sub_test_123",
            plan="MONTHLY",
            status="created",
            payment_url="https://rzp.io/rzp/test",
            expires_at=None,
        ),
    )

    assert response.model_dump() == {
        "purchase_type": "subscription",
        "purchase_id": "PO_TEST_123",
        "subscription": {
            "subscription_id": "sub_test_123",
            "plan": "MONTHLY",
            "status": "created",
            "payment_url": "https://rzp.io/rzp/test",
            "expires_at": None,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "metadata", "expected_status", "can_retry"),
    [
        ("created", {}, "PENDING", True),
        ("authenticated", {}, "PENDING", True),
        ("pending", {}, "PENDING", True),
        (
            "created",
            {"latest_initial_payment_failure": {"reason": "declined"}},
            "FAILED",
            True,
        ),
        ("halted", {}, "FAILED", False),
        ("cancelled", {}, "FAILED", False),
        ("expired", {}, "EXPIRED", False),
    ],
)
async def test_reconcile_maps_non_success_provider_states(
    provider_status, metadata, expected_status, can_retry
):
    user_id = uuid4()
    child_id = uuid4()
    purchase = _purchase(str(user_id), str(child_id))
    purchase.metadata_json = metadata
    razorpay = SimpleNamespace(
        fetch_subscription=AsyncMock(
            return_value=_provider_subscription(purchase, provider_status)
        ),
        fetch_subscription_invoices=AsyncMock(return_value=[]),
    )
    service, session = _service(razorpay)
    service._get_purchase_order = AsyncMock(return_value=purchase)
    service._get_by_provider_subscription_id = AsyncMock(return_value=None)

    result = await service.reconcile_paid_purchase(
        user_id=user_id,
        child_id=child_id,
        purchase_order_id=purchase.purchase_order_id,
    )

    assert result.status == expected_status
    assert result.can_retry is can_retry
    assert result.subscription is None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_paid_invoice_activates_once():
    user_id = uuid4()
    child_id = uuid4()
    purchase = _purchase(str(user_id), str(child_id))
    local_subscription = _subscription(str(user_id), str(child_id))
    provider = _provider_subscription(purchase, "active")
    provider.update(
        {
            "current_start": int(datetime.now(UTC).timestamp()),
            "current_end": int(
                (datetime.now(UTC) + timedelta(days=30)).timestamp()
            ),
        }
    )
    invoice = {
        "id": "inv_test_123",
        "status": "paid",
        "payment_id": "pay_test_123",
        "amount_paid": 19900,
        "currency": "INR",
        "paid_at": int(datetime.now(UTC).timestamp()),
    }
    razorpay = SimpleNamespace(
        fetch_subscription=AsyncMock(return_value=provider),
        fetch_subscription_invoices=AsyncMock(return_value=[invoice]),
    )
    service, session = _service(razorpay)
    service._get_purchase_order = AsyncMock(return_value=purchase)
    service._get_by_provider_subscription_id = AsyncMock(return_value=None)
    service._activate_initial_paid_subscription = AsyncMock(
        return_value=local_subscription
    )

    result = await service.reconcile_paid_purchase(
        user_id=user_id,
        child_id=child_id,
        purchase_order_id=purchase.purchase_order_id,
    )

    assert result.status == "SUCCESS"
    assert result.subscription.subscription_id == local_subscription.subscription_id
    service._activate_initial_paid_subscription.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_purchase_returns_without_calling_razorpay():
    user_id = uuid4()
    child_id = uuid4()
    purchase = _purchase(str(user_id), str(child_id))
    purchase.status = PurchaseStatus.COMPLETED
    local_subscription = _subscription(str(user_id), str(child_id))
    razorpay = SimpleNamespace(
        fetch_subscription=AsyncMock(),
        fetch_subscription_invoices=AsyncMock(),
    )
    service, _session = _service(razorpay)
    service._get_purchase_order = AsyncMock(return_value=purchase)
    service._get_by_provider_subscription_id = AsyncMock(
        return_value=local_subscription
    )

    result = await service.reconcile_paid_purchase(
        user_id=user_id,
        child_id=child_id,
        purchase_order_id=purchase.purchase_order_id,
    )

    assert result.status == "SUCCESS"
    razorpay.fetch_subscription.assert_not_awaited()
    razorpay.fetch_subscription_invoices.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_does_not_mutate_purchase():
    user_id = uuid4()
    child_id = uuid4()
    purchase = _purchase(str(user_id), str(child_id))
    provider_error = AppException(
        "Razorpay unavailable",
        status_code=502,
        code="RAZORPAY_SUBSCRIPTION_FETCH_FAILED",
    )
    razorpay = SimpleNamespace(
        fetch_subscription=AsyncMock(side_effect=provider_error),
        fetch_subscription_invoices=AsyncMock(),
    )
    service, session = _service(razorpay)
    service._get_purchase_order = AsyncMock(return_value=purchase)
    service._get_by_provider_subscription_id = AsyncMock(return_value=None)

    with pytest.raises(AppException) as error:
        await service.reconcile_paid_purchase(
            user_id=user_id,
            child_id=child_id,
            purchase_order_id=purchase.purchase_order_id,
        )

    assert error.value.code == "RAZORPAY_SUBSCRIPTION_FETCH_FAILED"
    assert purchase.status == PurchaseStatus.PENDING_PAYMENT
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_observes_webhook_completion_after_row_lock():
    user_id = uuid4()
    child_id = uuid4()
    initial_purchase = _purchase(str(user_id), str(child_id))
    completed_purchase = _purchase(str(user_id), str(child_id))
    completed_purchase.status = PurchaseStatus.COMPLETED
    local_subscription = _subscription(str(user_id), str(child_id))
    invoice = {
        "id": "inv_test_123",
        "status": "paid",
        "payment_id": "pay_test_123",
        "paid_at": int(datetime.now(UTC).timestamp()),
    }
    razorpay = SimpleNamespace(
        fetch_subscription=AsyncMock(
            return_value=_provider_subscription(initial_purchase, "active")
        ),
        fetch_subscription_invoices=AsyncMock(return_value=[invoice]),
    )
    service, session = _service(razorpay)
    service._get_purchase_order = AsyncMock(
        side_effect=[initial_purchase, completed_purchase]
    )
    service._get_by_provider_subscription_id = AsyncMock(
        side_effect=[None, local_subscription]
    )
    service._activate_initial_paid_subscription = AsyncMock()

    result = await service.reconcile_paid_purchase(
        user_id=user_id,
        child_id=child_id,
        purchase_order_id=initial_purchase.purchase_order_id,
    )

    assert result.status == "SUCCESS"
    service._activate_initial_paid_subscription.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_rejects_purchase_owned_by_another_child():
    user_id = uuid4()
    child_id = uuid4()
    purchase = _purchase(str(user_id), str(uuid4()))
    razorpay = SimpleNamespace(
        fetch_subscription=AsyncMock(),
        fetch_subscription_invoices=AsyncMock(),
    )
    service, _session = _service(razorpay)
    service._get_purchase_order = AsyncMock(return_value=purchase)

    with pytest.raises(AppException) as error:
        await service.reconcile_paid_purchase(
            user_id=user_id,
            child_id=child_id,
            purchase_order_id=purchase.purchase_order_id,
        )

    assert error.value.code == "PURCHASE_FORBIDDEN"
    razorpay.fetch_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_charged_webhook_creates_subscription_from_purchase():
    user_id = uuid4()
    child_id = uuid4()
    purchase = _purchase(str(user_id), str(child_id))
    razorpay = SimpleNamespace()
    service, session = _service(razorpay)
    service._get_by_provider_subscription_id = AsyncMock(return_value=None)
    service._get_purchase_by_provider_subscription = AsyncMock(
        return_value=purchase
    )
    service._get_purchase_order = AsyncMock(return_value=purchase)
    service._activate_initial_paid_subscription = AsyncMock()
    payload = {
        "payload": {
            "subscription": {
                "entity": {
                    "id": purchase.provider_subscription_id,
                    "status": "active",
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_test_123",
                    "amount": 19900,
                    "currency": "INR",
                    "created_at": int(datetime.now(UTC).timestamp()),
                }
            },
            "invoice": {"entity": {"id": "inv_test_123"}},
        }
    }

    await service._webhook_subscription_charged(
        payload,
        purchase.provider_subscription_id,
        "pay_test_123",
    )

    service._activate_initial_paid_subscription.assert_awaited_once()
    session.flush.assert_awaited_once()
