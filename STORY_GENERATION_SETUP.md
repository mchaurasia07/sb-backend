# Story Generation API - Setup & Testing Guide

## Quick Setup

### 1. Enable Mock LLM Mode (for testing without OpenAI calls)

Add to your `.env` file:

```env
STORY_MOCK_LLM_RESPONSES=true
STORY_TEXT_MODEL=gpt-4o
STORY_IMAGE_MODEL=dall-e-3
STORY_IMAGE_SIZE=1024x1024
STORY_IMAGE_QUALITY=standard
STORY_MAX_RETRIES=3
STORY_GENERATION_ENABLED=true
```

### 2. Run Test Script

```bash
cd d:/storybook/workspace/sb-backend
python test_story_generation_flow.py
```

This will:
- ✓ Create test user and child profile
- ✓ Create story with PENDING status
- ✓ Execute all 6 workflow steps with mock responses
- ✓ Verify database records
- ✓ Display audit trail
- ✓ Confirm flow works end-to-end

**Expected output:**
```
================================================================================
TESTING STORY GENERATION FLOW (MOCK MODE)
================================================================================

✓ Mock LLM Mode: ENABLED
✓ Database tables created
✓ Created test user: ...
✓ Created test child: ...
✓ Story Request:
  - Mode: input_driven
  - Age Group: 5-7
  - Category: adventure
  - Learning Goal: courage

--------------------------------------------------------------------------------
STEP 1: Creating story record...
✓ Story created: ...
✓ Status: PENDING

--------------------------------------------------------------------------------
EXECUTING WORKFLOW (6 STEPS)...

✓ Workflow completed successfully!
✓ Final Status: COMPLETED
✓ Title: Emma's Amazing Adventure
✓ Pages: 8

--------------------------------------------------------------------------------
AUDIT TRAIL (Story Steps):
  ✓ Step 1: STORY_PLAN_GENERATION - COMPLETED (retries: 0)
  ✓ Step 2: STORY_PLAN_VALIDATION - COMPLETED (retries: 0)
  ✓ Step 3: STORY_GENERATION - COMPLETED (retries: 0)
  ✓ Step 4: IMAGE_PLAN_GENERATION - COMPLETED (retries: 0)
  ✓ Step 5: IMAGE_PLAN_VALIDATION - COMPLETED (retries: 0)
  ✓ Step 6: IMAGE_GENERATION - SKIPPED (test mode)

================================================================================
✓ COMPLETE FLOW TEST PASSED!
================================================================================
```

## API Endpoints

### Generate Story (Async with Polling)

```bash
# Create story (returns immediately)
curl -X POST "http://localhost:8000/api/v1/stories/generate" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "child_id": "uuid-here",
    "mode": "input_driven",
    "age_group": "5-7",
    "category": "adventure",
    "learning_goal": "courage",
    "context": "Emma discovers a magical garden",
    "skip_image_generation": true,
    "skip_validation": false
  }'

# Response (202 Accepted):
{
  "success": true,
  "data": {
    "id": "story-uuid",
    "status": "PENDING",
    "title": null,
    "pages": []
  },
  "message": "Story generation started successfully"
}

# Poll for completion
curl -X GET "http://localhost:8000/api/v1/stories/story-uuid" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Response (when COMPLETED):
{
  "success": true,
  "data": {
    "id": "story-uuid",
    "status": "COMPLETED",
    "title": "Emma's Amazing Adventure",
    "moral": "courage and kindness",
    "summary": "Join Emma on an exciting adventure...",
    "pages": [
      {
        "page_number": 1,
        "page_type": "cover",
        "text": "",
        "image_url": "http://localhost:8000/photo/stories/uuid/cover.png"
      },
      {
        "page_number": 1,
        "page_type": "page",
        "text": "One sunny morning, Emma discovered a secret garden...",
        "image_url": "http://localhost:8000/photo/stories/uuid/page_1.png"
      }
    ]
  }
}
```

### Get Story Steps (Audit Trail)

```bash
curl -X GET "http://localhost:8000/api/v1/stories/story-uuid/steps" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Response:
{
  "success": true,
  "data": [
    {
      "id": "step-uuid",
      "step_name": "STORY_PLAN_GENERATION",
      "status": "COMPLETED",
      "retry_count": 0,
      "error_message": null,
      "started_at": "2026-05-16T10:00:00Z",
      "completed_at": "2026-05-16T10:05:00Z"
    },
    {
      "id": "step-uuid-2",
      "step_name": "STORY_GENERATION",
      "status": "COMPLETED",
      "retry_count": 0,
      "error_message": null,
      "started_at": "2026-05-16T10:05:00Z",
      "completed_at": "2026-05-16T10:10:00Z"
    }
  ]
}
```

### List Stories

```bash
curl -X GET "http://localhost:8000/api/v1/stories?child_id=child-uuid" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Response:
{
  "success": true,
  "data": [
    {
      "id": "story-uuid-1",
      "status": "COMPLETED",
      "title": "Emma's Amazing Adventure",
      "created_at": "2026-05-16T10:00:00Z"
    },
    {
      "id": "story-uuid-2",
      "status": "IN_PROGRESS",
      "title": null,
      "created_at": "2026-05-16T11:00:00Z"
    }
  ]
}
```

## Generation Modes

### Input-Driven Mode

Generate story based on child preferences and learning goals:

```json
{
  "mode": "input_driven",
  "age_group": "5-7",
  "category": "adventure",
  "learning_goal": "courage",
  "context": "Emma loves exploring and learning about nature"
}
```

### Event-Driven Mode

Convert a real-life event into a story:

```json
{
  "mode": "event_driven",
  "age_group": "5-7",
  "event_description": "Today Emma learned to ride a bike without training wheels for the first time!"
}
```

## Testing Flags

### skip_image_generation

Skip DALL-E image generation (saves time and cost during testing):

```json
{
  "skip_image_generation": true
}
```

When enabled:
- All 6 workflow steps still execute
- Image generation step is skipped
- Story pages created without image URLs
- Fast workflow completion (~30 seconds instead of 2+ minutes)

### skip_validation

Skip story plan validation retries:

```json
{
  "skip_validation": true
}
```

When enabled:
- Plan validation step skipped
- No retry logic applied
- Faster but less robust

## Switching Between Mock and Real LLM

### Use Mock (Testing)

```env
STORY_MOCK_LLM_RESPONSES=true
```

- Returns valid JSON responses instantly
- No OpenAI API calls
- Perfect for testing flow and integration
- Cost: $0

### Use Real OpenAI (Production)

```env
STORY_MOCK_LLM_RESPONSES=false
OPENAI_API_KEY=sk-...
```

- Calls actual OpenAI APIs
- Generates unique, creative stories
- Validates responses with real LLM
- Cost: ~$0.50-2.00 per story (depending on image quality)

## Database Tables

### stories

Stores story metadata and workflow state:

```sql
SELECT id, title, status, current_step, generation_mode, age_group, 
       created_at, updated_at FROM stories WHERE user_id = ?;
```

### story_steps

Audit trail with full LLM prompts and responses:

```sql
SELECT step_name, status, retry_count, error_message, started_at, completed_at 
FROM story_steps WHERE story_id = ?;
```

### story_pages

Individual pages with text and image URLs:

```sql
SELECT page_number, page_type, text, image_url FROM story_pages 
WHERE story_id = ? ORDER BY page_number;
```

## Troubleshooting

### Story Generation Hangs

Check if background task is running:
- Query `story.status` - should progress from PENDING → IN_PROGRESS → COMPLETED
- Check logs for background task execution

### Mock Responses Not Used

Verify `.env`:
```bash
# Should show true
grep STORY_MOCK_LLM_RESPONSES .env
```

### Image URLs Return 404

Check storage directory:
```bash
ls -la photo/stories/{story_id}/
# Should contain: cover.png, page_1.png, page_2.png, ..., back_cover.png
```

### Validation Failures

View audit trail:
```bash
GET /api/v1/stories/{story_id}/steps
```

Each failed step includes `error_message` with validation details.

## Performance Expectations

### With Mock LLM (STORY_MOCK_LLM_RESPONSES=true)

- Story generation: 5-10 seconds
- No API latency
- All database writes complete

### With Real OpenAI (STORY_MOCK_LLM_RESPONSES=false)

- Plan generation: 10-15 seconds
- Story generation: 10-15 seconds
- Image plan generation: 5-10 seconds
- Image generation (8 images): 2-4 minutes
- **Total: 30-60 seconds** (depending on generation retries)

## Next Steps

1. ✓ Run test script to verify flow works
2. ✓ Test via API with mock mode enabled
3. ✓ Verify database records and audit trail
4. ✓ Switch to real OpenAI (set STORY_MOCK_LLM_RESPONSES=false)
5. ✓ Test end-to-end with images
6. ✓ Monitor story_steps table for any validation failures
7. ✓ Deploy to production

## Architecture Summary

```
POST /api/v1/stories/generate
    ↓
[Route Handler]
    ↓
StoryService.generate_story_async()
    ├─ Validate child & character image
    ├─ Create Story record (status=PENDING)
    └─ Return immediately (202 Accepted)
    ↓
[Background Task]
    ↓
StoryService.execute_workflow()
    ├─ Step 1: Generate story plan (LLM)
    ├─ Step 2: Validate plan (with 3-retry fallback)
    ├─ Step 3: Generate story text (LLM)
    ├─ Step 4: Generate image plan (LLM)
    ├─ Step 5: Validate image plan
    └─ Step 6: Generate images (DALL-E)
    ↓
[Update database]
    ├─ Story status → COMPLETED
    ├─ Create story_pages records
    └─ Log all story_steps
    ↓
Client polls: GET /api/v1/stories/{story_id}
    ↓
[Story Response with pages & images]
```
