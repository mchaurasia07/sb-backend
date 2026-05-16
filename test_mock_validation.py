#!/usr/bin/env python3
"""Test mock story plan validation directly."""

import json
import sys
sys.path.insert(0, '.')

from app.service.mock_llm_responses import get_mock_story_plan
from app.service.plan_validator import PlanValidator

def main():
    """Test validation of mock plan."""
    print("\n" + "="*80)
    print("TEST: Mock Story Plan Validation")
    print("="*80)

    validator = PlanValidator()

    # Test each age group
    for age_group in ["2-4", "5-7", "8-12"]:
        print(f"\n{'='*60}")
        print(f"Age Group: {age_group}")
        print(f"{'='*60}")

        # Get mock plan
        plan = get_mock_story_plan(child_name="Emma", age_group=age_group)

        # Validate it
        result = validator.validate(plan, age_group=age_group)

        # Display results
        if result.ok:
            print("✓ VALIDATION PASSED")
            print(f"\nPlan Summary:")
            print(f"  Title: {plan['title']}")
            print(f"  Age Band: {plan['age_band']}")
            print(f"  Pages: {plan['final_page_count']}")
            print(f"  Characters: {len(plan['characters'])}")
            print(f"  Moral: {plan['moral_theme']}")
        else:
            print("✗ VALIDATION FAILED")
            print(f"\nErrors ({len(result.errors)}):")
            for i, error in enumerate(result.errors, 1):
                print(f"  {i}. {error}")

    # Also try to parse as JSON to ensure it's valid JSON
    print(f"\n{'='*60}")
    print("JSON Validity Test")
    print(f"{'='*60}")

    for age_group in ["2-4", "5-7", "8-12"]:
        plan = get_mock_story_plan(child_name="Emma", age_group=age_group)
        try:
            json_str = json.dumps(plan)
            parsed = json.loads(json_str)
            print(f"✓ Age {age_group}: Valid JSON ({len(json_str)} chars)")
        except json.JSONDecodeError as e:
            print(f"✗ Age {age_group}: Invalid JSON - {e}")

if __name__ == "__main__":
    main()
