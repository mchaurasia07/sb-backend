from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from dateutil.relativedelta import relativedelta


class PeriodPlan(Protocol):
    plan_id: str
    duration_months: int
    trial_days: int


def utc_now() -> datetime:
    """Return a timezone-aware UTC datetime for all subscription timestamps."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Normalize naive datetimes from MySQL to UTC-aware datetimes."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def calculate_period_end(start_date: datetime, plan: PeriodPlan) -> datetime:
    """Calculate trial/premium period end using calendar months where applicable."""
    start = ensure_utc(start_date)
    if plan.plan_id == "FREE_TRIAL":
        return start + timedelta(days=plan.trial_days or 7)
    if plan.duration_months > 0:
        return start + relativedelta(months=plan.duration_months)
    return start


def isoformat_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).isoformat().replace("+00:00", "Z")
