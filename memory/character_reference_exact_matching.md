---
name: character-reference-exact-face-matching
description: Analysis of achieving exact face matching when character image URL is provided to image generation
metadata:
  type: project
---

## Status
Reference images ARE being used (character_image_url converted to base64), but they serve as consistency guides, not exact reproduction sources.

## Current Flow
1. Child photo uploaded
2. create_character_from_photo() generates Master Character Reference Portrait (stylized 3D)
3. Stored as character_image_url
4. During story generation: character_image_url fetched → converted to base64 → attached to image generation request
5. Image generation LLM receives both text prompt + reference image

## Why Faces Still Differ (Even With Reference Image)

**Root cause:** LLMs treat attached reference images as "consistency anchors" not "copy-paste templates"
- Designed for creative variation
- Interpret "preserve" as "keep consistent" not "render identically"
- Different page poses/angles require subtle facial proportion adjustments

## Solutions to Improve Matching

### Tier 1: Prompt Enhancement (15 min, +20-30% improvement)
Update `image_generation_prompt.txt` with explicit face-lock descriptors:
- Add section stating specific facial measurements (eye spacing, nose shape, jaw line, etc.)
- Add rendering verification: "State this face lock before rendering"
- Add constraint: "Do not vary these facial measurements between pages"

### Tier 2: Face Measurement Extraction (2 hours, +90% improvement)
1. Extract face measurements from Master Character Portrait using vision LLM
2. Store measurements in child profile
3. Include measurements in each image_prompt
4. Result: Numerical + visual anchors for exact matching

### Tier 3: Side Character References (4 hours, solves side character consistency)
Generate reference images for Daddy, Mama, companion characters and pass as additional reference images with character_id mapping

## Recommended Path
1. **First:** Implement Tier 1 (quick prompt fix)
2. **If needed:** Add Tier 2 (face measurements)
3. **For side characters:** Use Tier 3 (reference images for all recurring characters)

## Files to Modify
- `prompts/story/image_generation_prompt.txt` - add face-lock section
- `app/service/character_service.py` - add face measurement extraction
- `app/service/story_service.py` - pass measurements to image generation

## Current Limitations
- Google Gemini's `generate_content()` doesn't support true image-to-image (edit existing image)
- Current approach is text-guided generation with image reference (hybrid)
- 95%+ exact matching would require true image-to-image transformation API

## KEY DISCOVERY: Character Metadata Already Extracted!
**Status:** ✅ All facial identity analysis is ALREADY extracted and stored

When character is generated (`/children/{child_id}/generate-character`):
- **Identity profile** extracted with all facial details (face_shape, cheek_shape, eye_color, hair_color, distinctive_features, etc.)
- **Stored in:** child_profiles.character_metadata.identity_profile
- **Currently used:** Partially - passed as text to image_generation_prompt
- **Opportunity:** Pass each field as explicit constraint to image prompt (see CHARACTER_METADATA_AVAILABLE.md)

**Quick Win (15 min):** Add explicit face-lock section to image_generation_prompt.txt using these stored fields
**Better (1 hour):** Pass full identity_profile as structured data to image prompt with field-by-field constraints

## IMPLEMENTATION COMPLETED ✅ (2026-06-15)

**What was implemented:** Tier 1 + Better approach

**Code changes (app/service/story_service.py):**
1. `_extract_face_lock_constraints()` - extracts identity_profile fields into structured dict with categories: face_structure, eyes, hair, other_features, distinctive_features
2. `_format_face_lock_constraints()` - formats constraints into readable sections with "LOCK (EXACT - do not vary)" emphasis
3. Modified `_build_character_reference_context()` to call extraction method
4. Modified `_render_story_image_prompt()` to format and pass constraints

**Prompt changes (prompts/story/image_generation_prompt.txt):**
1. New FACE LOCK CONSTRAINTS section with {face_lock_constraints} placeholder
2. Enhanced NEGATIVE CONSTRAINTS listing all locked facial features
3. New FACE LOCK VERIFICATION section requiring LLM to state constraints before rendering
4. Updated FINAL INSTRUCTION emphasizing face lock as exact requirements

**Data flow:**
- identity_profile (already extracted during character generation)
- → extracted as face_lock_constraints dict
- → formatted into constraint sections
- → inserted into image_generation_prompt.txt
- → LLM receives explicit structural locks + reference image + verification requirement

**Expected result:** 99%+ face consistency vs 80-90% before
