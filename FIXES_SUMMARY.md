# Story Plan Validation Fixes Summary

## Problem
Story plan validation was failing after 3 retries with error message "Story plan validation failed after 3 retries" without showing the actual validation errors.

## Root Causes Found & Fixed

### 1. **Incomplete Validator Implementation** ✓ FIXED
**Issue**: The validator defined `required_page_keys` set but never actually used it to validate pages. It only checked `page_number` and `story_role`, missing validation of:
- `scene_description`
- `narration_sample`
- `child_action`
- `learning_goal_integration`
- `mood`
- `visual_continuity_notes`
- `environment` (must be dict)

**Fix**: Enhanced `app/service/plan_validator.py` to:
```python
# Check for missing required keys
missing_keys = required_page_keys - set(page.keys())
if missing_keys:
    errors.append(f"pages[{idx}] missing required fields: ...")

# Validate string fields are non-empty
for field in [...]:
    value = page.get(field)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        errors.append(f"pages[{idx}].{field} must be a non-empty string.")

# Validate environment is dict
environment = page.get("environment")
if environment is not None and not isinstance(environment, dict):
    errors.append(f"pages[{idx}].environment must be an object.")
```

### 2. **Poor Error Visibility** ✓ FIXED
**Issue**: When validation failed, logs only showed "Plan validation failed after 3 attempts" without showing actual errors.

**Fix**: Enhanced error logging in `app/service/story_service.py`:
```python
# Better formatting in _step_validate_plan()
error_list = "\n".join([f"  - {err}" for err in result.errors])
logger.warning(f"Story {story.id}: Plan validation failed on attempt {attempt}:\n{error_list}")

# More detailed final error when all retries fail
final_result = self.plan_validator.validate(plan, age_group=story.age_group)
error_details = "\n".join([f"  - {err}" for err in final_result.errors])
error_msg = f"Plan validation failed after {self.MAX_RETRIES} attempts:\n{error_details}"
```

### 3. **Mock Mode Returning Same Response Every Retry** ⚠️ LIMITATION
**Observation**: In mock mode, retrying doesn't help because the mock response is identical every time. However:
- **Mock response IS valid** - Contains all required fields per the schema
- **First attempt should pass** - No validation errors should occur
- **Retry mechanism works as designed** - For real LLM, retries with error feedback fix issues

### 4. **Test Script Issues** ✓ FIXED
**Issues**:
- Used lowercase `"input_driven"` instead of uppercase `"INPUT_DRIVEN"`
- Child profile had `dob=None`, preventing age_group calculation

**Fixes** in `test_story_generation_flow.py`:
```python
# Before
mode="input_driven"
dob=None

# After  
mode="INPUT_DRIVEN"
dob = datetime.now().date() - timedelta(days=6*365)  # 6-year-old
```

## Mock Response Structure Verification

The mock response in `get_mock_story_plan()` contains:
```
✓ title: "Emma's Amazing Adventure"
✓ age_band: "Early Reader" (for age_group="5-7")
✓ final_page_count: 8
✓ pages: 8 items, each with:
  ✓ page_number: 1-8 (sequential)
  ✓ story_role: "introduction", "setup", "conflict", etc. (valid roles)
  ✓ scene_description: non-empty string
  ✓ narration_sample: non-empty string
  ✓ child_action: non-empty string
  ✓ learning_goal_integration: non-empty string
  ✓ environment: dict with lighting, time_of_day, dominant_colors
  ✓ mood: non-empty string
  ✓ visual_continuity_notes: non-empty string
✓ characters: [hero, companion] with all required fields
✓ summary: non-empty string
✓ moral_theme: non-empty string
✓ setting: non-empty string
✓ tone: non-empty string
```

## How to Test

### Option 1: Run Complete End-to-End Test
```bash
cd d:/storybook/workspace/sb-backend
python test_complete_flow.py
```

### Option 2: Test Validator Directly
```bash
python validate_fixes.py
```

### Option 3: Test via API with Postman
1. Ensure `.env` has `STORY_MOCK_LLM_RESPONSES=true`
2. Create a child profile with character image
3. POST `/api/v1/stories/generate` with:
   ```json
   {
     "child_id": "uuid",
     "mode": "INPUT_DRIVEN",
     "category": "adventure",
     "learning_goal": "courage"
   }
   ```
4. Poll GET `/api/v1/stories/{story_id}` to track progress
5. When complete, check audit trail: GET `/api/v1/stories/{story_id}/steps`

## Expected Behavior After Fixes

1. **Mock Mode**: Story should complete on first attempt with no retries needed
2. **Validation Success**: All 6 steps should complete (or fewer if skip_image_generation=true)
3. **Error Visibility**: If validation fails, detailed errors are logged
4. **Audit Trail**: Each step (including retries) is recorded in story_steps table
5. **Status Progression**: PENDING → IN_PROGRESS → COMPLETED (or FAILED)

## Files Modified

1. `app/service/plan_validator.py` - Enhanced page field validation
2. `app/service/story_service.py` - Better error logging
3. `test_story_generation_flow.py` - Fixed mode and DOB
4. Created validation test scripts for verification

## Next Steps for User

1. Ensure `.env` has correct settings:
   ```
   STORY_MOCK_LLM_RESPONSES=true  # For testing
   ACCESS_TOKEN_EXPIRE_MINUTES=525600  # 1 year
   ```

2. Test the flow:
   - Run `python test_complete_flow.py`
   - Or test via API with Postman
   - Monitor logs for detailed error messages

3. If issues remain, check logs for:
   - `[WORKFLOW] Starting for story` - Workflow started
   - `Story X: Plan validation passed on attempt 1` - Success
   - `Story X: Plan validation failed on attempt Y:` - Failure with details
   - Each step shows up as COMPLETED/FAILED/IN_PROGRESS

## Validation Schema Reference

From `story_plan_prompt.txt` and validated in `plan_validator.py`:

**Top-Level Fields**:
- title (string, non-empty)
- age_band ("Toddler" | "Early Reader" | "Advanced") - must match age_group
- final_page_count (positive integer) - must equal pages.length
- summary (string, non-empty)
- moral_theme (string, non-empty)
- setting (string, non-empty)
- tone (string, non-empty)
- characters (non-empty array)
- pages (non-empty array)

**Character Fields**:
- name (string, non-empty)
- role ("hero" | "companion" | "supporter" | "reframed_custom")
- anchor_description (string, non-empty)
- visual_traits (dict with hair, clothing, signature_item)

**Page Fields** (all required):
- page_number (positive integer, sequential 1..N)
- story_role ("introduction" | "setup" | "conflict" | "escalation" | "climax" | "resolution")
- scene_description (string, non-empty)
- narration_sample (string, non-empty)
- child_action (string, non-empty)
- learning_goal_integration (string, non-empty)
- environment (dict)
- mood (string, non-empty)
- visual_continuity_notes (string, non-empty)

**Optional Page Fields**:
- hook_to_next (string, can be null)
- image_gen_prompt (string)
