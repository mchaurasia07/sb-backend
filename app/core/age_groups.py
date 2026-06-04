from __future__ import annotations

from typing import Any

from app.core.exceptions import AppException


AGE_GROUP_0_2 = "0-2"
AGE_GROUP_2_4 = "2-4"
AGE_GROUP_4_6 = "4-6"
AGE_GROUP_6_8 = "6-8"

DEFAULT_AGE_GROUP = AGE_GROUP_4_6
SUPPORTED_AGE_GROUPS = (
    AGE_GROUP_0_2,
    AGE_GROUP_2_4,
    AGE_GROUP_4_6,
    AGE_GROUP_6_8,
)

PAGE_COUNT_BY_AGE_GROUP = {
    AGE_GROUP_0_2: 4,
    AGE_GROUP_2_4: 6,
    AGE_GROUP_4_6: 8,
    AGE_GROUP_6_8: 10,
}

PAGE_COUNT_RANGE_BY_AGE_GROUP = {
    AGE_GROUP_0_2: (6, 8),
    AGE_GROUP_2_4: (6, 9),
    AGE_GROUP_4_6: (8, 10),
    AGE_GROUP_6_8: (10, 11),
}


def normalize_age_group(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def validate_age_group(value: Any) -> str:
    normalized = normalize_age_group(value)
    if normalized not in SUPPORTED_AGE_GROUPS:
        raise AppException(
            "Unsupported age group",
            code="AGE_GROUP_UNSUPPORTED",
            details={"age_group": normalized, "supported_age_groups": ", ".join(SUPPORTED_AGE_GROUPS)},
        )
    return normalized


def page_count_for_age_group(value: Any) -> int:
    return PAGE_COUNT_BY_AGE_GROUP.get(normalize_age_group(value), PAGE_COUNT_BY_AGE_GROUP[DEFAULT_AGE_GROUP])


def page_count_range_for_age_group(value: Any) -> tuple[int, int]:
    return PAGE_COUNT_RANGE_BY_AGE_GROUP.get(
        normalize_age_group(value),
        PAGE_COUNT_RANGE_BY_AGE_GROUP[DEFAULT_AGE_GROUP],
    )
