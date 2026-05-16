#!/usr/bin/env python3
"""Diagnose where story plan validation is failing."""

import sys
import json
sys.path.insert(0, '.')

from app.service.mock_llm_responses import get_mock_story_plan, get_mock_story_plan_text
from app.service.plan_validator import PlanValidator
from app.core.config import settings

def diagnose():
    """Run diagnostics on the validation chain."""
    print("\n" + "="*80)
    print("STORY PLAN VALIDATION DIAGNOSTICS")
    print("="*80)

    # Check settings
    print(f"\n[SETTINGS]")
    print(f"Mock Mode: {settings.STORY_MOCK_LLM_RESPONSES}")
    print(f"Mock Response Enabled: {bool(settings.STORY_MOCK_LLM_RESPONSES)}")

    # Test 1: Get mock response as it would come from LLM
    print(f"\n[TEST 1] Getting mock response from OpenAI provider")
    print("-" * 60)
    mock_text = get_mock_story_plan_text(child_name="Emma", age_group="5-7")
    print(f"Response type: {type(mock_text)}")
    print(f"Response length: {len(mock_text)} chars")
    print(f"First 100 chars: {mock_text[:100]}...")

    # Test 2: Parse the response as JSON
    print(f"\n[TEST 2] Parsing response as JSON")
    print("-" * 60)
    try:
        plan = json.loads(mock_text)
        print(f"✓ Valid JSON parsed")
        print(f"  - Plan keys: {list(plan.keys())}")
        print(f"  - Title: {plan.get('title')}")
        print(f"  - Age Band: {plan.get('age_band')}")
        print(f"  - Pages: {len(plan.get('pages', []))}")
        print(f"  - Characters: {len(plan.get('characters', []))}")
    except json.JSONDecodeError as e:
        print(f"✗ JSON Parse Error: {e}")
        return

    # Test 3: Validate the parsed plan
    print(f"\n[TEST 3] Validating parsed plan")
    print("-" * 60)
    validator = PlanValidator()

    # Test all age groups
    for age_group in ["2-4", "5-7", "8-12"]:
        plan = json.loads(get_mock_story_plan_text(child_name="Emma", age_group=age_group))
        result = validator.validate(plan, age_group=age_group)

        status = "✓ PASS" if result.ok else "✗ FAIL"
        print(f"\n{status} Age group '{age_group}':")

        if result.ok:
            print(f"  ✓ Validation passed")
            print(f"    - Title: {plan['title']}")
            print(f"    - Age Band: {plan['age_band']}")
            print(f"    - Final Page Count: {plan['final_page_count']}")
            print(f"    - Actual Pages: {len(plan['pages'])}")
        else:
            print(f"  ✗ Validation failed with {len(result.errors)} errors:")
            for i, error in enumerate(result.errors, 1):
                print(f"    {i}. {error}")

            # Deep dive into first error
            if result.errors:
                print(f"\n  Deep dive into first error:")
                first_error = result.errors[0]
                if "pages" in first_error.lower():
                    # Check pages structure
                    print(f"    Pages structure:")
                    for page_idx, page in enumerate(plan.get('pages', [])):
                        if not isinstance(page, dict):
                            print(f"      ✗ Page {page_idx}: Not a dict!")
                            continue
                        required = {"page_number", "story_role", "scene_description",
                                  "narration_sample", "child_action", "learning_goal_integration",
                                  "environment", "mood", "visual_continuity_notes"}
                        missing = required - set(page.keys())
                        if missing:
                            print(f"      ✗ Page {page_idx}: Missing {missing}")
                        else:
                            print(f"      ✓ Page {page_idx}: Has all required fields")

    # Test 4: Check validator requirements match mock response
    print(f"\n[TEST 4] Verifying validator requirements vs mock response")
    print("-" * 60)
    plan = json.loads(get_mock_story_plan_text(child_name="Emma", age_group="5-7"))

    required_top_level = ["title", "age_band", "final_page_count", "summary", "moral_theme",
                         "setting", "tone", "characters", "pages"]
    print(f"Top-level fields:")
    for field in required_top_level:
        has_it = "✓" if field in plan else "✗"
        print(f"  {has_it} {field}: {type(plan.get(field)).__name__}")

    if plan.get("pages"):
        first_page = plan["pages"][0]
        required_page = ["page_number", "story_role", "scene_description", "narration_sample",
                        "child_action", "learning_goal_integration", "environment", "mood",
                        "visual_continuity_notes"]
        print(f"\nPage fields (first page):")
        for field in required_page:
            has_it = "✓" if field in first_page else "✗"
            value = first_page.get(field)
            if isinstance(value, str):
                value_str = f"'{value[:30]}...'" if len(value) > 30 else f"'{value}'"
            elif isinstance(value, dict):
                value_str = f"dict with {len(value)} keys"
            else:
                value_str = str(type(value).__name__)
            print(f"  {has_it} {field}: {value_str}")

    print(f"\n{'='*80}")
    print("DIAGNOSTICS COMPLETE")
    print(f"{'='*80}")


if __name__ == "__main__":
    diagnose()
