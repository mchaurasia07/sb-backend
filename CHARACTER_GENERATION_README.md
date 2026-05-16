# Character Generation API Implementation

## Overview

This document describes the production-ready character generation system that converts child profile photos into AI-generated storybook characters.

## Architecture

### Service Layer Design

```
API Route → CharacterService → AIProvider (Interface)
                ↓
         ChildRepository
         ImageStorageService
         PromptLoader
                ↓
         OpenAIProvider (Implementation)
```

### Key Components

1. **AI Service Interface** (`app/service/ai/base.py`)
   - Abstract base class for provider implementations
   - Generic `ImageGenerationResult` and `TextGenerationResult` types
   - Methods: `generate_image_from_reference()`, `generate_text_from_image()`

2. **OpenAI Provider** (`app/service/ai/openai_provider.py`)
   - Implements AIProvider interface
   - Uses DALL-E 3 for image generation
   - Uses GPT-4V for character description generation
   - Handles API errors gracefully with user-friendly messages

3. **Character Service** (`app/service/character_service.py`)
   - Orchestrates character generation workflow
   - Validates child profile and ownership
   - Loads prompt templates and substitutes variables
   - Calls AI provider for image and description
   - Stores generated assets in file system
   - Updates database with character metadata

4. **Prompt Templates** (`app/prompts/`)
   - `character_generation.txt` - Image generation prompt
   - `character_description.txt` - Description generation prompt
   - Variables: `{additional_context}`, `{child_name}`, `{age}`

5. **Storage Layer** (`app/service/image_storage_service.py`)
   - Saves generated character images as `child_character.png`
   - Stores in `photo/{parent_id}/{child_id}/` directory
   - Returns public URLs for image access

## API Endpoint

### Generate Character

```
POST /api/v1/children/{child_id}/generate-character
Authorization: Bearer {access_token}

Request:
{
  "additional_context": "Optional context about hobbies, personality, etc."
}

Response (200):
{
  "success": true,
  "message": "Character generated successfully",
  "data": {
    "character_image_url": "http://localhost:8000/photo/{parent_id}/{child_id}/child_character.png",
    "character_description": "Detailed character description for visual consistency...",
    "metadata": {
      "description": "...",
      "style": "3D Pixar-style cartoon",
      "generation_model": "dall-e-3",
      "prompt_used": "...",
      "revised_prompt": "...",
      "generated_at": "2026-05-16T10:30:00+00:00",
      "generation_status": "completed"
    }
  }
}
```

## Database Schema

### child_profiles table additions

```sql
ALTER TABLE child_profiles ADD COLUMN character_image_url VARCHAR(1024) NULL;
ALTER TABLE child_profiles ADD COLUMN character_metadata JSON NULL;
```

The migration `20260516_0005_add_character_fields.py` handles this automatically.

### Character Metadata Structure

```json
{
  "description": "String describing the character visually",
  "style": "3D Pixar-style cartoon",
  "generation_model": "dall-e-3",
  "prompt_used": "The actual prompt sent to the model",
  "revised_prompt": "Model-revised version of prompt (if available)",
  "generated_at": "ISO 8601 timestamp",
  "generation_status": "completed|failed"
}
```

## Configuration

Add these to `.env`:

```env
# OpenAI Configuration
OPENAI_API_KEY=sk-...your-api-key...
OPENAI_IMAGE_MODEL=dall-e-3
CHARACTER_IMAGE_SIZE=1024x1024
CHARACTER_IMAGE_QUALITY=standard
CHARACTER_GENERATION_ENABLED=true
```

## File Structure

```
app/
├── core/
│   └── config.py                    # Added OpenAI settings
├── entity/
│   └── child_profile.py            # Added character_image_url, character_metadata
├── model/
│   ├── request/
│   │   └── character.py            # NEW: CharacterGenerationRequest
│   └── response/
│       ├── character.py            # NEW: CharacterGenerationResponse
│       └── child.py                # Updated: Added character fields
├── prompts/                        # NEW directory
│   ├── character_generation.txt   # NEW: Image generation prompt
│   └── character_description.txt  # NEW: Description generation prompt
├── repository/
│   └── child_repository.py        # Added update_character() method
├── routes/v1/
│   └── children.py                # Added generate-character endpoint
├── service/
│   ├── ai/                        # NEW directory
│   │   ├── __init__.py           # Exports
│   │   ├── base.py               # Abstract interfaces
│   │   ├── openai_provider.py   # OpenAI implementation
│   │   └── factory.py            # Provider factory
│   ├── character_service.py      # NEW: Main character generation logic
│   └── image_storage_service.py  # Added save_character_image() method
└── utils/
    └── prompt_loader.py           # NEW: Template loader with variable substitution

alembic/versions/
└── 20260516_0005_add_character_fields.py  # NEW: Database migration
```

## Usage Flow

### 1. Create Child Profile (existing endpoint)

```bash
POST /api/v1/children
- Upload child photo
- Response includes child_id
```

### 2. Generate Character (new endpoint)

```bash
POST /api/v1/children/{child_id}/generate-character
- Optional: include additional_context
- Generates and stores character image
- Generates and stores character description
```

### 3. Retrieve Profile with Character

```bash
GET /api/v1/children
- Returns list including character_image_url and character_metadata
- Can use character_image_url to display character
- Can use character_description for scene consistency
```

## Error Handling

The implementation includes comprehensive error handling:

- **400 Bad Request**: Child has no profile photo
- **404 Not Found**: Child profile not found or doesn't belong to user
- **500 Internal Server Error**: OpenAI API failures, storage errors
- **409 Conflict**: (Future) Character already exists and regeneration requested

Error responses follow the standard API format:

```json
{
  "success": false,
  "message": "Error description",
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "details": null
  }
}
```

## Security Considerations

1. **Ownership Validation**: Verifies child profile belongs to authenticated user
2. **File Path Validation**: Ensures generated files stay within media directory
3. **Image Format Validation**: Accepts only PNG, JPG, JPEG, WEBP for reference
4. **API Key Security**: OpenAI key loaded from environment, never exposed in logs
5. **Rate Limiting**: Can be applied via middleware (not yet configured)

## Performance Considerations

1. **Async Operations**: All I/O operations use async/await
2. **File Storage**: Character images stored locally with cloud-ready abstraction
3. **Caching**: Prompt templates cached with LRU cache (64 max)
4. **Batch Limits**: Single image generation per request (no batch processing)

## Future Enhancements

1. **Multiple Providers**: Abstract interface supports Replicate, Midjourney
2. **Background Processing**: Integrate Celery for async job queue
3. **Regeneration**: Allow regenerating character with different styles
4. **Version History**: Store multiple character versions
5. **Style Options**: User-selectable art styles in request
6. **Progress Webhooks**: Notify frontend of generation progress
7. **Bulk Operations**: Generate multiple character variants

## Testing

### Test the endpoint manually:

```bash
# 1. Create a child profile first
curl -X POST http://localhost:8000/api/v1/children \
  -H "Authorization: Bearer {token}" \
  -F "first_name=John" \
  -F "last_name=Doe" \
  -F "age=8" \
  -F "dob=2018-05-16" \
  -F "photo=@child_photo.jpg"

# 2. Generate character
curl -X POST http://localhost:8000/api/v1/children/{child_id}/generate-character \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"additional_context": "loves space and robots"}'

# 3. Verify character in profile
curl -X GET http://localhost:8000/api/v1/children \
  -H "Authorization: Bearer {token}"
```

### Expected Results

- Character image accessible at returned `character_image_url`
- `character_metadata` contains complete generation details
- Character description suitable for scene consistency prompts

## Dependencies

New packages added to `requirements.txt`:

- `openai==1.57.0` - OpenAI API client

## Migration

Run database migration:

```bash
cd d:/storybook/workspace/sb-backend
python -m alembic upgrade head
```

This adds:
- `character_image_url` (VARCHAR(1024), nullable)
- `character_metadata` (JSON, nullable)

## Logging

All operations logged via structured logging:

```
generate_character_for_child_id=..., user_id=...
Calling_AI_provider_to_generate_character_image
Successfully_generated_character_image
Saving_character_image_to_storage
Generating_character_description
Successfully_generated_character_description
Updating_child_profile
Character_generation_completed
```

## Monitoring

Key metrics to monitor:

1. Character generation success rate
2. Average generation time (typically 30-60 seconds)
3. OpenAI API costs (check billing)
4. Storage usage (character images size)
5. Error rates by type (API failures, validation, storage)

## Troubleshooting

### "No AI provider configured"
- Ensure `OPENAI_API_KEY` is set in `.env`
- Verify `CHARACTER_GENERATION_ENABLED=true`

### "OpenAI rate limit exceeded"
- Wait before retrying (typically 1 minute)
- Check OpenAI account billing and usage limits

### "Child profile photo required"
- Ensure child profile was created with photo upload
- Verify `avatar_image_url` is populated in database

### Character image not accessible via URL
- Check file exists at `photo/{parent_id}/{child_id}/child_character.png`
- Verify static file serving configured in main.py

## Production Checklist

- [ ] OpenAI API key configured with sufficient billing
- [ ] Character generation disabled/enabled via config
- [ ] Database migration applied to production database
- [ ] Static file serving configured and tested
- [ ] Error handling and logging tested
- [ ] Rate limiting configured (optional)
- [ ] Monitoring set up for API performance
- [ ] Backup strategy for generated images
- [ ] Privacy policy updated for AI image generation
