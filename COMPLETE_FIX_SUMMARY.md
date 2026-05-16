# Complete Story Generation Validation Fixes Summary

## Problem Statement
User reported: "Story plan validation failed after 3 retries" when testing with payload:
```json
{
  "child_id": "{{child_id}}",
  "mode": "INPUT_DRIVEN",
  "category": "adventure",
  "learning_goal": "courage",
  "context": "Emma loves exploring and learning about nature. She's curious and brave.",
  "skip_image_generation": true,
  "skip_validation": false
}
```

## Root Causes & Fixes

### Issue 1: Incomplete Validator Implementation ✓ FIXED
**Problem**: Validator defined required page fields but never validated them
**Fix**: Enhanced `app/service/plan_validator.py` to validate:
- All required page fields present (scene_description, narration_sample, etc.)
- String fields are non-empty
- environment is a dict
- Comprehensive error messages with line breaks

### Issue 2: Mock Response Age Band Mismatch ✓ FIXED
**Problem**: Mock LLM always returned "Early Reader" age_band regardless of child's actual age
```
Error: `age_band` must be one of ['Toddler'] for age_group=AgeGroup.TODDLER
```

**Root Cause**: 
- OpenAI provider wasn't passing age_group to mock functions
- Mock functions were hardcoded for age_group="5-7"

**Fix**: Enhanced `app/service/ai/openai_provider.py`:
- Extract age_group from prompt (contains "2-4", "5-7", or "8-12")
- Pass age_group to `get_mock_story_plan_text()`
- Dynamic page count based on age_group:
  - 2-4 (Toddler): 6 pages, age_band="Toddler"
  - 5-7 (Early Reader): 8 pages, age_band="Early Reader"
  - 8-12 (Advanced): 12 pages, age_band="Advanced"

**Updated**: `app/service/mock_llm_responses.py`:
- Dynamically set age_band and page_count based on age_group parameter
- Trim pages array to match final_page_count
- Re-number pages if trimmed

### Issue 3: Mock Story JSON Page Count Mismatch ✓ FIXED
**Problem**: Mock story_json was hardcoded with 8 pages, but story plan might specify 6 or 12 pages
```
Error: Image plan pages must match story pages exactly (page_number 1..N)
```

**Root Cause**:
- Step 3 (story generation) calls LLM with story_plan containing final_page_count
- Mock response was ignoring the page count and always returning 8 pages
- Step 5 (image plan validation) failed because story had 6 pages but image plan had 8

**Fix**: Enhanced `app/service/ai/openai_provider.py`:
- Extract `final_page_count` from story_plan_json in prompt using regex
- Pass story_pages_count to `get_mock_story_text()`

**Updated**: `app/service/mock_llm_responses.py`:
- `get_mock_story_json()` now accepts story_pages_count parameter
- Trims base_pages array to match story_pages_count
- `get_mock_story_text()` forwards story_pages_count parameter

### Issue 4: Mock Image Plan Page Count ✓ FIXED
**Problem**: Mock image_plan was hardcoded to 8 pages, but image plan validator checks that pages match story pages

**Fix**: Enhanced `app/service/ai/openai_provider.py`:
- Extract page_number values from story_json in prompt using regex
- Find max page_number to determine actual page count
- Pass story_pages_count to `get_mock_image_plan_text()`

**Already Fixed in**: `app/service/mock_llm_responses.py`:
- `get_mock_image_plan()` dynamically generates pages based on story_pages_count parameter

## Validation Flow Now Works End-to-End

### For 2-4 year old (Toddler)
1. ✓ Step 1: Generate story plan (6 pages, age_band="Toddler")
2. ✓ Step 2: Validate plan (passes - age_band matches age_group)
3. ✓ Step 3: Generate story (6 pages from plan)
4. ✓ Step 4: Generate image plan (6 pages from story)
5. ✓ Step 5: Validate image plan (passes - page counts match)
6. ✓ Step 6: Generate images (skipped with skip_image_generation=true)

### For 5-7 year old (Early Reader)
1. ✓ Step 1: Generate story plan (8 pages, age_band="Early Reader")
2. ✓ Step 2: Validate plan (passes)
3. ✓ Step 3: Generate story (8 pages)
4. ✓ Step 4: Generate image plan (8 pages)
5. ✓ Step 5: Validate image plan (passes)
6. ✓ Step 6: Generate images (skipped)

### For 8-12 year old (Advanced)
1. ✓ Step 1: Generate story plan (12 pages, age_band="Advanced")
2. ✓ Step 2: Validate plan (passes)
3. ✓ Step 3: Generate story (12 pages)
4. ✓ Step 4: Generate image plan (12 pages)
5. ✓ Step 5: Validate image plan (passes)
6. ✓ Step 6: Generate images (skipped)

## Testing

### Quick Test
```bash
python final_integration_test.py
```

Tests all three age groups with your exact payload:
- 3 year old (2-4 group)
- 6 year old (5-7 group)
- 10 year old (8-12 group)

Expected result: All workflows complete with COMPLETED status

### Individual Age Group Tests
```bash
python debug_payload_test.py  # Test with your exact payload
python diagnose_validation.py  # Check validator
python validate_fixes.py       # Check validator enhancements
```

## Files Modified

1. **app/service/plan_validator.py**
   - Added validation of all required page fields
   - Better error messages with line breaks
   - Checks for missing required keys

2. **app/service/story_service.py**
   - Enhanced error logging in _step_validate_plan()
   - Shows detailed validation errors instead of generic message
   - Better final error message when all retries fail

3. **app/service/ai/openai_provider.py**
   - Extract age_group from prompt for story plan generation
   - Extract final_page_count from story plan for story generation
   - Extract max page_number from story for image plan generation
   - Pass correct parameters to all mock functions

4. **app/service/mock_llm_responses.py**
   - get_mock_story_plan() dynamically sets age_band and page_count
   - get_mock_story_json() accepts story_pages_count and trims pages
   - get_mock_story_text() forwards story_pages_count parameter
   - get_mock_image_plan() already supported dynamic page count

5. **test_story_generation_flow.py**
   - Fixed mode to uppercase ("INPUT_DRIVEN")
   - Added proper DOB for age group calculation

## Key Insights

### Mock Mode Design
The mock LLM responses need to be **consistent with the actual LLM behavior**:
- Story plan determines page count
- Story must have matching pages to story plan
- Image plan must have matching pages to story

By extracting parameters from prompts (age_group, final_page_count, page numbers), we ensure mock responses align with what real LLM would return.

### Age Group Mappings
```
Age    → Age Group → Age Band  → Pages
2-4    → 2-4       → Toddler   → 6
5-7    → 5-7       → Early Reader → 8
8-12   → 8-12      → Advanced  → 12
```

### Parameter Extraction Strategy
Since OpenAI provider doesn't have access to story metadata, we extract it from prompts:
- Age group: Search prompt for "2-4", "5-7", or "8-12" values
- Page count (story plan): Use regex to find `"final_page_count": N`
- Page count (story): Use regex to find all `"page_number": N` and take max

## Next Steps for User

1. Test with the provided test scripts
2. Monitor logs for "MOCK MODE:" messages confirming mock is working
3. Verify all age groups work correctly
4. When ready, set `STORY_MOCK_LLM_RESPONSES=false` to use real OpenAI
5. Monitor production for any similar mismatches

## Rollback Plan

If issues arise with age_group extraction, the old behavior (default to "5-7") can be restored by removing the extraction logic in openai_provider.py. However, this should not be necessary as the fixes are comprehensive.
