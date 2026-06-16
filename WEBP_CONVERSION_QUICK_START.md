# WebP Conversion - Quick Start Guide

## What It Does

Converts PNG images (2-3 MB each) to WebP format (0.6-0.8 MB each) - **70% smaller** with the same quality!

## APIs

### 1. Convert Story Images

```
POST /stories/{story_id}/convert-to-webp?quality=85
```

**Example:**
```bash
curl -X POST \
  "http://localhost:8000/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/convert-to-webp" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response:**
```json
{
  "success": true,
  "message": "Converted 8 pages to WebP. Compression ratio: 72.3%",
  "data": {
    "story_id": "550e8400-e29b-41d4-a716-446655440000",
    "total_pages": 8,
    "converted_pages": 8,
    "failed_pages": 0,
    "original_size_mb": 22.5,
    "converted_size_mb": 6.2,
    "compression_ratio": 0.723
  }
}
```

### 2. Check Conversion Status

```
GET /stories/{story_id}/webp-stats
```

**Example:**
```bash
curl -X GET \
  "http://localhost:8000/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/webp-stats" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response:**
```json
{
  "success": true,
  "data": {
    "story_id": "550e8400-e29b-41d4-a716-446655440000",
    "total_pages": 8,
    "webp_images": 8,
    "still_png": 0
  }
}
```

---

## Quality Settings

| Quality | Result | Speed |
|---------|--------|-------|
| **75** | Good, fast | ⚡ |
| **85** | Excellent (RECOMMENDED) | ⚡⚡ |
| **95** | Perfect, slow | ⚡⚡⚡ |

Use `?quality=85` (default) for best balance.

---

## What Happens

1. ✅ Downloads all PNG images from story JSON
2. ✅ Converts each to WebP (60-80% smaller)
3. ✅ Saves locally to `/webp_images/`
4. ✅ Updates story JSON with new URLs
5. ✅ Returns compression stats

---

## Image URLs Change

**Before:**
```
/stories/page_1.png  (2.8 MB)
```

**After:**
```
/webp_images/story_550e8400_page_1.webp  (0.75 MB)
```

Story JSON automatically updated - no user action needed!

---

## Browser Support

✅ Chrome, Firefox, Safari 16+, Edge, Android
✅ 95%+ of users supported
✅ Imperceptible quality difference

---

## File Size Savings

| Pages | Original | Converted | Saved |
|-------|----------|-----------|-------|
| 8 | 22 MB | 6 MB | 16 MB (73%) |
| 16 | 44 MB | 12 MB | 32 MB (73%) |
| 100+ | 220+ MB | 60 MB | 160 MB (73%) |

---

## Python Example

```python
import httpx

async def convert_story_to_webp(story_id: str, token: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://localhost:8000/api/v1/stories/{story_id}/convert-to-webp",
            params={"quality": 85},
            headers={"Authorization": f"Bearer {token}"}
        )
    
    data = response.json()
    if data["success"]:
        print(f"✓ Converted {data['data']['converted_pages']} pages")
        print(f"✓ Saved {data['data']['compression_ratio']*100:.0f}% storage")
    return data
```

---

## Implementation Options

### Option A: Auto-convert on story completion
Add to story generation service:
```python
# After story generation completes
await ImageConversionService(session).convert_story_images_to_webp(user_id, story_id)
```

### Option B: Manual conversion via API
User calls endpoint when needed:
```bash
POST /stories/{story_id}/convert-to-webp
```

### Option C: Batch process existing stories
```bash
# Convert 100 oldest PNG stories (run nightly)
for story in get_stories_with_png():
    await convert_to_webp(story.id)
```

---

## No Issues Expected

✅ **User experience:** Better (faster loading)
✅ **Quality:** Same or imperceptibly different
✅ **Compatibility:** 95%+ browsers support WebP
✅ **Storage:** Significantly reduced
✅ **Bandwidth:** Significantly reduced

---

## Next Steps

1. Test with a story:
   ```bash
   POST /stories/{test_story_id}/convert-to-webp
   ```

2. Check result:
   ```bash
   GET /stories/{test_story_id}/webp-stats
   ```

3. Enable for all new stories (add to generation workflow)

4. Batch convert existing stories (run nightly job)

---

## Questions?

See full documentation: `PNG_TO_WEBP_CONVERSION_API.md`
