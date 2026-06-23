"""REST endpoints for story narration generation."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.dependencies import get_current_user
from app.core.exceptions import NotFoundException
from app.entity.user import User
from app.model.response.common import ApiResponse, success_response

logger = logging.getLogger(__name__)


class StoryNarrationRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "/{story_id}/generate-narration",
            self.generate_story_narration,
            methods=["POST"],
            response_model=ApiResponse[dict[str, Any]],
            status_code=status.HTTP_200_OK,
        )

    async def generate_story_narration(
        self,
        story_id: UUID,
        overwrite: bool = False,
        generic_story: bool = Query(
            True,
            description="If true, narrate generic_story_contents.story_json. If false, narrate story_contents.story_json.",
        ),
        language: str = Query("en", min_length=2, max_length=16),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[dict[str, Any]]:
        try:
            logger.info(
                "Generating narration: user_id=%s story_id=%s language=%s overwrite=%s generic_story=%s",
                current_user.id,
                story_id,
                language,
                overwrite,
                generic_story,
            )

            narration_language = language.strip().lower()

            if generic_story:
                story_json = await container.story_narration.generate_generic_story_narration(
                    story_id=story_id,
                    language=narration_language,
                    overwrite=overwrite,
                )
            else:
                story_json = await container.story_narration.generate_story_table_narration(
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

        except Exception:
            logger.exception(f"Unexpected error in narration generation: story_id={story_id}")
            raise


router = StoryNarrationRouter(app_container).router
