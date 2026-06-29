from __future__ import annotations

import hashlib
import json
from datetime import UTC
from decimal import Decimal
from math import ceil
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.core.logger import get_logger
from app.entity.subscription import (
    BillingCycle,
    ChildSubscription,
    Payment,
    PaymentProvider,
    PaymentStatus,
    PaymentType,
    PurchaseOrder,
    PurchaseStatus,
    PurchaseType,
    SubscriptionEvent,
    SubscriptionPlan,
    SubscriptionStatus,
)
from app.model.response.subscription import (
    CancelSubscriptionResponse,
    CurrentSubscriptionResponse,
    PaidPurchaseResponse,
    PaidSubscriptionDetails,
    PaymentHistoryItem,
    PurchaseHistoryItem,
    SubscriptionFeatures,
    SubscriptionPageResponse,
    SubscriptionPaymentVerificationResponse,
    SubscriptionPlanResponse,
    SubscriptionSummaryResponse,
)
from app.repository.child_repository import ChildRepository
from app.service.razorpay_service import RazorpayService
from app.utils.datetime_utils import calculate_period_end, ensure_utc, utc_now
from app.utils.id_utils import generate_payment_id, generate_purchase_order_id, generate_subscription_id

logger = get_logger(__name__)

ENTITLEMENT_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAYMENT_PENDING,
    SubscriptionStatus.HALTED,
)


DEFAULT_PLAN_ROWS: tuple[dict[str, Any], ...] = (
    {
        "plan_id": "FREE_TRIAL",
        "name": "Free Trial",
        "billing_cycle": BillingCycle.TRIAL,
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
    },
    {
        "plan_id": "MONTHLY",
        "name": "Monthly Premium",
        "billing_cycle": BillingCycle.MONTHLY,
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
    },
    {
        "plan_id": "YEARLY",
        "name": "Yearly Premium",
        "billing_cycle": BillingCycle.YEARLY,
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
    },
)


class SubscriptionService:
    def __init__(self, session: AsyncSession, razorpay: RazorpayService | None = None):
        self.session = session
        self.children = ChildRepository(session)
        self.razorpay = razorpay or RazorpayService()

    async def list_plans(self) -> list[SubscriptionPlanResponse]:
        await self.ensure_default_plans()
        result = await self.session.execute(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.id)
        )
        return [self._plan_response(plan) for plan in result.scalars().all()]

    async def activate_free_trial(self, *, user_id: UUID, child_id: UUID) -> SubscriptionSummaryResponse:
        await self.ensure_default_plans()
        await self._ensure_child(user_id, child_id)
        user_key, child_key = self._keys(user_id, child_id)
        if await self._has_used_free_trial(user_key=user_key, child_key=child_key):
            raise ConflictException(
                "Free trial has already been used for this child.",
                status_code=409,
                code="FREE_TRIAL_ALREADY_USED",
            )
        if await self._get_current_subscription(user_key=user_key, child_key=child_key):
            raise ConflictException(
                "Child already has an active subscription.",
                status_code=409,
                code="SUBSCRIPTION_ALREADY_ACTIVE",
            )
        plan = await self._get_plan("FREE_TRIAL")
        now = utc_now()
        period_end = calculate_period_end(now, plan)
        purchase = PurchaseOrder(
            purchase_order_id=generate_purchase_order_id(),
            user_id=user_key,
            child_id=child_key,
            plan_id=plan.plan_id,
            purchase_type=PurchaseType.FREE_TRIAL,
            amount=Decimal("0.00"),
            currency=plan.currency,
            status=PurchaseStatus.COMPLETED,
            metadata_json={"source": "free_trial"},
        )
        subscription = ChildSubscription(
            subscription_id=generate_subscription_id("SUB_TRIAL_"),
            user_id=user_key,
            child_id=child_key,
            plan_id=plan.plan_id,
            billing_cycle=plan.billing_cycle,
            status=SubscriptionStatus.ACTIVE,
            start_date=now,
            current_period_start=now,
            current_period_end=period_end,
            renewal_date=None,
            expiry_date=period_end,
            auto_renew=False,
            provider=None,
            stories_used=0,
            stories_limit=plan.stories_per_month,
        )
        self.session.add_all([purchase, subscription])
        await self.session.commit()
        await self.session.refresh(subscription)
        return self._subscription_summary(subscription)

    async def create_paid_purchase(
        self,
        *,
        user_id: UUID,
        child_id: UUID,
        plan_id: str,
    ) -> PaidPurchaseResponse:
        await self.ensure_default_plans()
        await self._ensure_child(user_id, child_id)
        plan = await self._get_plan(plan_id.upper())
        if not plan.is_paid:
            raise AppException(
                "Use the trial endpoint to activate the free trial.",
                status_code=400,
                code="FREE_TRIAL_NOT_ALLOWED_FOR_PURCHASE",
            )
        user_key, child_key = self._keys(user_id, child_id)
        active_subscription = await self._get_current_subscription(user_key=user_key, child_key=child_key)
        if active_subscription is not None and active_subscription.plan_id != "FREE_TRIAL":
            raise ConflictException(
                "Child already has an active subscription.",
                status_code=409,
                code="SUBSCRIPTION_ALREADY_ACTIVE",
            )

        pending_purchase = await self._get_pending_paid_purchase(
            user_key=user_key,
            child_key=child_key,
            plan_id=plan.plan_id,
            for_update=True,
        )
        if pending_purchase is not None:
            subscription_payload = (pending_purchase.metadata_json or {}).get(
                "razorpay_subscription"
            )
            if not isinstance(subscription_payload, dict):
                raise AppException(
                    "Pending purchase is missing its Razorpay subscription details.",
                    status_code=409,
                    code="PENDING_PURCHASE_SUBSCRIPTION_MISSING",
                )
            provider_subscription_id = (
                pending_purchase.provider_subscription_id
                or subscription_payload.get("id")
            )
            payment_url = subscription_payload.get("short_url")
            if not provider_subscription_id or not payment_url:
                raise AppException(
                    "Pending purchase has incomplete Razorpay subscription details.",
                    status_code=409,
                    code="PENDING_PURCHASE_SUBSCRIPTION_INCOMPLETE",
                )
            return PaidPurchaseResponse(
                purchase_id=pending_purchase.purchase_order_id,
                subscription=PaidSubscriptionDetails(
                    subscription_id=provider_subscription_id,
                    plan=pending_purchase.plan_id,
                    status=PurchaseStatus.PENDING_PAYMENT.value,
                    payment_url=payment_url,
                    expires_at=subscription_payload.get("expire_by"),
                ),
            )

        razorpay_plan_id = self._effective_razorpay_plan_id(plan)
        if not razorpay_plan_id:
            raise AppException(
                "Razorpay plan ID is not configured for this subscription plan.",
                status_code=503,
                code="RAZORPAY_PLAN_NOT_CONFIGURED",
            )
        purchase = PurchaseOrder(
            purchase_order_id=generate_purchase_order_id(),
            user_id=user_key,
            child_id=child_key,
            plan_id=plan.plan_id,
            purchase_type=PurchaseType.NEW_SUBSCRIPTION,
            amount=plan.price,
            currency=plan.currency,
            status=PurchaseStatus.PENDING_PAYMENT,
            provider=PaymentProvider.RAZORPAY.value,
            metadata_json={"source": "subscription_purchase"},
        )
        self.session.add(purchase)
        await self.session.flush()
        subscription_payload = await self.razorpay.create_subscription(
            razorpay_plan_id=razorpay_plan_id,
            plan_id=plan.plan_id,
            user_id=user_key,
            child_id=child_key,
            purchase_order_id=purchase.purchase_order_id,
            total_count=60 if plan.billing_cycle == BillingCycle.MONTHLY else 60,
        )
        provider_subscription_id = subscription_payload.get("id")
        if not provider_subscription_id:
            raise AppException(
                "Razorpay did not return a subscription ID.",
                status_code=502,
                code="RAZORPAY_SUBSCRIPTION_ID_MISSING",
            )
        purchase.provider_subscription_id = provider_subscription_id
        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "razorpay_subscription": subscription_payload,
        }
        await self.session.commit()
        return PaidPurchaseResponse(
            purchase_id=purchase.purchase_order_id,
            subscription=PaidSubscriptionDetails(
                subscription_id=provider_subscription_id,
                plan=plan.plan_id,
                status=subscription_payload["status"],
                payment_url=subscription_payload["short_url"],
                expires_at=subscription_payload.get("expire_by"),
            ),
        )

    async def verify_first_payment(
        self,
        *,
        user_id: UUID,
        child_id: UUID,
        purchase_order_id: str,
        razorpay_payment_id: str,
        razorpay_subscription_id: str,
        razorpay_signature: str,
    ) -> SubscriptionSummaryResponse:
        if not self.razorpay.verify_subscription_payment_signature(
            razorpay_payment_id=razorpay_payment_id,
            razorpay_subscription_id=razorpay_subscription_id,
            razorpay_signature=razorpay_signature,
        ):
            raise AppException("Invalid Razorpay payment signature.", status_code=400, code="INVALID_RAZORPAY_SIGNATURE")

        user_key, child_key = self._keys(user_id, child_id)
        purchase = await self._get_purchase_order(purchase_order_id)
        if purchase.user_id != user_key or purchase.child_id != child_key:
            raise AppException("Purchase order does not belong to this child.", status_code=403, code="PURCHASE_FORBIDDEN")
        if purchase.provider_subscription_id != razorpay_subscription_id:
            raise AppException("Razorpay subscription mismatch.", status_code=400, code="RAZORPAY_SUBSCRIPTION_MISMATCH")
        if purchase.status == PurchaseStatus.COMPLETED:
            existing = await self._get_by_provider_subscription_id(razorpay_subscription_id)
            if existing is not None:
                return self._subscription_summary(existing)
        if purchase.status not in (PurchaseStatus.PENDING_PAYMENT, PurchaseStatus.PAYMENT_SUCCESS):
            raise ConflictException("Purchase order cannot be verified in its current state.", status_code=409, code="PURCHASE_NOT_VERIFYABLE")

        plan = await self._get_plan(purchase.plan_id)
        if await self._get_current_subscription(user_key=user_key, child_key=child_key):
            raise ConflictException("Child already has an active subscription.", status_code=409, code="SUBSCRIPTION_ALREADY_ACTIVE")

        now = utc_now()
        period_end = calculate_period_end(now, plan)
        subscription = ChildSubscription(
            subscription_id=generate_subscription_id(),
            user_id=user_key,
            child_id=child_key,
            plan_id=plan.plan_id,
            billing_cycle=plan.billing_cycle,
            status=SubscriptionStatus.ACTIVE,
            start_date=now,
            current_period_start=now,
            current_period_end=period_end,
            renewal_date=period_end,
            expiry_date=period_end,
            auto_renew=True,
            provider=PaymentProvider.RAZORPAY.value,
            provider_subscription_id=razorpay_subscription_id,
            stories_used=0,
            stories_limit=plan.stories_per_month,
        )
        self.session.add(subscription)
        await self.session.flush()
        await self._create_payment_if_absent(
            purchase_order_id=purchase.purchase_order_id,
            subscription_id=subscription.subscription_id,
            user_id=user_key,
            child_id=child_key,
            plan_id=plan.plan_id,
            amount=plan.price,
            currency=plan.currency,
            status=PaymentStatus.SUCCESS,
            payment_type=PaymentType.INITIAL,
            provider_payment_id=razorpay_payment_id,
            provider_subscription_id=razorpay_subscription_id,
            paid_at=now,
        )
        purchase.status = PurchaseStatus.COMPLETED
        await self.session.commit()
        await self.session.refresh(subscription)
        return self._subscription_summary(subscription)

    async def reconcile_paid_purchase(
        self,
        *,
        user_id: UUID,
        child_id: UUID,
        purchase_order_id: str,
    ) -> SubscriptionPaymentVerificationResponse:
        user_key, child_key = self._keys(user_id, child_id)
        purchase = await self._get_purchase_order(purchase_order_id)
        if purchase.user_id != user_key or purchase.child_id != child_key:
            raise AppException(
                "Purchase order does not belong to this child.",
                status_code=403,
                code="PURCHASE_FORBIDDEN",
            )
        provider_subscription_id = purchase.provider_subscription_id
        if not provider_subscription_id:
            raise AppException(
                "Purchase order is missing its Razorpay subscription.",
                status_code=409,
                code="PURCHASE_PROVIDER_SUBSCRIPTION_MISSING",
            )

        existing = await self._get_by_provider_subscription_id(provider_subscription_id)
        if purchase.status == PurchaseStatus.COMPLETED and existing is not None:
            return self._payment_verification_response(
                purchase=purchase,
                status="SUCCESS",
                provider_status="active",
                can_retry=False,
                subscription=existing,
            )

        provider_subscription = await self.razorpay.fetch_subscription(
            provider_subscription_id
        )
        if provider_subscription.get("id") != provider_subscription_id:
            raise AppException(
                "Razorpay returned a mismatched subscription.",
                status_code=502,
                code="RAZORPAY_SUBSCRIPTION_MISMATCH",
            )
        notes = provider_subscription.get("notes")
        if isinstance(notes, dict):
            expected_notes = {
                "purchase_order_id": purchase.purchase_order_id,
                "user_id": user_key,
                "child_id": child_key,
                "app_plan_id": purchase.plan_id,
            }
            if any(str(notes.get(key)) != value for key, value in expected_notes.items()):
                raise AppException(
                    "Razorpay subscription ownership could not be confirmed.",
                    status_code=409,
                    code="RAZORPAY_SUBSCRIPTION_OWNERSHIP_MISMATCH",
                )

        provider_status = str(provider_subscription.get("status") or "unknown").lower()
        invoices = await self.razorpay.fetch_subscription_invoices(
            provider_subscription_id
        )
        paid_invoices = [
            invoice
            for invoice in invoices
            if invoice.get("status") == "paid" and invoice.get("payment_id")
        ]
        paid_invoice = max(
            paid_invoices,
            key=lambda invoice: int(invoice.get("paid_at") or invoice.get("date") or 0),
            default=None,
        )

        # Re-lock after provider I/O. Both webhook and interactive verification
        # serialize on this row before creating financial records.
        purchase = await self._get_purchase_order(purchase_order_id, for_update=True)
        existing = await self._get_by_provider_subscription_id(provider_subscription_id)
        if purchase.status == PurchaseStatus.COMPLETED and existing is not None:
            return self._payment_verification_response(
                purchase=purchase,
                status="SUCCESS",
                provider_status=provider_status,
                can_retry=False,
                subscription=existing,
            )

        if provider_status in {"active", "completed"} and paid_invoice is not None:
            subscription = await self._activate_initial_paid_subscription(
                purchase=purchase,
                provider_subscription=provider_subscription,
                paid_invoice=paid_invoice,
            )
            await self.session.commit()
            logger.info(
                "subscription_purchase_reconciled",
                purchase_id=purchase.purchase_order_id,
                provider_subscription_id=provider_subscription_id,
                provider_status=provider_status,
                result="SUCCESS",
            )
            return self._payment_verification_response(
                purchase=purchase,
                status="SUCCESS",
                provider_status=provider_status,
                can_retry=False,
                subscription=subscription,
            )

        expires_at = provider_subscription.get("expire_by")
        expired_by_time = (
            isinstance(expires_at, int)
            and self._timestamp_from_razorpay(expires_at) <= utc_now()
        )
        if provider_status == "expired" or expired_by_time:
            purchase.status = PurchaseStatus.PAYMENT_FAILED
            result_status = "EXPIRED"
            can_retry = False
        elif provider_status in {"halted", "cancelled"}:
            purchase.status = PurchaseStatus.PAYMENT_FAILED
            result_status = "FAILED"
            can_retry = False
        else:
            latest_failure = (purchase.metadata_json or {}).get(
                "latest_initial_payment_failure"
            )
            result_status = "FAILED" if latest_failure else "PENDING"
            can_retry = True

        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "last_reconciliation": {
                "provider_status": provider_status,
                "invoice_count": len(invoices),
                "result": result_status,
            },
        }
        await self.session.commit()
        logger.info(
            "subscription_purchase_reconciled",
            purchase_id=purchase.purchase_order_id,
            provider_subscription_id=provider_subscription_id,
            provider_status=provider_status,
            result=result_status,
        )
        return self._payment_verification_response(
            purchase=purchase,
            status=result_status,
            provider_status=provider_status,
            can_retry=can_retry,
            subscription=None,
        )

    async def process_razorpay_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str | None,
        event_id_header: str | None = None,
    ) -> dict[str, Any]:
        if not self.razorpay.verify_webhook_signature(raw_body=raw_body, signature=signature):
            raise AppException("Invalid Razorpay webhook signature.", status_code=400, code="INVALID_RAZORPAY_WEBHOOK_SIGNATURE")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AppException("Invalid webhook payload.", status_code=400, code="INVALID_WEBHOOK_PAYLOAD") from exc

        event_type = payload.get("event") or payload.get("event_type") or "unknown"
        event_id = (
            event_id_header
            or payload.get("id")
            or payload.get("event_id")
            or hashlib.sha256(raw_body).hexdigest()
        )
        provider_subscription_id = self._payload_subscription_id(payload)
        provider_payment_id = self._payload_payment_id(payload)

        existing = await self._get_event(event_id)
        if existing and existing.processed:
            return {"processed": False, "duplicate": True, "event_id": event_id}
        event = existing or SubscriptionEvent(
            provider=PaymentProvider.RAZORPAY.value,
            event_id=event_id,
            event_type=event_type,
            provider_subscription_id=provider_subscription_id,
            provider_payment_id=provider_payment_id,
            payload=payload,
            processed=False,
            created_at=utc_now(),
        )
        if existing is None:
            self.session.add(event)
            await self.session.flush()
        try:
            await self._dispatch_webhook_event(event_type, payload, provider_subscription_id, provider_payment_id)
            event.processed = True
            event.processed_at = utc_now()
            event.error_message = None
            await self.session.commit()
        except Exception as exc:
            event.error_message = str(exc)[:1000]
            await self.session.commit()
            logger.exception("razorpay_webhook_processing_failed", event_id=event_id, event_type=event_type)
            raise
        return {"processed": True, "duplicate": False, "event_id": event_id}

    async def get_current_subscription(self, *, user_id: UUID, child_id: UUID) -> CurrentSubscriptionResponse | None:
        await self._ensure_child(user_id, child_id)
        user_key, child_key = self._keys(user_id, child_id)
        subscription = await self._get_current_subscription(user_key=user_key, child_key=child_key)
        if subscription is None:
            return None
        plan = await self._get_plan(subscription.plan_id)
        now = utc_now()
        expiry = ensure_utc(subscription.expiry_date)
        remaining_seconds = max(0.0, (expiry - now).total_seconds())
        return CurrentSubscriptionResponse(
            **self._subscription_summary(subscription).model_dump(),
            plan_name=plan.name,
            cancel_at_period_end=subscription.cancel_at_period_end,
            days_remaining=ceil(remaining_seconds / 86400) if remaining_seconds else 0,
            stories_used=subscription.stories_used,
            stories_limit=subscription.stories_limit,
            can_create_story=self._can_use_subscription(subscription, now),
        )

    async def list_purchases(
        self, *, user_id: UUID, child_id: UUID, page: int, size: int
    ) -> SubscriptionPageResponse[PurchaseHistoryItem]:
        await self._ensure_child(user_id, child_id)
        user_key, child_key = self._keys(user_id, child_id)
        total = await self.session.scalar(
            select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.user_id == user_key, PurchaseOrder.child_id == child_key)
        )
        result = await self.session.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.user_id == user_key, PurchaseOrder.child_id == child_key)
            .order_by(PurchaseOrder.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return SubscriptionPageResponse(
            page=page,
            size=size,
            total_records=int(total or 0),
            total_pages=ceil((total or 0) / size) if total else 0,
            items=[
                PurchaseHistoryItem(
                    purchase_order_id=item.purchase_order_id,
                    plan_id=item.plan_id,
                    purchase_type=item.purchase_type.value,
                    amount=item.amount,
                    currency=item.currency,
                    status=item.status.value,
                    created_at=item.created_at,
                )
                for item in result.scalars().all()
            ],
        )

    async def list_payments(
        self, *, user_id: UUID, child_id: UUID, page: int, size: int
    ) -> SubscriptionPageResponse[PaymentHistoryItem]:
        await self._ensure_child(user_id, child_id)
        user_key, child_key = self._keys(user_id, child_id)
        total = await self.session.scalar(
            select(func.count()).select_from(Payment).where(Payment.user_id == user_key, Payment.child_id == child_key)
        )
        result = await self.session.execute(
            select(Payment)
            .where(Payment.user_id == user_key, Payment.child_id == child_key)
            .order_by(Payment.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return SubscriptionPageResponse(
            page=page,
            size=size,
            total_records=int(total or 0),
            total_pages=ceil((total or 0) / size) if total else 0,
            items=[
                PaymentHistoryItem(
                    payment_id=item.payment_id,
                    plan_id=item.plan_id,
                    amount=item.amount,
                    currency=item.currency,
                    status=item.status.value,
                    payment_type=item.payment_type.value,
                    provider=item.provider,
                    paid_at=item.paid_at,
                )
                for item in result.scalars().all()
            ],
        )

    async def cancel_subscription(
        self,
        *,
        user_id: UUID,
        child_id: UUID,
        subscription_id: str,
        reason: str | None,
    ) -> CancelSubscriptionResponse:
        await self._ensure_child(user_id, child_id)
        user_key, child_key = self._keys(user_id, child_id)
        result = await self.session.execute(
            select(ChildSubscription).where(
                ChildSubscription.subscription_id == subscription_id,
                ChildSubscription.user_id == user_key,
                ChildSubscription.child_id == child_key,
            )
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise NotFoundException("Subscription not found.", code="SUBSCRIPTION_NOT_FOUND")
        if subscription.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAYMENT_PENDING):
            raise ConflictException("Subscription cannot be cancelled in its current state.", status_code=409, code="SUBSCRIPTION_NOT_CANCELLABLE")
        if subscription.provider == PaymentProvider.RAZORPAY.value and subscription.provider_subscription_id:
            await self.razorpay.cancel_subscription(subscription.provider_subscription_id)
        now = utc_now()
        subscription.auto_renew = False
        subscription.cancel_at_period_end = True
        subscription.cancelled_at = now
        subscription.cancel_reason = reason
        await self.session.commit()
        return CancelSubscriptionResponse(
            subscription_id=subscription.subscription_id,
            status=subscription.status.value,
            cancel_at_period_end=subscription.cancel_at_period_end,
            expiry_date=subscription.expiry_date,
            auto_renew=subscription.auto_renew,
        )

    async def expire_due_subscriptions(self, *, limit: int = 200) -> dict[str, int]:
        now = utc_now()
        result = await self.session.execute(
            select(ChildSubscription)
            .where(
                ChildSubscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.PAYMENT_PENDING, SubscriptionStatus.HALTED]
                ),
                ChildSubscription.expiry_date < now,
            )
            .order_by(ChildSubscription.expiry_date)
            .limit(limit)
        )
        subscriptions = list(result.scalars().all())
        expired_count = 0
        for subscription in subscriptions:
            if not subscription.auto_renew or subscription.status == SubscriptionStatus.HALTED:
                subscription.status = SubscriptionStatus.EXPIRED
                subscription.auto_renew = False
                expired_count += 1
        await self.session.commit()
        return {"checked_count": len(subscriptions), "expired_count": expired_count}

    async def can_create_story(self, *, user_id: UUID, child_id: UUID) -> bool:
        user_key, child_key = self._keys(user_id, child_id)
        subscription = await self._get_current_subscription(user_key=user_key, child_key=child_key)
        return self._can_use_subscription(subscription, utc_now()) if subscription else False

    async def require_can_create_story(self, *, user_id: UUID, child_id: UUID) -> None:
        if not await self.can_create_story(user_id=user_id, child_id=child_id):
            raise ConflictException(
                "No active subscription with remaining story quota was found for this child.",
                status_code=409,
                code="SUBSCRIPTION_ENTITLEMENT_REQUIRED",
            )

    async def increment_story_usage(self, *, user_id: UUID, child_id: UUID) -> None:
        user_key, child_key = self._keys(user_id, child_id)
        subscription = await self._get_current_subscription(user_key=user_key, child_key=child_key)
        if subscription is None:
            raise ConflictException("No active subscription found for this child.", status_code=409, code="SUBSCRIPTION_NOT_ACTIVE")
        if not self._can_use_subscription(subscription, utc_now()):
            raise ConflictException("Story quota has been exhausted for this child.", status_code=409, code="STORY_QUOTA_EXHAUSTED")
        subscription.stories_used += 1
        await self.session.flush()

    async def ensure_default_plans(self) -> None:
        for row in DEFAULT_PLAN_ROWS:
            result = await self.session.execute(select(SubscriptionPlan).where(SubscriptionPlan.plan_id == row["plan_id"]))
            plan = result.scalar_one_or_none()
            if plan is None:
                self.session.add(SubscriptionPlan(**row))
        await self.session.flush()

    async def _dispatch_webhook_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        provider_subscription_id: str | None,
        provider_payment_id: str | None,
    ) -> None:
        if event_type == "subscription.authenticated":
            await self._webhook_subscription_authenticated(
                provider_subscription_id, payload
            )
        elif event_type == "subscription.activated":
            await self._webhook_subscription_activated(provider_subscription_id)
        elif event_type == "subscription.charged":
            await self._webhook_subscription_charged(payload, provider_subscription_id, provider_payment_id)
        elif event_type == "subscription.pending":
            await self._webhook_subscription_status(provider_subscription_id, SubscriptionStatus.PAYMENT_PENDING, payload, provider_payment_id)
        elif event_type == "subscription.halted":
            await self._webhook_subscription_status(provider_subscription_id, SubscriptionStatus.HALTED, payload, provider_payment_id)
        elif event_type == "subscription.cancelled":
            await self._webhook_subscription_cancelled(provider_subscription_id)
        elif event_type == "payment.failed":
            await self._webhook_payment_failed(payload, provider_subscription_id, provider_payment_id)
        elif event_type == "invoice.paid":
            await self._webhook_invoice_paid(payload, provider_subscription_id, provider_payment_id)
        else:
            logger.info("razorpay_webhook_ignored", event_type=event_type)

    async def _webhook_subscription_authenticated(
        self,
        provider_subscription_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        purchase = await self._get_purchase_by_provider_subscription(
            provider_subscription_id
        )
        if purchase is None:
            return
        subscription_entity = self._entity(payload, "subscription")
        purchase.metadata_json = {
            **(purchase.metadata_json or {}),
            "last_provider_status": subscription_entity.get("status")
            or "authenticated",
        }
        await self.session.flush()

    async def _webhook_subscription_activated(self, provider_subscription_id: str | None) -> None:
        subscription = await self._get_by_provider_subscription_id(
            provider_subscription_id
        )
        if subscription is None:
            purchase = await self._get_purchase_by_provider_subscription(
                provider_subscription_id
            )
            if purchase is None:
                raise NotFoundException(
                    "Purchase order not found for Razorpay event.",
                    code="RAZORPAY_PURCHASE_NOT_FOUND",
                )
            await self.reconcile_paid_purchase(
                user_id=UUID(purchase.user_id),
                child_id=UUID(purchase.child_id),
                purchase_order_id=purchase.purchase_order_id,
            )
            return
        subscription.status = SubscriptionStatus.ACTIVE
        if not subscription.current_period_end:
            plan = await self._get_plan(subscription.plan_id)
            subscription.current_period_end = calculate_period_end(subscription.current_period_start or utc_now(), plan)
            subscription.renewal_date = subscription.current_period_end
            subscription.expiry_date = subscription.current_period_end
        await self.session.flush()

    async def _webhook_subscription_charged(
        self,
        payload: dict[str, Any],
        provider_subscription_id: str | None,
        provider_payment_id: str | None,
    ) -> None:
        subscription = await self._get_by_provider_subscription_id(
            provider_subscription_id
        )
        if subscription is None:
            purchase = await self._get_purchase_by_provider_subscription(
                provider_subscription_id
            )
            if purchase is None:
                raise NotFoundException(
                    "Purchase order not found for Razorpay event.",
                    code="RAZORPAY_PURCHASE_NOT_FOUND",
                )
            purchase = await self._get_purchase_order(
                purchase.purchase_order_id, for_update=True
            )
            payment_entity = self._entity(payload, "payment")
            invoice_entity = self._entity(payload, "invoice")
            subscription_entity = self._entity(payload, "subscription")
            if not provider_payment_id:
                raise AppException(
                    "Razorpay charged event is missing its payment.",
                    status_code=400,
                    code="RAZORPAY_PAYMENT_ID_MISSING",
                )
            paid_invoice = {
                **invoice_entity,
                "id": invoice_entity.get("id"),
                "status": "paid",
                "payment_id": provider_payment_id,
                "amount_paid": payment_entity.get("amount"),
                "currency": payment_entity.get("currency"),
                "paid_at": payment_entity.get("created_at"),
            }
            await self._activate_initial_paid_subscription(
                purchase=purchase,
                provider_subscription=subscription_entity,
                paid_invoice=paid_invoice,
            )
            await self.session.flush()
            return
        plan = await self._get_plan(subscription.plan_id)
        payment_entity = self._entity(payload, "payment")
        invoice_entity = self._entity(payload, "invoice")
        amount = self._amount_from_razorpay(payment_entity, fallback=plan.price)
        currency = payment_entity.get("currency") or plan.currency
        paid_at = self._timestamp_from_razorpay(payment_entity.get("created_at")) or utc_now()
        await self._create_payment_if_absent(
            purchase_order_id=None,
            subscription_id=subscription.subscription_id,
            user_id=subscription.user_id,
            child_id=subscription.child_id,
            plan_id=subscription.plan_id,
            amount=amount,
            currency=currency,
            status=PaymentStatus.SUCCESS,
            payment_type=PaymentType.RENEWAL,
            provider_payment_id=provider_payment_id,
            provider_subscription_id=provider_subscription_id,
            provider_invoice_id=invoice_entity.get("id"),
            paid_at=paid_at,
        )
        start = ensure_utc(subscription.current_period_end)
        if start < utc_now():
            start = utc_now()
        period_end = calculate_period_end(start, plan)
        subscription.current_period_start = start
        subscription.current_period_end = period_end
        subscription.renewal_date = period_end
        subscription.expiry_date = period_end
        subscription.stories_used = 0
        subscription.stories_limit = plan.stories_per_month
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.auto_renew = True
        await self._mark_purchase_completed_by_provider_subscription(provider_subscription_id)
        await self.session.flush()

    async def _webhook_subscription_status(
        self,
        provider_subscription_id: str | None,
        status: SubscriptionStatus,
        payload: dict[str, Any],
        provider_payment_id: str | None,
    ) -> None:
        subscription = await self._require_provider_subscription(provider_subscription_id)
        subscription.status = status
        if provider_payment_id:
            await self._webhook_payment_failed(payload, provider_subscription_id, provider_payment_id)
        await self.session.flush()

    async def _webhook_subscription_cancelled(self, provider_subscription_id: str | None) -> None:
        subscription = await self._require_provider_subscription(provider_subscription_id)
        subscription.auto_renew = False
        subscription.cancel_at_period_end = True
        subscription.cancelled_at = utc_now()
        if ensure_utc(subscription.expiry_date) <= utc_now():
            subscription.status = SubscriptionStatus.CANCELLED
        await self.session.flush()

    async def _webhook_payment_failed(
        self,
        payload: dict[str, Any],
        provider_subscription_id: str | None,
        provider_payment_id: str | None,
    ) -> None:
        subscription = await self._get_by_provider_subscription_id(provider_subscription_id) if provider_subscription_id else None
        if subscription is None:
            purchase = await self._get_purchase_by_provider_subscription(
                provider_subscription_id
            )
            if purchase is not None:
                payment_entity = self._entity(payload, "payment")
                purchase.metadata_json = {
                    **(purchase.metadata_json or {}),
                    "latest_initial_payment_failure": {
                        "payment_id": provider_payment_id,
                        "reason": payment_entity.get("error_description")
                        or payment_entity.get("error_reason")
                        or "Payment failed",
                        "created_at": payment_entity.get("created_at"),
                    },
                }
                await self.session.flush()
                return
            logger.info(
                "razorpay_payment_failed_without_known_subscription",
                provider_subscription_id=provider_subscription_id,
            )
            return
        payment_entity = self._entity(payload, "payment")
        failure_reason = payment_entity.get("error_description") or payment_entity.get("error_reason") or "Payment failed"
        await self._create_payment_if_absent(
            purchase_order_id=None,
            subscription_id=subscription.subscription_id,
            user_id=subscription.user_id,
            child_id=subscription.child_id,
            plan_id=subscription.plan_id,
            amount=self._amount_from_razorpay(payment_entity, fallback=Decimal("0.00")),
            currency=payment_entity.get("currency") or "INR",
            status=PaymentStatus.FAILED,
            payment_type=PaymentType.RENEWAL,
            provider_payment_id=provider_payment_id,
            provider_subscription_id=provider_subscription_id,
            paid_at=None,
            failure_reason=failure_reason,
        )

    async def _webhook_invoice_paid(
        self,
        payload: dict[str, Any],
        provider_subscription_id: str | None,
        provider_payment_id: str | None,
    ) -> None:
        invoice_entity = self._entity(payload, "invoice")
        invoice_id = invoice_entity.get("id")
        if not invoice_id:
            return
        payment = await self._get_payment_by_provider_payment(provider_payment_id) if provider_payment_id else None
        if payment is None and provider_subscription_id:
            result = await self.session.execute(
                select(Payment)
                .where(Payment.provider_subscription_id == provider_subscription_id, Payment.provider_invoice_id.is_(None))
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
            payment = result.scalar_one_or_none()
        if payment is not None:
            payment.provider_invoice_id = invoice_id
            await self.session.flush()

    async def _ensure_child(self, user_id: UUID, child_id: UUID) -> None:
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found.", code="CHILD_NOT_FOUND")

    async def _get_plan(self, plan_id: str) -> SubscriptionPlan:
        result = await self.session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.plan_id == plan_id, SubscriptionPlan.is_active.is_(True))
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise NotFoundException("Subscription plan not found.", code="SUBSCRIPTION_PLAN_NOT_FOUND")
        return plan

    async def _get_purchase_order(
        self, purchase_order_id: str, *, for_update: bool = False
    ) -> PurchaseOrder:
        statement = select(PurchaseOrder).where(
            PurchaseOrder.purchase_order_id == purchase_order_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        purchase = result.scalar_one_or_none()
        if purchase is None:
            raise NotFoundException("Purchase order not found.", code="PURCHASE_ORDER_NOT_FOUND")
        return purchase

    async def _get_pending_paid_purchase(
        self,
        *,
        user_key: str,
        child_key: str,
        plan_id: str,
        for_update: bool = False,
    ) -> PurchaseOrder | None:
        statement = (
            select(PurchaseOrder)
            .where(
                PurchaseOrder.user_id == user_key,
                PurchaseOrder.child_id == child_key,
                PurchaseOrder.plan_id == plan_id,
                PurchaseOrder.status == PurchaseStatus.PENDING_PAYMENT,
            )
            .order_by(PurchaseOrder.created_at.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def _get_event(self, event_id: str) -> SubscriptionEvent | None:
        result = await self.session.execute(select(SubscriptionEvent).where(SubscriptionEvent.event_id == event_id))
        return result.scalar_one_or_none()

    async def _has_used_free_trial(self, *, user_key: str, child_key: str) -> bool:
        result = await self.session.execute(
            select(ChildSubscription.id).where(
                ChildSubscription.user_id == user_key,
                ChildSubscription.child_id == child_key,
                ChildSubscription.plan_id == "FREE_TRIAL",
            )
        )
        return result.scalar_one_or_none() is not None

    async def _get_current_subscription(self, *, user_key: str, child_key: str) -> ChildSubscription | None:
        now = utc_now()
        result = await self.session.execute(
            select(ChildSubscription)
            .where(
                ChildSubscription.user_id == user_key,
                ChildSubscription.child_id == child_key,
                ChildSubscription.status.in_(list(ENTITLEMENT_STATUSES)),
                ChildSubscription.expiry_date > now,
            )
            .order_by(ChildSubscription.expiry_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_by_provider_subscription_id(self, provider_subscription_id: str | None) -> ChildSubscription | None:
        if not provider_subscription_id:
            return None
        result = await self.session.execute(
            select(ChildSubscription).where(ChildSubscription.provider_subscription_id == provider_subscription_id).limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_purchase_by_provider_subscription(
        self, provider_subscription_id: str | None
    ) -> PurchaseOrder | None:
        if not provider_subscription_id:
            return None
        result = await self.session.execute(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.provider_subscription_id
                == provider_subscription_id
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _require_provider_subscription(self, provider_subscription_id: str | None) -> ChildSubscription:
        subscription = await self._get_by_provider_subscription_id(provider_subscription_id)
        if subscription is None:
            raise NotFoundException("Subscription not found for Razorpay event.", code="RAZORPAY_SUBSCRIPTION_NOT_FOUND")
        return subscription

    async def _get_payment_by_provider_payment(self, provider_payment_id: str | None) -> Payment | None:
        if not provider_payment_id:
            return None
        result = await self.session.execute(select(Payment).where(Payment.provider_payment_id == provider_payment_id).limit(1))
        return result.scalar_one_or_none()

    async def _create_payment_if_absent(
        self,
        *,
        purchase_order_id: str | None,
        subscription_id: str | None,
        user_id: str,
        child_id: str,
        plan_id: str,
        amount: Decimal,
        currency: str,
        status: PaymentStatus,
        payment_type: PaymentType,
        provider_payment_id: str | None,
        provider_subscription_id: str | None,
        paid_at,
        provider_invoice_id: str | None = None,
        failure_reason: str | None = None,
    ) -> Payment:
        existing = await self._get_payment_by_provider_payment(provider_payment_id)
        if existing is not None:
            if provider_invoice_id and not existing.provider_invoice_id:
                existing.provider_invoice_id = provider_invoice_id
            return existing
        payment = Payment(
            payment_id=generate_payment_id(),
            purchase_order_id=purchase_order_id,
            subscription_id=subscription_id,
            user_id=user_id,
            child_id=child_id,
            plan_id=plan_id,
            amount=amount,
            currency=currency,
            status=status,
            provider=PaymentProvider.RAZORPAY.value,
            provider_payment_id=provider_payment_id,
            provider_subscription_id=provider_subscription_id,
            provider_invoice_id=provider_invoice_id,
            payment_type=payment_type,
            paid_at=paid_at,
            failure_reason=failure_reason,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def _activate_initial_paid_subscription(
        self,
        *,
        purchase: PurchaseOrder,
        provider_subscription: dict[str, Any],
        paid_invoice: dict[str, Any],
    ) -> ChildSubscription:
        provider_subscription_id = purchase.provider_subscription_id
        existing = await self._get_by_provider_subscription_id(
            provider_subscription_id
        )
        if existing is not None:
            purchase.status = PurchaseStatus.COMPLETED
            return existing

        current = await self._get_current_subscription(
            user_key=purchase.user_id,
            child_key=purchase.child_id,
        )
        if current is not None and current.plan_id != "FREE_TRIAL":
            raise ConflictException(
                "Child already has an active paid subscription.",
                status_code=409,
                code="SUBSCRIPTION_ALREADY_ACTIVE",
            )

        plan = await self._get_plan(purchase.plan_id)
        now = utc_now()
        period_start = (
            self._timestamp_from_razorpay(provider_subscription.get("current_start"))
            or self._timestamp_from_razorpay(paid_invoice.get("paid_at"))
            or now
        )
        period_end = self._timestamp_from_razorpay(
            provider_subscription.get("current_end")
        ) or calculate_period_end(period_start, plan)
        subscription = ChildSubscription(
            subscription_id=generate_subscription_id(),
            user_id=purchase.user_id,
            child_id=purchase.child_id,
            plan_id=plan.plan_id,
            billing_cycle=plan.billing_cycle,
            status=SubscriptionStatus.ACTIVE,
            start_date=period_start,
            current_period_start=period_start,
            current_period_end=period_end,
            renewal_date=period_end,
            expiry_date=period_end,
            auto_renew=True,
            provider=PaymentProvider.RAZORPAY.value,
            provider_subscription_id=provider_subscription_id,
            stories_used=0,
            stories_limit=plan.stories_per_month,
        )
        self.session.add(subscription)
        await self.session.flush()
        await self._create_payment_if_absent(
            purchase_order_id=purchase.purchase_order_id,
            subscription_id=subscription.subscription_id,
            user_id=purchase.user_id,
            child_id=purchase.child_id,
            plan_id=plan.plan_id,
            amount=self._amount_from_razorpay(
                {"amount": paid_invoice.get("amount_paid") or paid_invoice.get("amount")},
                fallback=plan.price,
            ),
            currency=paid_invoice.get("currency") or plan.currency,
            status=PaymentStatus.SUCCESS,
            payment_type=PaymentType.INITIAL,
            provider_payment_id=paid_invoice.get("payment_id"),
            provider_subscription_id=provider_subscription_id,
            provider_invoice_id=paid_invoice.get("id"),
            paid_at=self._timestamp_from_razorpay(paid_invoice.get("paid_at")) or now,
        )
        purchase.status = PurchaseStatus.COMPLETED
        return subscription

    async def _mark_purchase_completed_by_provider_subscription(self, provider_subscription_id: str | None) -> None:
        if not provider_subscription_id:
            return
        result = await self.session.execute(
            select(PurchaseOrder).where(PurchaseOrder.provider_subscription_id == provider_subscription_id)
        )
        for purchase in result.scalars().all():
            if purchase.status in (PurchaseStatus.PENDING_PAYMENT, PurchaseStatus.PAYMENT_SUCCESS):
                purchase.status = PurchaseStatus.COMPLETED

    @staticmethod
    def _plan_response(plan: SubscriptionPlan) -> SubscriptionPlanResponse:
        if plan.billing_cycle == BillingCycle.TRIAL:
            duration_label = f"{plan.trial_days} Days"
        elif plan.duration_months == 1:
            duration_label = "1 Month"
        else:
            duration_label = f"{plan.duration_months} Months"
        return SubscriptionPlanResponse(
            plan_id=plan.plan_id,
            name=plan.name,
            price=plan.price,
            currency=plan.currency,
            duration_label=duration_label,
            billing_cycle=plan.billing_cycle.value,
            features=SubscriptionFeatures(
                stories_per_month=plan.stories_per_month,
                audio_enabled=plan.audio_enabled,
                image_enabled=plan.image_enabled,
                pdf_enabled=plan.pdf_enabled,
            ),
        )

    @staticmethod
    def _subscription_summary(subscription: ChildSubscription) -> SubscriptionSummaryResponse:
        return SubscriptionSummaryResponse(
            subscription_id=subscription.subscription_id,
            plan_id=subscription.plan_id,
            status=subscription.status.value,
            start_date=subscription.start_date,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            renewal_date=subscription.renewal_date,
            expiry_date=subscription.expiry_date,
            billing_cycle=subscription.billing_cycle.value,
            auto_renew=subscription.auto_renew,
        )

    @staticmethod
    def _payment_verification_response(
        *,
        purchase: PurchaseOrder,
        status: str,
        provider_status: str,
        can_retry: bool,
        subscription: ChildSubscription | None,
    ) -> SubscriptionPaymentVerificationResponse:
        return SubscriptionPaymentVerificationResponse(
            purchase_id=purchase.purchase_order_id,
            status=status,
            provider_status=provider_status,
            can_retry=can_retry,
            retry_after_seconds=3 if status == "PENDING" else None,
            subscription=(
                SubscriptionService._subscription_summary(subscription)
                if subscription is not None
                else None
            ),
        )

    @staticmethod
    def _keys(user_id: UUID, child_id: UUID) -> tuple[str, str]:
        return str(user_id), str(child_id)

    @staticmethod
    def _can_use_subscription(subscription: ChildSubscription, now) -> bool:
        return (
            subscription.status in ENTITLEMENT_STATUSES
            and ensure_utc(subscription.expiry_date) > now
            and subscription.stories_used < subscription.stories_limit
        )

    @staticmethod
    def _effective_razorpay_plan_id(plan: SubscriptionPlan) -> str | None:
        env_value = None
        if plan.plan_id == "MONTHLY":
            env_value = settings.RAZORPAY_MONTHLY_PLAN_ID
        elif plan.plan_id == "YEARLY":
            env_value = settings.RAZORPAY_YEARLY_PLAN_ID
        if env_value:
            return env_value
        if plan.razorpay_plan_id and not plan.razorpay_plan_id.startswith("RAZORPAY_"):
            return plan.razorpay_plan_id
        return plan.razorpay_plan_id if settings.ENVIRONMENT.lower() in {"test", "testing"} else None

    @staticmethod
    def _payload_subscription_id(payload: dict[str, Any]) -> str | None:
        return (
            SubscriptionService._entity(payload, "subscription").get("id")
            or SubscriptionService._entity(payload, "payment").get("subscription_id")
            or SubscriptionService._entity(payload, "invoice").get("subscription_id")
        )

    @staticmethod
    def _payload_payment_id(payload: dict[str, Any]) -> str | None:
        return (
            SubscriptionService._entity(payload, "payment").get("id")
            or SubscriptionService._entity(payload, "invoice").get("payment_id")
        )

    @staticmethod
    def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
        entity = payload.get("payload", {}).get(name, {}).get("entity")
        return entity if isinstance(entity, dict) else {}

    @staticmethod
    def _amount_from_razorpay(entity: dict[str, Any], fallback: Decimal) -> Decimal:
        amount = entity.get("amount")
        if isinstance(amount, int):
            return Decimal(amount) / Decimal(100)
        if isinstance(amount, str) and amount.isdigit():
            return Decimal(amount) / Decimal(100)
        return fallback

    @staticmethod
    def _timestamp_from_razorpay(value: Any):
        if isinstance(value, int):
            from datetime import datetime

            return datetime.fromtimestamp(value, UTC)
        return None
