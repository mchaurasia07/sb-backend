from __future__ import annotations

import hmac
from hashlib import sha256
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logger import get_logger

logger = get_logger(__name__)


class RazorpayService:
    """Small Razorpay REST client for subscription checkout and webhooks."""

    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(self) -> None:
        self.key_id = settings.RAZORPAY_KEY_ID
        self.key_secret = settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET

    def _require_api_credentials(self) -> None:
        if not self.key_id or not self.key_secret:
            raise AppException(
                "Razorpay credentials are not configured.",
                status_code=503,
                code="RAZORPAY_NOT_CONFIGURED",
            )

    async def create_subscription(
        self,
        *,
        razorpay_plan_id: str,
        plan_id: str,
        user_id: str,
        child_id: str,
        purchase_order_id: str,
        total_count: int,
    ) -> dict[str, Any]:
        self._require_api_credentials()
        payload = {
            "plan_id": razorpay_plan_id,
            "total_count": total_count,
            "customer_notify": 1,
            "notes": {
                "app_plan_id": plan_id,
                "user_id": user_id,
                "child_id": child_id,
                "purchase_order_id": purchase_order_id,
            },
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/subscriptions",
                json=payload,
                auth=(self.key_id, self.key_secret),
            )
        if response.status_code >= 400:
            logger.error(
                "razorpay_create_subscription_failed",
                status_code=response.status_code,
                response=response.text[:1000],
            )
            raise AppException(
                "Unable to create Razorpay subscription.",
                status_code=502,
                code="RAZORPAY_SUBSCRIPTION_CREATE_FAILED",
            )
        return response.json()

    async def cancel_subscription(self, provider_subscription_id: str) -> dict[str, Any]:
        self._require_api_credentials()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/subscriptions/{provider_subscription_id}/cancel",
                json={"cancel_at_cycle_end": 1},
                auth=(self.key_id, self.key_secret),
            )
        if response.status_code >= 400:
            logger.error(
                "razorpay_cancel_subscription_failed",
                provider_subscription_id=provider_subscription_id,
                status_code=response.status_code,
                response=response.text[:1000],
            )
            raise AppException(
                "Unable to cancel Razorpay subscription.",
                status_code=502,
                code="RAZORPAY_SUBSCRIPTION_CANCEL_FAILED",
            )
        return response.json()

    async def fetch_subscription(self, provider_subscription_id: str) -> dict[str, Any]:
        self._require_api_credentials()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/subscriptions/{provider_subscription_id}",
                auth=(self.key_id, self.key_secret),
            )
        if response.status_code >= 400:
            logger.error(
                "razorpay_fetch_subscription_failed",
                provider_subscription_id=provider_subscription_id,
                status_code=response.status_code,
            )
            raise AppException(
                "Unable to confirm the Razorpay subscription.",
                status_code=502,
                code="RAZORPAY_SUBSCRIPTION_FETCH_FAILED",
            )
        return response.json()

    async def fetch_subscription_invoices(
        self, provider_subscription_id: str
    ) -> list[dict[str, Any]]:
        self._require_api_credentials()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.BASE_URL}/invoices",
                params={"subscription_id": provider_subscription_id},
                auth=(self.key_id, self.key_secret),
            )
        if response.status_code >= 400:
            logger.error(
                "razorpay_fetch_subscription_invoices_failed",
                provider_subscription_id=provider_subscription_id,
                status_code=response.status_code,
            )
            raise AppException(
                "Unable to confirm the Razorpay payment.",
                status_code=502,
                code="RAZORPAY_INVOICES_FETCH_FAILED",
            )
        payload = response.json()
        items = payload.get("items", [])
        return items if isinstance(items, list) else []

    def verify_subscription_payment_signature(
        self,
        *,
        razorpay_payment_id: str,
        razorpay_subscription_id: str,
        razorpay_signature: str,
    ) -> bool:
        if not self.key_secret:
            return False
        message = f"{razorpay_payment_id}|{razorpay_subscription_id}".encode()
        expected = hmac.new(self.key_secret.encode(), message, sha256).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature)

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str | None) -> bool:
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(self.webhook_secret.encode(), raw_body, sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
