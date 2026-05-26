"""REST endpoints for story narration generation."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.database import get_db_session
from app.core.exceptions import NotFoundException
from app.entity.user import User
from app.model.response.common import ApiResponse, success_response
from app.service.story_narration_service import StoryNarrationService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/{story_id}/generate-narration",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
async def generate_story_narration(
    story_id: UUID,
    overwrite: bool = False,
    language: str = Query("en", min_length=2, max_length=16),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[dict[str, Any]]:
    """
    Generate narration audio for all pages of a generic story content row.

    Reads generic_story_contents.story_json for the requested story_id and
    language, generates WAV audio using Gemini TTS, creates
    word-level timestamps for read-along functionality, saves audio files to
    local storage, and writes narration timing back to that language-specific JSON.

    Args:
        story_id: UUID of story to generate narration for
        overwrite: If true, regenerate audio even if it already exists
        language: Language code of generic_story_contents row to narrate
        current_user: Authenticated user (dependency)
        session: Database session (dependency)

    Returns:
        Updated story with duration and word_timestamps in each page

    Raises:
        404: Story not found or user doesn't own it
        400: Story missing story_json or invalid content
        500: TTS API error or file I/O error

    Status Codes:
        200: Narration generated successfully
        404: Story not found
        403: Unauthorized (user doesn't own story)
        500: Server error (TTS API failure, file write error, etc.)

    Example Response:
        {
            "success": true,
            "message": "Narration generated successfully",
            "data": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "title": "The Enchanted Forest",
                "pages": [
                    {
                        "page_number": 1,
                        "text": "Every night, a strange whisper echoed...",
                        "duration": 18.2,
                        "word_timestamps": [
                            {"word": "Every", "start": 0.12, "end": 0.48},
                            {"word": "night", "start": 0.48, "end": 0.95},
                            ...
                        ]
                    },
                    ...
                ]
            }
        }
    """
    try:
        logger.info(
            "Generating narration: user_id=%s story_id=%s language=%s overwrite=%s",
            current_user.id,
            story_id,
            language,
            overwrite,
        )

        service = StoryNarrationService(session)
        story_json = await service.generate_narration(
            story_id=story_id,
            language=language,
            overwrite=overwrite,
        )

        logger.info("Narration generation successful: story_id=%s language=%s", story_id, language)
        return success_response(story_json, "Narration generated successfully")

    except NotFoundException as e:
        logger.error(f"Story not found: story_id={story_id}, error={str(e)}")
        raise e

    except Exception as e:
        logger.exception(f"Unexpected error in narration generation: story_id={story_id}")
        raise
