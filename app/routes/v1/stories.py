import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.entity.custom_story_workflow import CustomStoryWorkflowType
from app.entity.story_batch_job import StoryBatchJobStatus
from app.entity.user import User
from app.model.request.story import StoryGenerationRequest, BatchWebPConversionRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.custom_story_workflow import (
    CustomStoryWorkflowBatchJobResponse,
    CustomStoryWorkflowResponse,
    CustomStoryWorkflowStepResponse,
    CustomStoryWorkflowBatchJobCancelResponse,
    CustomStoryWorkflowEventResponse,
)
from app.model.response.story import (
    StoryBatchJobCancelResponse,
    StoryBatchJobReconcileResponse,
    StoryResponse,
    StoryStatusResponse,
    StoryStepResponse,
    BatchWebPConversionResponse,
)
from app.service.custom_story_workflow_service import CustomStoryWorkflowService
from app.service.image_webp_batch_service import ImageWebPBatchService
from app.service.story_service import StoryService, StoryGenerationFlags
from app.service.story_service_batch_service import StoryServiceBatchService

logger = logging.getLogger(__name__)
router = APIRouter()


async def execute_story_workflow_background(
    story_id: UUID,
    user_id: UUID,
    resume: bool = False,
    flags: StoryGenerationFlags | None = None,
) -> None:
    """Background task that executes story generation workflow.

    Creates new database session since background tasks don't share request session.
    """
    logger.info(f"[BACKGROUND] Starting workflow for story {story_id}")
    async with AsyncSessionLocal() as session:
        try:
            logger.info(f"[BACKGROUND] Created session, initializing service")
            service = StoryService(session)
            logger.info(f"[BACKGROUND] Service initialized, executing workflow")
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
            service = StoryServiceBatchService(session)
            await service.execute_workflow(story_id, flags=flags or StoryGenerationFlags(), resume=resume)
            logger.info("[BATCH_BACKGROUND] Delayed workflow completed successfully for story %s", story_id)
        except Exception as e:
            logger.error("[BATCH_BACKGROUND] Delayed workflow failed for story %s", story_id)
            logger.exception("[BATCH_BACKGROUND] Exception: %s", str(e))


@router.post("/workflows", response_model=ApiResponse[CustomStoryWorkflowResponse], status_code=status.HTTP_201_CREATED)
async def create_custom_story_workflow(
    payload: StoryGenerationRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CustomStoryWorkflowResponse]:
    """Create a custom story workflow and enqueue its first event."""
    data = await CustomStoryWorkflowService(session).create(current_user.id, payload)
    if data.execute_workflow:
        response.status_code = status.HTTP_202_ACCEPTED
        return success_response(data, "Custom story workflow queued successfully")
    response.status_code = status.HTTP_201_CREATED
    return success_response(data, "Custom story workflow saved successfully; execution skipped")


@router.get("/workflows", response_model=ApiResponse[PaginatedResponse[CustomStoryWorkflowResponse]])
async def list_custom_story_workflows(
    child_id: UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[CustomStoryWorkflowResponse]]:
    data = await CustomStoryWorkflowService(session).list(
        current_user.id,
        child_id=child_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    return success_response(data, "Custom story workflows retrieved successfully")


@router.get(
    "/workflows/batch-jobs",
    response_model=ApiResponse[PaginatedResponse[CustomStoryWorkflowBatchJobResponse]],
)
async def list_custom_workflow_batch_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workflow_id: UUID | None = Query(default=None),
    story_type: CustomStoryWorkflowType | None = Query(default=None),
    status_filter: StoryBatchJobStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[CustomStoryWorkflowBatchJobResponse]]:
    """List workflow batch jobs with optional filtering."""
    data = await CustomStoryWorkflowService(session).list_batch_jobs(
        current_user.id,
        page=page,
        page_size=page_size,
        workflow_id=workflow_id,
        story_type=story_type,
        status_filter=status_filter,
    )
    return success_response(data, "Batch jobs retrieved successfully")


@router.post("/workflows/events/process", response_model=ApiResponse[dict[str, Any]])
async def process_custom_story_workflow_events(
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[dict[str, Any]]:
    """Process pending custom story workflow events."""
    _ = current_user
    data = await CustomStoryWorkflowService(session).process_events(limit=limit)
    return success_response(data, "Custom story workflow events processed successfully")


@router.get("/workflows/{workflow_id}", response_model=ApiResponse[CustomStoryWorkflowResponse])
async def get_custom_story_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CustomStoryWorkflowResponse]:
    data = await CustomStoryWorkflowService(session).get(current_user.id, workflow_id)
    return success_response(data, "Custom story workflow retrieved successfully")


@router.delete("/workflows/{workflow_id}", response_model=ApiResponse[None])
async def delete_custom_story_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await CustomStoryWorkflowService(session).delete(current_user.id, workflow_id)
    return success_response(None, "Custom story workflow deleted successfully")


@router.get("/workflows/{workflow_id}/steps", response_model=ApiResponse[list[CustomStoryWorkflowStepResponse]])
async def get_custom_story_workflow_steps(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[CustomStoryWorkflowStepResponse]]:
    data = await CustomStoryWorkflowService(session).get_steps(current_user.id, workflow_id)
    return success_response(data, "Custom story workflow steps retrieved successfully")


@router.get("/workflows/{workflow_id}/events", response_model=ApiResponse[list[CustomStoryWorkflowEventResponse]])
async def get_story_workflow_events(
    workflow_id: UUID,
    story_type: str | None = Query(default=None, pattern="^(CUSTOM|GENERIC)$"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[CustomStoryWorkflowEventResponse]]:
    data = await CustomStoryWorkflowService(session).get_events(current_user.id, workflow_id, story_type=story_type)
    return success_response(data, "Story workflow events retrieved successfully")


@router.post(
    "/workflows/{workflow_id}/retry",
    response_model=ApiResponse[CustomStoryWorkflowResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_custom_story_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CustomStoryWorkflowResponse]:
    data = await CustomStoryWorkflowService(session).retry(current_user.id, workflow_id)
    return success_response(data, "Custom story workflow retry queued successfully")


@router.post(
    "/workflows/{workflow_id}/batch-jobs/{batch_job_id}/cancel",
    response_model=ApiResponse[CustomStoryWorkflowBatchJobCancelResponse],
)
async def cancel_custom_workflow_batch_job(
    workflow_id: UUID,
    batch_job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CustomStoryWorkflowBatchJobCancelResponse]:
    """Cancel a submitted Google Batch job for a custom story workflow."""
    data = await CustomStoryWorkflowService(session).cancel_batch_job(
        user_id=current_user.id,
        workflow_id=workflow_id,
        batch_job_id=batch_job_id,
    )
    return success_response(CustomStoryWorkflowBatchJobCancelResponse(**data), "Batch job cancelled successfully")


@router.post("/batch-jobs/reconcile", response_model=ApiResponse[StoryBatchJobReconcileResponse])
async def reconcile_story_batch_jobs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryBatchJobReconcileResponse]:
    """Manually reconcile submitted/running Google Batch jobs."""
    _ = current_user
    story_data = await StoryServiceBatchService(session).reconcile_batch_jobs(limit=limit)
    workflow_data = await CustomStoryWorkflowService(session).reconcile_batch_jobs(limit=limit)
    data = {
        "checked_count": story_data.get("checked_count", 0) + workflow_data.get("checked_count", 0),
        "processed_count": story_data.get("processed_count", 0) + workflow_data.get("processed_count", 0),
        "results": [*story_data.get("results", []), *workflow_data.get("results", [])],
    }
    return success_response(
        StoryBatchJobReconcileResponse(**data),
        "Story batch jobs reconciled successfully",
    )


@router.post(
    "/{story_id}/batch-jobs/{batch_job_id}/cancel",
    response_model=ApiResponse[StoryBatchJobCancelResponse],
)
async def cancel_story_batch_job(
    story_id: UUID,
    batch_job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryBatchJobCancelResponse]:
    """Cancel a submitted Google Batch job for a delayed story workflow."""
    data = await StoryServiceBatchService(session).cancel_batch_job(
        user_id=current_user.id,
        story_id=story_id,
        batch_job_id=batch_job_id,
    )
    return success_response(StoryBatchJobCancelResponse(**data), data["message"])


@router.post(
    "/batch/convert-to-webp",
    response_model=ApiResponse[BatchWebPConversionResponse],
)
async def batch_convert_stories_to_webp(
    request: BatchWebPConversionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[BatchWebPConversionResponse]:
    """Batch convert PNG images to WebP and upload to Cloudflare R2.

    Converts all PNG images (cover, pages, back cover) to WebP format for each story
    (works for both custom and generic stories), uploads to Cloudflare R2, deletes
    original PNGs, and updates story JSON for all language versions (en, hi, mr) with
    new WebP URLs.

    Input:
    - story_ids: List of story IDs to convert (max 100) - custom or generic
    - quality: WebP quality 1-100 (default: 85)

    Returns:
    - Per-story conversion results with compression metrics and image counts
    """
    data = await ImageWebPBatchService(session).convert_stories_to_webp_batch(
        user_id=None,
        story_ids=request.story_ids,
        quality=request.quality,
    )
    return success_response(
        BatchWebPConversionResponse(**data),
        f"Converted {data['successful']}/{data['total_stories']} stories to WebP",
    )


@router.get("/{story_id}/status", response_model=ApiResponse[StoryStatusResponse])
async def get_story_status(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryStatusResponse]:
    """Retrieve only the current generation status for a story."""
    service = StoryService(session)
    data = await service.get_story_status(current_user.id, story_id)
    return success_response(data, "Story status retrieved successfully")


@router.get("/{story_id}", response_model=ApiResponse[StoryResponse])
async def get_story(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryResponse]:
    """Retrieve a story with full content and current status.

    Use for polling during generation:
    - status=PENDING or IN_PROGRESS: Generation still running
    - status=COMPLETED: Story ready with pages and images
    - status=FAILED: Generation failed, see error_message
    """
    service = StoryService(session)
    data = await service.get_story(current_user.id, story_id)
    return success_response(data, "Story retrieved successfully")


@router.get("/{story_id}/steps", response_model=ApiResponse[list[StoryStepResponse]])
async def get_story_steps(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[StoryStepResponse]]:
    """Retrieve audit trail for story generation workflow.

    Shows each step (plan generation, validation, story generation, etc.)
    with status, timestamps, and error messages for debugging.
    """
    service = StoryService(session)
    data = await service.get_story_steps(current_user.id, story_id)
    return success_response(data, "Story steps retrieved successfully")


@router.get("", response_model=ApiResponse[PaginatedResponse[StoryResponse]])
async def list_stories(
    child_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[StoryResponse]]:
    """List stories for current user, optionally filtered by child.

    Returns stories in reverse chronological order (newest first).
    """
    service = StoryService(session)
    data = await service.list_stories(
        current_user.id,
        child_id,
        page=page,
        page_size=page_size,
    )
    return success_response(data, "Stories retrieved successfully")
