#!/usr/bin/env python3
"""Validate that all fixes are working correctly."""

import sys
import json
sys.path.insert(0, '.')

from app.service.mock_llm_responses import get_mock_story_plan
from app.service.plan_validator import PlanValidator

def test_validator_enhancement():
    """Test that validator properly checks all required fields."""
    print("\n" + "="*80)
    print("VALIDATOR ENHANCEMENT TEST")
    print("="*80)

    validator = PlanValidator()

    # Test 1: Mock response should pass
    print("\n[TEST 1] Mock response passes validation")
    print("-" * 60)
    for age_group in ["2-4", "5-7", "8-12"]:
        plan = get_mock_story_plan(child_name="Emma", age_group=age_group)
        result = validator.validate(plan, age_group=age_group)

        status = "✓ PASS" if result.ok else "✗ FAIL"
        print(f"{status} Age group {age_group}:")
        if not result.ok:
            for error in result.errors:
                print(f"  Error: {error}")
        else:
            print(f"  ✓ Valid: {len(plan['pages'])} pages, {len(plan['characters'])} characters")

    # Test 2: Catch missing required fields
    print(f"\n[TEST 2] Detect missing required fields")
    print("-" * 60)

    # Create invalid plan (missing scene_description in first page)
    plan = get_mock_story_plan(child_name="Emma", age_group="5-7")
    del plan['pages'][0]['scene_description']

    result = validator.validate(plan, age_group="5-7")
    if not result.ok:
        print("✓ Validator correctly detected missing field:")
        for error in result.errors:
            if "scene_description" in error:
                print(f"  {error}")
    else:
        print("✗ FAIL: Validator should have caught missing field!")

    # Test 3: Catch invalid age_band
    print(f"\n[TEST 3] Detect age_band mismatch")
    print("-" * 60)

    plan = get_mock_story_plan(child_name="Emma", age_group="5-7")
    plan['age_band'] = "Advanced"  # Wrong! Should be "Early Reader"

    result = validator.validate(plan, age_group="5-7")
    if not result.ok:
        print("✓ Validator correctly detected age_band mismatch:")
        for error in result.errors:
            if "age_band" in error:
                print(f"  {error}")
    else:
        print("✗ FAIL: Validator should have caught age_band mismatch!")

    # Test 4: Catch page count mismatch
    print(f"\n[TEST 4] Detect page count mismatch")
    print("-" * 60)

    plan = get_mock_story_plan(child_name="Emma", age_group="5-7")
    plan['final_page_count'] = 10  # Wrong! Should be 8

    result = validator.validate(plan, age_group="5-7")
    if not result.ok:
        print("✓ Validator correctly detected page count mismatch:")
        for error in result.errors:
            if "final_page_count" in error or "Page count" in error:
                print(f"  {error}")
    else:
        print("✗ FAIL: Validator should have caught page count mismatch!")

    print(f"\n{'='*80}")
    print("VALIDATOR TESTS COMPLETE")
    print(f"{'='*80}")

if __name__ == "__main__":
    test_validator_enhancement()
