import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.entity.user import User
from app.model.request.story import BatchWebPConversionRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.story import (
    BatchWebPConversionResponse,
    StoryBatchJobCancelResponse,
    StoryBatchJobReconcileResponse,
    StoryResponse,
    StoryStatusResponse,
    StoryStepResponse,
)
from app.service.story_service import StoryGenerationFlags

logger = logging.getLogger(__name__)


async def execute_story_workflow_background(
    story_id: UUID,
    user_id: UUID,
    resume: bool = False,
    flags: StoryGenerationFlags | None = None,
) -> None:
    """Background task that executes story generation workflow."""
    logger.info(f"[BACKGROUND] Starting workflow for story {story_id}")
    async with AsyncSessionLocal() as session:
        try:
            logger.info("[BACKGROUND] Created session, initializing service")
            service = app_container.request(session).story
            logger.info("[BACKGROUND] Service initialized, executing workflow")
            await service.execute_workflow(story_id, flags=flags or StoryGenerationFlags(), resume=resume)
            logger.info(f"[BACKGROUND] Workflow completed successfully for story {story_id}")
        except Exception as e:
            logger.error(f"[BACKGROUND] Workflow failed for story {story_id}")
            logger.exception(f"[BACKGROUND] Exception: {str(e)}")


async def execute_story_batch_workflow_background(
    story_id: UUID,
    user_id: UUID,
    resume: bool = False,
    flags: StoryGenerationFlags | None = None,
) -> None:
    """Background task that executes delayed Google Batch story generation."""
    logger.info("[BATCH_BACKGROUND] Starting delayed workflow for story %s", story_id)
    async with AsyncSessionLocal() as session:
        try:
            await app_container.request(session).story_batch.execute_workflow(
                story_id,
                flags=flags or StoryGenerationFlags(),
                resume=resume,
            )
            logger.info("[BATCH_BACKGROUND] Delayed workflow completed successfully for story %s", story_id)
        except Exception as e:
            logger.error("[BATCH_BACKGROUND] Delayed workflow failed for story %s", story_id)
            logger.exception("[BATCH_BACKGROUND] Exception: %s", str(e))


class StoriesRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "/batch-jobs/reconcile",
            self.reconcile_story_batch_jobs,
            methods=["POST"],
            response_model=ApiResponse[StoryBatchJobReconcileResponse],
        )
        self.router.add_api_route(
            "/{story_id}/batch-jobs/{batch_job_id}/cancel",
            self.cancel_story_batch_job,
            methods=["POST"],
            response_model=ApiResponse[StoryBatchJobCancelResponse],
        )
        self.router.add_api_route(
            "/batch/convert-to-webp",
            self.batch_convert_stories_to_webp,
            methods=["POST"],
            response_model=ApiResponse[BatchWebPConversionResponse],
        )
        self.router.add_api_route(
            "/{story_id}/status",
            self.get_story_status,
            methods=["GET"],
            response_model=ApiResponse[StoryStatusResponse],
        )
        self.router.add_api_route(
            "/{story_id}",
            self.get_story,
            methods=["GET"],
            response_model=ApiResponse[StoryResponse],
        )
        self.router.add_api_route(
            "/{story_id}/steps",
            self.get_story_steps,
            methods=["GET"],
            response_model=ApiResponse[list[StoryStepResponse]],
        )
        self.router.add_api_route(
            "",
            self.list_stories,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[StoryResponse]],
        )

    async def reconcile_story_batch_jobs(
        self,
        limit: int = Query(50, ge=1, le=200),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryBatchJobReconcileResponse]:
        """Manually reconcile submitted/running Google Batch jobs."""
        _ = current_user
        story_data = await container.story_batch.reconcile_batch_jobs(limit=limit)
        return success_response(
            StoryBatchJobReconcileResponse(**story_data),
            "Story batch jobs reconciled successfully",
        )

    async def cancel_story_batch_job(
        self,
        story_id: UUID,
        batch_job_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryBatchJobCancelResponse]:
        """Cancel a submitted Google Batch job for a delayed story workflow."""
        data = await container.story_batch.cancel_batch_job(
            user_id=current_user.id,
            story_id=story_id,
            batch_job_id=batch_job_id,
        )
        return success_response(StoryBatchJobCancelResponse(**data), data["message"])

    async def batch_convert_stories_to_webp(
        self,
        request: BatchWebPConversionRequest,
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[BatchWebPConversionResponse]:
        """Batch convert PNG images to WebP and upload to Cloudflare R2."""
        data = await container.image_webp_batch.convert_stories_to_webp_batch(
            user_id=None,
            story_ids=request.story_ids,
            quality=request.quality,
        )
        return success_response(
            BatchWebPConversionResponse(**data),
            f"Converted {data['successful']}/{data['total_stories']} stories to WebP",
        )

    async def get_story_status(
        self,
        story_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryStatusResponse]:
        """Retrieve only the current generation status for a story."""
        data = await container.story.get_story_status(current_user.id, story_id)
        return success_response(data, "Story status retrieved successfully")

    async def get_story(
        self,
        story_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryResponse]:
        """Retrieve a story with full content and current status."""
        data = await container.story.get_story(current_user.id, story_id)
        return success_response(data, "Story retrieved successfully")

    async def get_story_steps(
        self,
        story_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[StoryStepResponse]]:
        """Retrieve audit trail for story generation workflow."""
        data = await container.story.get_story_steps(current_user.id, story_id)
        return success_response(data, "Story steps retrieved successfully")

    async def list_stories(
        self,
        child_id: UUID | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[StoryResponse]]:
        """List stories for current user, optionally filtered by child."""
        data = await container.story.list_stories(
            current_user.id,
            child_id,
            page=page,
            page_size=page_size,
        )
        return success_response(data, "Stories retrieved successfully")


router = StoriesRouter(app_container).router
