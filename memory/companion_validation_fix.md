---
name: companion-appearance-validation
description: Fix for image plan validation failing on empty companion.appearance when companion not used in story
metadata:
  type: project
---

## Issue
Image plan validation failed with: "visual_bible.companion.appearance must be a non-empty string when provided"

When story plan LLM created a companion object structure but didn't populate appearance (usually when no companion in story).

## Root Cause
Validator was checking if companion object existed, then validating appearance field regardless of whether companion was actually used in the story.

Story plan prompt enhancement added new companion fields (character_id, hair_lock, etc.), causing LLM to create structure even when no companion exists.

## Fix Applied (2026-06-16)

### Validator Logic (`app/service/image_plan_validator.py`)
Changed validation to only check appearance if companion.name is populated (indicating companion is used).

**Logic:** 
- If companion.name is empty/null → companion not used → skip appearance validation
- If companion.name exists → companion IS used → appearance MUST be populated

### Prompt Guidance (`prompts/story/story_plan_prompt.txt`)
Added explicit instructions:
- "If NO companion appears in story, leave companion object empty/null"
- "If companion HAS a name in visual_bible, it MUST have appearance description"

## Result
✅ Allows stories without companions
✅ Requires appearance only if companion has name
✅ Prevents false validation failures

## Status: FIXED
Both validator and prompt updated. Story generation should now work for stories with and without companions.
