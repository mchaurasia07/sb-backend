# Postman Collection - Story Generation API Testing Guide

## 📥 Import Collection

1. Open Postman
2. Click **Import** (top left)
3. Select **Story_Generation_API.postman_collection.json**
4. Collection imported ✓

---

## ⚙️ Setup Variables

Before running requests, set these environment variables in Postman:

### 1. **base_url**
```
http://localhost:8000
```

### 2. **jwt_token**
Get from login request or your existing token:
```
Bearer eyJhbGc...
```

### 3. **child_id**
Get from "Get Child ID" request under Setup folder

---

## 🚀 Quick Test Flow

### Step 1: Run Setup Requests
1. Go to **Setup** folder
2. Run **"1. Login and Get Token"** - Copy the JWT token
3. Set `{{jwt_token}}` variable with the token
4. Run **"2. Get Child ID"** - Copy the child_id
5. Set `{{child_id}}` variable

### Step 2: Generate Stories (Input-Driven)
1. Go to **Story Generation - Input Driven Mode**
2. Run **"1. Generate Story (Input-Driven, Skip Images, Mock Mode)"**
   - Returns immediately with story_id
   - Status: PENDING
3. Automatically saves story_id to `{{story_id}}`

### Step 3: Poll for Completion
1. Go to **Story Retrieval & Polling**
2. Run **"2. Get Story (Wait for Completion - Polling Loop)"**
   - Shows current status
   - Keep running until COMPLETED
3. When COMPLETED: View title, moral, pages count

### Step 4: Check Audit Trail
1. Run **"3. Get Story Steps (Audit Trail)"**
2. View all 6 steps with:
   - Step name (STORY_PLAN_GENERATION, etc.)
   - Status (COMPLETED/FAILED)
   - Retry count
   - Error messages if any

---

## 📋 All Scenarios Included

### Input-Driven Stories
| Request | Description | Skip Images | Mode |
|---------|-------------|-------------|------|
| Input-Driven, Skip Images | Fast test | ✓ | input_driven |
| Input-Driven, Full Images | Complete story with 10 images | ✗ | input_driven |
| Input-Driven, Different Age | Infant Toddler (0-3) story | ✓ | input_driven |

### Event-Driven Stories
| Request | Description | Skip Images | Mode |
|---------|-------------|-------------|------|
| Event-Driven, Skip Images | Real event as story | ✓ | event_driven |
| Event-Driven, Full Images | Event with 10 images | ✗ | event_driven |
| Event-Driven, Another Event | Sibling help story | ✓ | event_driven |

### Advanced Scenarios
| Request | Description |
|---------|-------------|
| Skip Validation | Fast generation (no retries) |
| All Features | Event + Full Images + Validation |
| Multiple Categories | Different category test |

---

## 📊 Response Understanding

### Generate Story Response (202 Accepted)
```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "PENDING",
    "title": null,
    "pages": []
  },
  "message": "Story generation started successfully"
}
```

**Status meanings:**
- `PENDING` - Not started yet
- `IN_PROGRESS` - Executing workflow
- `COMPLETED` - Ready with full content
- `FAILED` - Generation failed

### Get Story Response (When COMPLETED)
```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
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

### Story Steps Response (Audit Trail)
```json
{
  "success": true,
  "data": [
    {
      "id": "step-uuid-1",
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

---

## ⏱️ Timing Expectations

### With Mock LLM (STORY_MOCK_LLM_RESPONSES=true)
- Generation: 5-10 seconds
- All requests complete instantly
- Perfect for testing

### With Real OpenAI (skip_image_generation=true)
- Generation: 30-50 seconds
- Story plan: 10-15 sec
- Story generation: 10-15 sec
- Image plan: 5-10 sec

### With Real OpenAI (full with images)
- Generation: 3-5 minutes
- Includes 2-4 min for DALL-E image generation
- Most expensive but complete

---

## 🧪 Testing Checklist

- [ ] Run login and get JWT token
- [ ] Get child_id from children endpoint
- [ ] Generate input-driven story (skip images)
- [ ] Poll GET story endpoint until COMPLETED
- [ ] View story title and page count
- [ ] Check audit trail (all 6 steps)
- [ ] Generate event-driven story (skip images)
- [ ] Check different age groups
- [ ] Generate with images enabled
- [ ] Verify images saved in `photo/stories/`
- [ ] Test skip_validation flag
- [ ] List all stories for user

---

## 🐛 Troubleshooting

### Story stays in PENDING
- Check if mock mode is enabled: `STORY_MOCK_LLM_RESPONSES=true`
- Check logs for background task errors
- Wait 10-15 seconds if using real LLM

### 404 on Get Story
- Verify story_id is correct
- Check you're using same child_id

### Images return 404
- Check `photo/stories/{story_id}/` directory exists
- Verify image generation wasn't skipped
- Images take 2-4 minutes to generate with real OpenAI

### Validation keeps failing
- Check story_id variable is set correctly from generation response
- Verify child has character_image_url
- Check error_message in audit trail

---

## 💡 Pro Tips

### Tip 1: Auto-Save Story IDs
The collection already saves story IDs automatically:
- Input-driven stories → `{{story_id}}`
- Full image stories → `{{story_id_full}}`
- Event stories → `{{story_id_event}}`

### Tip 2: Use Pre-request Scripts
Check the "Pre-request Script" tab on requests to see:
- Variable setup
- Request customization

### Tip 3: Check Test Scripts
Check the "Tests" tab to see:
- Automatic story_id extraction
- Status logging
- Response validation

### Tip 4: Polling Pattern
```
POST /generate → Get story_id → Poll GET /stories/{id} → Check status
```

Keep polling every 3-5 seconds until status = COMPLETED

### Tip 5: Audit Trail for Debugging
If story generation fails:
```
GET /stories/{id}/steps → Check error_message of failed step
```

---

## 🔄 Recommended Testing Order

1. **Setup** (2 min)
   - Login
   - Get child_id

2. **Mock Mode Testing** (10 min)
   - Set `STORY_MOCK_LLM_RESPONSES=true` in .env
   - Generate input-driven (skip images)
   - Poll until COMPLETED
   - Generate event-driven (skip images)
   - List stories

3. **Real OpenAI Testing** (20 min)
   - Set `STORY_MOCK_LLM_RESPONSES=false`
   - Generate input-driven (skip images)
   - Poll until COMPLETED
   - Check audit trail
   - Generate event-driven (skip images)

4. **Full Image Testing** (30 min)
   - Generate with images enabled
   - Wait 3-5 minutes
   - Verify images in file system
   - Check image URLs work

---

## 📝 Notes

- All requests have descriptions explaining what they do
- Collection includes 15+ pre-built test scenarios
- Variables auto-populate through test scripts
- Use for both development and debugging
- Perfect for frontend integration testing

---

## 🎯 Example Full Flow

```
1. POST /login
   ↓ Copy jwt_token

2. GET /children
   ↓ Copy child_id

3. POST /stories/generate (input-driven, skip images)
   ↓ Get story_id

4. GET /stories/{story_id}
   ↓ Check status (keep calling until COMPLETED)

5. GET /stories/{story_id}/steps
   ↓ View audit trail

6. GET /stories
   ↓ List all stories
```

That's it! You have a complete story generation workflow! 🎉
