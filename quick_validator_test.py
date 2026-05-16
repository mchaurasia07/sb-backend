#!/usr/bin/env python3
"""Quick test to see if mock response passes validator."""

import sys
sys.path.insert(0, '.')

from app.service.mock_llm_responses import get_mock_story_plan
from app.service.plan_validator import PlanValidator

# Test with age group 5-7 (Early Reader)
plan = get_mock_story_plan(child_name="Emma", age_group="5-7")
validator = PlanValidator()
result = validator.validate(plan, age_group="5-7")

print("Mock Plan Validation Test")
print("="*60)
print(f"Result: {'✓ PASSED' if result.ok else '✗ FAILED'}")
print(f"Errors: {len(result.errors)}")

if result.errors:
    print("\nValidation Errors:")
    for i, error in enumerate(result.errors, 1):
        print(f"{i}. {error}")
else:
    print("\n✓ All validation checks passed!")
    print("\nPlan Structure:")
    print(f"  - Title: {plan['title']}")
    print(f"  - Age Band: {plan['age_band']}")
    print(f"  - Final Page Count: {plan['final_page_count']}")
    print(f"  - Actual Pages: {len(plan['pages'])}")
    print(f"  - Characters: {len(plan['characters'])}")

    # Check first page has all required keys
    first_page = plan['pages'][0]
    required = {"page_number", "story_role", "scene_description", "narration_sample",
                "child_action", "learning_goal_integration", "environment", "mood",
                "visual_continuity_notes"}
    print(f"\nFirst Page Required Fields:")
    for field in required:
        has_it = "✓" if field in first_page else "✗"
        print(f"  {has_it} {field}")
