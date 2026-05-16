#!/usr/bin/env python3
"""Debug script to test story plan validation with mock response."""

import json
import sys
sys.path.insert(0, '.')

from app.service.mock_llm_responses import get_mock_story_plan
from app.service.plan_validator import PlanValidator

def test_mock_plan_validation():
    """Test that mock story plan passes validation."""
    print("\n" + "="*80)
    print("TESTING MOCK STORY PLAN VALIDATION")
    print("="*80)

    validator = PlanValidator()

    for age_group in ["2-4", "5-7", "8-12"]:
        print(f"\n--- Testing age_group: {age_group} ---")
        plan = get_mock_story_plan(child_name="Emma", age_group=age_group)

        # Show plan structure
        print(f"Plan structure:")
        print(f"  - Title: {plan.get('title')}")
        print(f"  - Age band: {plan.get('age_band')}")
        print(f"  - Final page count: {plan.get('final_page_count')}")
        print(f"  - Pages array length: {len(plan.get('pages', []))}")
        print(f"  - Characters: {[c['name'] for c in plan.get('characters', [])]}")

        # Check first page structure
        if plan.get('pages'):
            first_page = plan['pages'][0]
            print(f"\n  First page keys: {list(first_page.keys())}")
            print(f"  Expected keys:")
            required_keys = {
                "page_number", "story_role", "scene_description",
                "narration_sample", "child_action", "learning_goal_integration",
                "environment", "mood", "visual_continuity_notes"
            }
            for key in required_keys:
                has_key = "✓" if key in first_page else "✗"
                print(f"    {has_key} {key}")

        # Validate
        result = validator.validate(plan, age_group=age_group)
        print(f"\nValidation result: {'✓ PASS' if result.ok else '✗ FAIL'}")

        if not result.ok:
            print(f"Errors ({len(result.errors)}):")
            for error in result.errors:
                print(f"  - {error}")
        else:
            print("All validations passed!")

if __name__ == "__main__":
    test_mock_plan_validation()
