from __future__ import annotations

from uuid import uuid4


def _public_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:24].upper()}"


def generate_subscription_id(prefix: str = "SUB_") -> str:
    return _public_id(prefix)


def generate_purchase_order_id() -> str:
    return _public_id("PO_")


def generate_payment_id() -> str:
    return _public_id("PAY_")
