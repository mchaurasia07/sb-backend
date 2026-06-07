import pytest

from app.core.age_groups import (
    AGE_GROUP_0_3,
    AGE_GROUP_3_6,
    AGE_GROUP_6_9,
    age_group_label,
    page_count_for_age_group,
    page_count_range_for_age_group,
    validate_age_group,
)
from app.core.exceptions import AppException


def test_age_group_validation_accepts_canonical_values():
    assert validate_age_group("0-3") == AGE_GROUP_0_3
    assert validate_age_group("3-6") == AGE_GROUP_3_6
    assert validate_age_group("6-9") == AGE_GROUP_6_9


def test_age_group_validation_maps_legacy_values():
    assert validate_age_group("0-2") == AGE_GROUP_0_3
    assert validate_age_group("2-4") == AGE_GROUP_0_3
    assert validate_age_group("4-6") == AGE_GROUP_3_6
    assert validate_age_group("6-8") == AGE_GROUP_6_9
    assert validate_age_group("TODDLER") == AGE_GROUP_0_3
    assert validate_age_group("ADVANCED") == AGE_GROUP_6_9


def test_age_group_counts_ranges_and_labels_use_canonical_values():
    assert page_count_for_age_group("0-3") == 8
    assert page_count_for_age_group("3-6") == 8
    assert page_count_for_age_group("6-9") == 10
    assert page_count_range_for_age_group("0-3") == (8, 9)
    assert page_count_range_for_age_group("3-6") == (8, 10)
    assert page_count_range_for_age_group("6-9") == (10, 12)
    assert age_group_label("4-6") == "Early Reader (3-6 years)"


def test_age_group_validation_rejects_unknown_values():
    with pytest.raises(AppException):
        validate_age_group("9-12")
