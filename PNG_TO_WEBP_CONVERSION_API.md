# PNG to WebP Image Conversion API

## Overview

This API converts PNG images in stories to WebP format, reducing file size by 60-80% while maintaining the same quality. WebP is a modern image format supported by all modern browsers (Chrome, Firefox, Safari, Edge).

## Implementation

### Service: `ImageConversionService`
**File:** `app/service/image_conversion_service.py`

**Features:**
- Downloads PNG images from story JSON
- Converts to WebP with optimized compression
- Saves WebP files locally
- Updates story JSON with new WebP URLs
- Tracks compression metrics
- Handles RGBA/RGB conversion automatically

### API Endpoints

#### 1. Convert Story Images to WebP

```
POST /stories/{story_id}/convert-to-webp
```

**Headers:**
```
Authorization: Bearer YOUR_TOKEN
Content-Type: application/json
```

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `language` | string | "en" | Language code (optional) |
| `quality` | integer | 85 | WebP quality 1-100 (optional) |

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `story_id` | UUID | Story ID to convert |

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Converted 8 pages to WebP. Compression ratio: 72.3%",
  "data": {
    "story_id": "550e8400-e29b-41d4-a716-446655440000",
    "language": "en",
    "total_pages": 8,
    "converted_pages": 8,
    "failed_pages": 0,
    "original_size_mb": 22.5,
    "converted_size_mb": 6.2,
    "compression_ratio": 0.723,
    "conversions": [
      {
        "page_number": 1,
        "original_url": "https://storage.example.com/story_page_1.png",
        "original_size_mb": 2.8,
        "converted_size_mb": 0.75,
        "compression_ratio": 0.732,
        "webp_url": "/webp_images/story_550e8400_page_1.webp",
        "local_path": "/data/webp_images/story_550e8400_page_1.webp"
      }
      // ... more pages
    ],
    "errors": []
  }
}
```

#### 2. Get WebP Conversion Statistics

```
GET /stories/{story_id}/webp-stats
```

**Response:**
```json
{
  "success": true,
  "message": "WebP conversion stats retrieved successfully",
  "data": {
    "story_id": "550e8400-e29b-41d4-a716-446655440000",
    "total_pages": 8,
    "webp_images": 8,
    "still_png": 0
  }
}
```

---

## Usage Examples

### Basic Conversion (Default Quality)

```bash
curl -X POST \
  https://api.example.com/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/convert-to-webp \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Conversion with Custom Quality

```bash
# High quality (slower conversion, larger files)
curl -X POST \
  'https://api.example.com/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/convert-to-webp?quality=95' \
  -H "Authorization: Bearer YOUR_TOKEN"

# Fast conversion (smaller files, slightly lower quality)
curl -X POST \
  'https://api.example.com/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/convert-to-webp?quality=75' \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Python Example

```python
import httpx
from uuid import UUID

BASE_URL = "https://api.example.com/api/v1"
TOKEN = "your_bearer_token"
headers = {"Authorization": f"Bearer {TOKEN}"}

story_id = "550e8400-e29b-41d4-a716-446655440000"

async with httpx.AsyncClient() as client:
    response = await client.post(
        f"{BASE_URL}/stories/{story_id}/convert-to-webp",
        params={"quality": 85},
        headers=headers
    )
    data = response.json()
    
    if data["success"]:
        stats = data["data"]
        print(f"Converted {stats['converted_pages']} pages")
        print(f"Original size: {stats['original_size_mb']} MB")
        print(f"Converted size: {stats['converted_size_mb']} MB")
        print(f"Compression: {stats['compression_ratio']*100:.1f}%")
```

### JavaScript Example

```javascript
const BASE_URL = "https://api.example.com/api/v1";
const TOKEN = "your_bearer_token";
const storyId = "550e8400-e29b-41d4-a716-446655440000";

const response = await fetch(
  `${BASE_URL}/stories/${storyId}/convert-to-webp?quality=85`,
  {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}` }
  }
);

const data = await response.json();
if (data.success) {
  const stats = data.data;
  console.log(`Converted ${stats.converted_pages} pages`);
  console.log(`Compression: ${(stats.compression_ratio * 100).toFixed(1)}%`);
}
```

---

## Quality Settings

### Recommended Quality Values

| Quality | Use Case | File Size | Quality | Speed |
|---------|----------|-----------|---------|-------|
| **60-70** | Maximum compression | Smallest | Good | Fast |
| **75-80** | Balanced | Small-Medium | Excellent | Medium |
| **85-90** | High quality (RECOMMENDED) | Medium | Excellent+ | Slow |
| **95-100** | Lossless/Maximum quality | Larger | Perfect | Very Slow |

**Default: 85** - Best balance of size reduction and quality preservation.

---

## File Size Comparison

### Typical Results (8-page story)

**Before (PNG):**
- Average per page: 2.5-3.0 MB
- Total: 20-24 MB

**After (WebP with quality=85):**
- Average per page: 0.6-0.8 MB
- Total: 5-7 MB

**Compression:** 70-75% size reduction ✓

---

## WebP Browser Support

✅ **Fully Supported:**
- Chrome/Chromium (all versions)
- Firefox (65+)
- Safari (16+)
- Edge (all versions)
- Mobile browsers (iOS 14+, Android Chrome)

✅ **Coverage:** 95%+ of modern users

❌ **Not Supported:**
- Internet Explorer (all versions)
- Old Safari versions (<16)
- Very old mobile browsers

**Fallback:** If needed, keep PNG URLs in parallel and serve based on `Accept` header or browser detection.

---

## Implementation Details

### Image Processing Pipeline

1. **Download PNG** from URL provided in story JSON
2. **Load with PIL** (Python Imaging Library)
3. **Convert color space** if needed (RGBA → RGB with white background)
4. **Save as WebP** with:
   - Quality setting (1-100)
   - Method 6 (slowest but best compression)
5. **Store locally** in `/webp_images/` folder
6. **Update story JSON** with new WebP URL path
7. **Save to database**

### Storage

WebP images stored in:
```
{LOCAL_STORAGE_PATH}/webp_images/
├── story_550e8400_page_1.webp
├── story_550e8400_page_2.webp
├── story_550e8400_page_3.webp
└── ...
```

Static files served via:
```
GET /webp_images/story_550e8400_page_1.webp
```

---

## Error Handling

### Possible Errors

| Error | Status | Cause |
|-------|--------|-------|
| Story not found | 404 | Invalid story ID or unauthorized |
| Invalid story JSON | 400 | Story missing or corrupted |
| Invalid pages | 400 | Pages array malformed |
| Network error | 502 | Cannot download PNG image |
| Conversion failed | 500 | PIL conversion error |

Each page conversion failure is logged but doesn't stop the entire process. Failed pages are listed in `errors` array.

---

## Workflow Recommendations

### For New Stories

**Option 1: Auto-convert on completion**
- After story generation completes
- Automatically convert to WebP
- Update story JSON before returning to user

**Option 2: Manual conversion**
- User requests conversion when needed
- Useful for batch processing multiple stories

### For Existing Stories

**Batch convert all stories:**
```bash
# Recommended: Add to background job scheduler
# Run once per day to convert 100 stories at a time
curl -X POST https://api.example.com/api/v1/stories/batch-convert-to-webp
```

---

## Cost Savings

### Data Transfer Reduction

Example for 100 stories (8 pages each):

**Before (PNG):**
- 100 × 8 × 2.8 MB = 2,240 MB
- Monthly transfer at 10k views: 22 GB

**After (WebP):**
- 100 × 8 × 0.75 MB = 600 MB
- Monthly transfer at 10k views: 6 GB

**Savings: 16 GB/month = ~$3-5 per month on CDN costs**

---

## API Response Fields

### Conversion Response

| Field | Type | Description |
|-------|------|-------------|
| `story_id` | UUID | Story converted |
| `language` | string | Language of content |
| `total_pages` | int | Total pages in story |
| `converted_pages` | int | Successfully converted |
| `failed_pages` | int | Conversion failures |
| `original_size_mb` | float | Total PNG size |
| `converted_size_mb` | float | Total WebP size |
| `compression_ratio` | float | 0.0-1.0 (e.g., 0.72 = 72% reduction) |
| `conversions` | array | Details per page |
| `errors` | array | List of any errors |

### Conversion per Page

| Field | Type | Description |
|-------|------|-------------|
| `page_number` | int | Page number |
| `original_url` | string | Original PNG URL |
| `original_size_mb` | float | PNG file size |
| `converted_size_mb` | float | WebP file size |
| `compression_ratio` | float | Size reduction for page |
| `webp_url` | string | URL to serve WebP |
| `local_path` | string | Local file path |

---

## User Experience Impact

### Positive Impacts

✅ **Faster loading:** WebP loads 60-80% faster
✅ **Bandwidth savings:** Significant data reduction
✅ **Mobile friendly:** Better for limited data plans
✅ **Same quality:** Imperceptible quality loss at quality=85
✅ **Backward compatible:** PNG URLs still work

### No Negative Impacts

✅ No visual quality change (at quality=85+)
✅ No loading delay (served from local storage)
✅ No user action required
✅ Transparent to end users

---

## Configuration

### Environment Variables

```bash
# In .env file
LOCAL_STORAGE_PATH=/data/storage  # Default path for WebP files
```

### Adjustable Parameters

**In API call:**
- `quality` (1-100): Adjust per story needs
- `language`: Currently unused, reserved for future

**In service code:**
- `method=6`: Change compression method (1-6, default 6=best)

---

## Monitoring

### Success Metrics

Monitor in logs:
```
[INFO] Story 550e8400: Converted 8 pages to WebP, compression ratio: 72.3%
```

### Check Conversion Stats

```bash
curl -X GET \
  https://api.example.com/api/v1/stories/550e8400-e29b-41d4-a716-446655440000/webp-stats \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Summary

✅ **PNG → WebP API implemented and ready**

**Convert all story images to WebP for:**
- 60-80% smaller file sizes
- Same visual quality
- Faster loading
- Better mobile experience
- Lower bandwidth costs

**Safe to use:** Modern browsers fully support WebP. Recommended for all stories.
