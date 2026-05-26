# Story Narration Feature - Developer Quick Start

## Overview

The story narration feature generates MP3 audio for each page of a story using Google Cloud Text-to-Speech and creates word-level timestamps for read-along functionality.

## Quick Facts

| Aspect | Detail |
|--------|--------|
| **Endpoint** | `POST /api/v1/stories/{story_id}/generate-narration` |
| **Input** | Story UUID, optional overwrite flag |
| **Output** | Updated story JSON with audio_url, duration, word_timestamps per page |
| **Storage** | Local filesystem: `audio/{story_id}/page_{page_number}.mp3` |
| **Voice** | Female, Indian English (en-IN), Neural |
| **Pace** | Configurable (slow→0.85x, medium→1.0x speaking rate) |
| **Time** | ~5-10 seconds per page (depending on text length) |

## Files Created

```
app/
├── utils/
│   ├── word_timestamps.py          # Timestamp generation algorithm
│   ├── google_tts_utils.py         # Google TTS API wrapper
├── service/
│   ├── story_narration_service.py  # Orchestration logic
├── routes/v1/
│   ├── story_narration_routes.py   # REST endpoint
├── model/request/
│   ├── story_narration.py          # Request DTOs
```

## Files Modified

```
app/
├── main.py                         # Added /audio static mount
├── routes/v1/__init__.py           # Registered narration router
requirements.txt                    # Added google-cloud-texttospeech
```

## How It Works

### Step 1: Client Calls Endpoint
```bash
POST /api/v1/stories/{story_id}/generate-narration?overwrite=false
Authorization: Bearer <jwt_token>
```

### Step 2: Service Fetches Story
- Uses `StoryRepository.get_for_user()` to fetch story from DB
- Validates story ownership and story_json content

### Step 3: Process Each Page
For each page in `story.story_json["pages"]`:
1. Extract text and pace from page
2. Call Google TTS API → get MP3 bytes + duration
3. Generate word timestamps using proportional algorithm
4. Save MP3 file to `audio/{story_id}/page_{page_number}.mp3`
5. Update page JSON with:
   - `audio_url`: `/audio/{story_id}/page_{page_number}.mp3`
   - `duration`: float (seconds)
   - `word_timestamps`: list of {word, start, end}

### Step 4: Save Updated Story
- Call `StoryRepository.update()` with enriched story JSON
- Commit to database

### Step 5: Return Response
- Return `ApiResponse[StoryResponse]` with updated story

## Architecture

```
FastAPI Endpoint
    ↓
StoryNarrationService
    ├─ StoryRepository (fetch/save)
    ├─ GoogleTTSProvider (audio synthesis)
    └─ word_timestamps (timing generation)
        ↓
    Filesystem: audio/{story_id}/page_N.mp3
    Database: story_json updated with audio metadata
```

## Usage Example

### Using Curl
```bash
curl -X POST \
  "http://localhost:8000/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/generate-narration" \
  -H "Authorization: Bearer your_jwt_token" \
  -H "Content-Type: application/json"
```

### Using Python
```python
import requests
from uuid import UUID

story_id = UUID("550e8400-e29b-41d4-a716-446655440000")
headers = {"Authorization": "Bearer your_jwt_token"}

response = requests.post(
    f"http://localhost:8000/api/v1/stories/{story_id}/generate-narration",
    headers=headers,
    params={"overwrite": False}
)

if response.status_code == 200:
    story = response.json()["data"]
    
    for page in story["pages"]:
        print(f"Page {page['page_number']}: {page['audio_url']} ({page['duration']}s)")
        for word_ts in page["word_timestamps"]:
            print(f"  {word_ts['word']}: {word_ts['start']:.2f}-{word_ts['end']:.2f}s")
```

## Testing Locally

### 1. Create a Test Story
```bash
# Use existing POST /api/v1/stories/generate endpoint
# Ensure story has story_json with pages array
```

### 2. Call Generate-Narration
```bash
curl -X POST \
  "http://localhost:8000/api/v1/stories/{story_id}/generate-narration" \
  -H "Authorization: Bearer your_token"
```

### 3. Verify Audio Files
```bash
ls -la audio/{story_id}/
# Should see: page_1.mp3, page_2.mp3, etc.
```

### 4. Download and Play
```bash
curl -o page_1.mp3 http://localhost:8000/audio/{story_id}/page_1.mp3
# Play with any media player
```

## Configuration

### Google API Key
Set in `.env`:
```
GOOGLE_API_KEY=your_google_api_key_here
```

### Voice Selection
In `google_tts_utils.py`, modify `_synthesize_speech()`:
```python
voice = texttospeech.VoiceSelectionParams(
    language_code="en-IN",      # Change language
    name="en-IN-Neural2-C",     # Change voice (Female/Male)
    ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
)
```

### Speaking Rate
Pace mapping in `GoogleTTSProvider.PACE_RATE_MAP`:
```python
PACE_RATE_MAP = {
    "slow": 0.85,           # 15% slower
    "medium-slow": 0.95,    # 5% slower
    "medium": 1.0,          # Normal speed
    "fast": 1.1,            # 10% faster
}
```

## Logging

All operations logged using structlog at:
- **INFO**: Successful generation, page completion
- **ERROR**: API failures, file I/O errors, validation failures

Example log output:
```json
{
  "event": "Starting narration generation",
  "story_id": "550e8400-e29b-41d4-a716-446655440000",
  "page_count": 5,
  "timestamp": "2026-05-26T14:30:00Z"
}
```

## Timestamp Algorithm

### How It Works
1. Split text into words
2. Calculate base duration: `audio_duration / word_count`
3. Adjust each word by length factor:
   - `length_factor = word_length / avg_word_length`
   - Capped between 0.5x and 1.5x to avoid extremes
4. Distribute timestamps: `start = cumulative_time`, `end = start + adjusted_duration`

### Example
Text: "Every night a strange whisper"  
Duration: 5.0s  
Word count: 5  
Base duration: 1.0s per word

```
Length factors (vs avg 5.8 chars):
  "Every" (5 chars)    → 0.86x → 0.86s
  "night" (5 chars)    → 0.86x → 0.86s
  "a" (1 char)         → 0.17x → 0.5x (capped) → 0.5s
  "strange" (7 chars)  → 1.2x → 1.2s
  "whisper" (7 chars)  → 1.2x → 1.2s

Timeline:
  Every:    0.00s - 0.86s
  night:    0.86s - 1.72s
  a:        1.72s - 2.22s
  strange:  2.22s - 3.42s
  whisper:  3.42s - 4.62s
```

## Error Handling

### Story Not Found (404)
```json
{
  "success": false,
  "message": "Story {id} not found",
  "data": null
}
```

### Invalid Story JSON (400)
```json
{
  "success": false,
  "message": "Story does not have story_json content",
  "data": null
}
```

### API Error (500)
```json
{
  "success": false,
  "message": "Failed to generate narration: {error_detail}",
  "data": null
}
```

## Performance Notes

### Timing
- TTS API call: ~2-5 seconds per page
- File write: ~0.1 seconds per page
- Timestamp generation: ~10ms per page
- Database update: ~0.5 seconds

**Total for 5-page story: ~15-30 seconds**

### Optimization Ideas
- Parallel TTS calls with `asyncio.gather()` (if quota allows)
- Cache generated audio for identical text
- Use background tasks for very large stories

## Troubleshooting

### Audio Files Not Created
**Problem**: Story updated but no MP3 files
- Check `/audio` directory permissions
- Verify Google API key is valid
- Check logs for TTS API errors

### Timestamps Look Wrong
**Problem**: Word timing seems off
- This is normal - timestamps are approximate
- Longer words get more time due to length adjustment
- Suitable for read-along highlighting but not perfect sync

### Out of Quota
**Problem**: Google TTS returns quota exceeded error
- Check Google Cloud console for API quota
- Implement rate limiting if many concurrent requests
- Consider batching with delays

## Testing Checklist

- [ ] Story exists and user owns it
- [ ] Story has valid story_json with pages
- [ ] Google API key is valid
- [ ] Audio directory is writable
- [ ] MP3 files created in correct location
- [ ] story_json updated with audio_url, duration, word_timestamps
- [ ] Timestamps ordered and non-overlapping
- [ ] Audio files accessible via HTTP
- [ ] Response has correct structure
- [ ] Error cases handled properly

## Next Steps

1. Deploy google-cloud-texttospeech package
2. Configure Google API key in .env
3. Test with sample story
4. Monitor TTS API usage and costs
5. Consider moving audio storage to cloud storage for production
