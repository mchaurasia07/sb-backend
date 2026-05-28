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
    generic_story: bool = Query(
        True,
        description="If true, narrate generic_story_contents.story_json. If false, narrate story_contents.story_json.",
    ),
    language: str = Query("en", min_length=2, max_length=16),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[dict[str, Any]]:
    """
    Generate narration audio for all pages of a story JSON payload.

    With generic_story=true, reads generic_story_contents.story_json for the
    requested story_id and language. With generic_story=false, reads
    story_contents.story_json for the authenticated user's story row.

    Args:
        story_id: UUID of story to generate narration for
        overwrite: If true, regenerate audio even if it already exists
        generic_story: If true use generic_story_contents, otherwise use story_contents
        language: Language code of the content row to narrate
        current_user: Authenticated user (dependency)
        session: Database session (dependency)

    Returns:
        Updated story with duration and sentence-level word_timestamps in each page

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
                            {"word": "Every night, a strange whisper echoed.", "start": 0.12, "end": 3.8},
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
            "Generating narration: user_id=%s story_id=%s language=%s overwrite=%s generic_story=%s",
            current_user.id,
            story_id,
            language,
            overwrite,
            generic_story,
        )

        service = StoryNarrationService(session)
        narration_language = language.strip().lower()

        if generic_story:
            story_json = await service.generate_generic_story_narration(
                story_id=story_id,
                language=narration_language,
                overwrite=overwrite,
            )
        else:
            story_json = await service.generate_story_table_narration(
                story_id=story_id,
                user_id=current_user.id,
                language=narration_language,
                overwrite=overwrite,
            )

        logger.info(
            "Narration generation successful: story_id=%s language=%s generic_story=%s",
            story_id,
            narration_language,
            generic_story,
        )
        return success_response(story_json, "Narration generated successfully")

    except NotFoundException as e:
        logger.error(f"Story not found: story_id={story_id}, error={str(e)}")
        raise e

    except Exception as e:
        logger.exception(f"Unexpected error in narration generation: story_id={story_id}")
        raise
