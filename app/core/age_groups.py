from __future__ import annotations

from enum import Enum
from typing import Any

from app.core.exceptions import AppException


class AgeGroup(str, Enum):
    """Canonical age groups for story content."""

    INFANT_TODDLER = "0-3"
    EARLY_READER = "3-6"
    GROWING_READER = "6-9"


AGE_GROUP_0_3 = AgeGroup.INFANT_TODDLER.value
AGE_GROUP_3_6 = AgeGroup.EARLY_READER.value
AGE_GROUP_6_9 = AgeGroup.GROWING_READER.value

AGE_GROUP_LABELS = {
    AGE_GROUP_0_3: "Infant Toddler (0-3 years)",
    AGE_GROUP_3_6: "Early Reader (3-6 years)",
    AGE_GROUP_6_9: "Growing Reader (6-9 years)",
}

AGE_GROUP_ALIASES = {
    "0-2": AGE_GROUP_0_3,
    "2-4": AGE_GROUP_0_3,
    "4-6": AGE_GROUP_3_6,
    "6-8": AGE_GROUP_6_9,
    "INFANT_TODDLER": AGE_GROUP_0_3,
    "TODDLER": AGE_GROUP_0_3,
    "EARLY_READER": AGE_GROUP_3_6,
    "ADVANCED": AGE_GROUP_6_9,
    "GROWING_READER": AGE_GROUP_6_9,
    "infant_toddler": AGE_GROUP_0_3,
    "toddler": AGE_GROUP_0_3,
    "early_reader": AGE_GROUP_3_6,
    "advanced": AGE_GROUP_6_9,
    "growing_reader": AGE_GROUP_6_9,
    "Infant Toddler (0-3 years)": AGE_GROUP_0_3,
    "Early Reader (3-6 years)": AGE_GROUP_3_6,
    "Growing Reader (6-9 years)": AGE_GROUP_6_9,
}

DEFAULT_AGE_GROUP = AGE_GROUP_3_6
SUPPORTED_AGE_GROUPS = (
    AGE_GROUP_0_3,
    AGE_GROUP_3_6,
    AGE_GROUP_6_9,
)

PAGE_COUNT_BY_AGE_GROUP = {
    AGE_GROUP_0_3: 8,
    AGE_GROUP_3_6: 8,
    AGE_GROUP_6_9: 10,
}

PAGE_COUNT_RANGE_BY_AGE_GROUP = {
    AGE_GROUP_0_3: (8, 9),
    AGE_GROUP_3_6: (8, 10),
    AGE_GROUP_6_9: (10, 12),
}


def normalize_age_group(value: Any) -> str:
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip()
    return AGE_GROUP_ALIASES.get(normalized, normalized)


def validate_age_group(value: Any) -> str:
    normalized = normalize_age_group(value)
    if normalized not in SUPPORTED_AGE_GROUPS:
        raise AppException(
            "Unsupported age group",
            code="AGE_GROUP_UNSUPPORTED",
            details={"age_group": normalized, "supported_age_groups": ", ".join(SUPPORTED_AGE_GROUPS)},
        )
    return normalized


def age_group_label(value: Any) -> str:
    return AGE_GROUP_LABELS.get(normalize_age_group(value), str(getattr(value, "value", value) or "").strip())


def page_count_for_age_group(value: Any) -> int:
    return PAGE_COUNT_BY_AGE_GROUP.get(normalize_age_group(value), PAGE_COUNT_BY_AGE_GROUP[DEFAULT_AGE_GROUP])


def page_count_range_for_age_group(value: Any) -> tuple[int, int]:
    return PAGE_COUNT_RANGE_BY_AGE_GROUP.get(
        normalize_age_group(value),
        PAGE_COUNT_RANGE_BY_AGE_GROUP[DEFAULT_AGE_GROUP],
    )
