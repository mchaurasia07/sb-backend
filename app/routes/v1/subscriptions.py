from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse

from app.core.container import RequestContainer, get_request_container
from app.core.dependencies import get_current_user
from app.core.exceptions import AppException
from app.entity.user import User
from app.model.request.subscription import (
    CancelSubscriptionRequest,
    CreateSubscriptionPurchaseRequest,
    VerifySubscriptionPaymentRequest,
)
from app.model.response.common import ApiResponse, success_response
from app.model.response.subscription import (
    CancelSubscriptionResponse,
    CurrentSubscriptionResponse,
    PaidPurchaseResponse,
    PaymentHistoryItem,
    PurchaseHistoryItem,
    SubscriptionPageResponse,
    SubscriptionPlanResponse,
    SubscriptionSummaryResponse,
)

router = APIRouter()


def _authorize_path_user(current_user: User, user_id: UUID) -> None:
    if current_user.id != user_id:
        raise AppException("You cannot access another user's subscription data.", status_code=403, code="USER_FORBIDDEN")


@router.get(
    "/subscription/plans",
    response_model=ApiResponse[list[SubscriptionPlanResponse]],
)
async def get_subscription_plans(
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[list[SubscriptionPlanResponse]]:
    plans = await container.subscription.list_plans()
    return success_response(plans, "Subscription plans retrieved successfully.")


@router.post(
    "/user/{user_id}/child/{child_id}/subscription/trial",
    response_model=ApiResponse[SubscriptionSummaryResponse],
    status_code=status.HTTP_201_CREATED,
)
async def start_free_trial(
    user_id: UUID,
    child_id: UUID,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[SubscriptionSummaryResponse]:
    _authorize_path_user(current_user, user_id)
    data = await container.subscription.activate_free_trial(user_id=user_id, child_id=child_id)
    return success_response(data, "Free trial activated successfully.")


@router.post(
    "/user/{user_id}/child/{child_id}/subscription/purchase",
    response_model=ApiResponse[PaidPurchaseResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_paid_subscription_purchase(
    user_id: UUID,
    child_id: UUID,
    payload: CreateSubscriptionPurchaseRequest,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[PaidPurchaseResponse]:
    _authorize_path_user(current_user, user_id)
    data = await container.subscription.create_paid_purchase(
        user_id=user_id,
        child_id=child_id,
        plan_id=payload.plan_id,
    )
    return success_response(data, "Subscription purchase created successfully.")


@router.post(
    "/user/{user_id}/child/{child_id}/subscription/payment/verify",
    response_model=ApiResponse[SubscriptionSummaryResponse],
)
async def verify_first_payment(
    user_id: UUID,
    child_id: UUID,
    payload: VerifySubscriptionPaymentRequest,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[SubscriptionSummaryResponse]:
    _authorize_path_user(current_user, user_id)
    data = await container.subscription.verify_first_payment(
        user_id=user_id,
        child_id=child_id,
        purchase_order_id=payload.purchase_order_id,
        razorpay_payment_id=payload.razorpay_payment_id,
        razorpay_subscription_id=payload.razorpay_subscription_id,
        razorpay_signature=payload.razorpay_signature,
    )
    return success_response(data, "Subscription activated successfully.")


@router.post("/webhooks/razorpay", include_in_schema=False)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
    x_razorpay_event_id: str | None = Header(default=None, alias="X-Razorpay-Event-Id"),
    container: RequestContainer = Depends(get_request_container),
) -> JSONResponse:
    raw_body = await request.body()
    result = await container.subscription.process_razorpay_webhook(
        raw_body=raw_body,
        signature=x_razorpay_signature,
        event_id_header=x_razorpay_event_id,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"success": True, "message": "Webhook processed.", "data": result})


@router.get(
    "/user/{user_id}/child/{child_id}/subscription/current",
    response_model=ApiResponse[CurrentSubscriptionResponse],
)
async def get_current_child_subscription(
    user_id: UUID,
    child_id: UUID,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[CurrentSubscriptionResponse]:
    _authorize_path_user(current_user, user_id)
    data = await container.subscription.get_current_subscription(user_id=user_id, child_id=child_id)
    if data is None:
        return success_response(None, "No active subscription found.")
    return success_response(data, "Current subscription retrieved successfully.")


@router.get(
    "/user/{user_id}/child/{child_id}/subscription/purchases",
    response_model=ApiResponse[SubscriptionPageResponse[PurchaseHistoryItem]],
)
async def get_purchase_history(
    user_id: UUID,
    child_id: UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[SubscriptionPageResponse[PurchaseHistoryItem]]:
    _authorize_path_user(current_user, user_id)
    data = await container.subscription.list_purchases(user_id=user_id, child_id=child_id, page=page, size=size)
    return success_response(data, "Purchase history retrieved successfully.")


@router.get(
    "/user/{user_id}/child/{child_id}/payments",
    response_model=ApiResponse[SubscriptionPageResponse[PaymentHistoryItem]],
)
async def get_payment_history(
    user_id: UUID,
    child_id: UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[SubscriptionPageResponse[PaymentHistoryItem]]:
    _authorize_path_user(current_user, user_id)
    data = await container.subscription.list_payments(user_id=user_id, child_id=child_id, page=page, size=size)
    return success_response(data, "Payment history retrieved successfully.")


@router.post(
    "/user/{user_id}/child/{child_id}/subscription/{subscription_id}/cancel",
    response_model=ApiResponse[CancelSubscriptionResponse],
)
async def cancel_subscription(
    user_id: UUID,
    child_id: UUID,
    subscription_id: str,
    payload: CancelSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> ApiResponse[CancelSubscriptionResponse]:
    _authorize_path_user(current_user, user_id)
    if payload.cancel_type != "END_OF_PERIOD":
        raise AppException("Only END_OF_PERIOD cancellation is supported.", status_code=400, code="CANCEL_TYPE_UNSUPPORTED")
    data = await container.subscription.cancel_subscription(
        user_id=user_id,
        child_id=child_id,
        subscription_id=subscription_id,
        reason=payload.reason,
    )
    return success_response(data, "Subscription cancelled. Premium access will continue until the current period ends.")
