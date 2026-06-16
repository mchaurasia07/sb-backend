---
name: character-consistency-hair-beard
description: Root cause of side character appearance inconsistency (daddy beard, mama hair length varying between pages)
metadata:
  type: project
---

## Problem Statement
Side characters show inconsistent appearance across story pages:
- Daddy appears with/without beard randomly
- Mama appears with varying hair lengths
- Other recurring characters change outfit colors, eye appearance, etc.

## Root Cause
**PRIMARY:** Story Plan generation prompt does NOT enforce detailed character locking for father/mother/recurring characters.

**Why:** The `story_plan_prompt.txt` defines only `father.appearance: ""` and `mother.appearance: ""` without requiring specific details like:
- Hair color, length, style
- Facial hair (beard yes/no, style, color)
- Eye color
- Outfit specifics

This vagueness propagates downstream:
1. Story Planner creates "Daddy: tall kind man" (no beard info)
2. Image Planner cannot lock beard detail if it wasn't in story_plan
3. Image Generator invents beard presence → INCONSISTENCY between pages

## Solution Approach
Three-layer fix (must fix all three):

### Layer 1: story_plan_prompt.txt (CRITICAL)
Change father/mother structure to include:
- character_id (lowercase snake_case)
- hair_lock (color, length, style)
- facial_hair_lock (beard yes/no, if yes: style/color)
- eye_color
- outfit_lock
- signature_item

Add explicit instruction to LLM: "For EVERY character, lock these details without ambiguity"

### Layer 2: image_plan_prompt.txt (IMPORTANT)
Add validation that recurring_characters have these required locked fields
Add instruction: "Repeat locked details in EVERY image_prompt"

### Layer 3: image_generation_prompt.txt (SUPPORTING)
Add explicit rendering verification section that states:
"Daddy: [beard yes/no], [hair color/length/style], [eye color] - DO NOT CHANGE"

## Implementation Status
- Analysis document created: `/CONSISTENCY_ANALYSIS.md`
- Prompts not yet modified (awaiting user decision)

## Why This Matters
Without this fix: Character inconsistency persists across the entire story
With this fix: All side characters will have locked, consistent appearance
