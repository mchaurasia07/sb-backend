import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.entity.user import User
from app.model.request.story import StoryGenerationRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.custom_story_workflow import (
    CustomStoryWorkflowResponse,
    CustomStoryWorkflowStepResponse,
)
from app.model.response.story import (
    StoryBatchJobCancelResponse,
    StoryBatchJobReconcileResponse,
    StoryResponse,
    StoryStatusResponse,
    StoryStepResponse,
)
from app.service.custom_story_workflow_service import CustomStoryWorkflowService
from app.service.story_service import StoryService, StoryGenerationFlags
from app.service.story_service_batch_service import StoryServiceBatchService

logger = logging.getLogger(__name__)
router = APIRouter()


async def execute_custom_story_workflow_background(workflow_id: UUID) -> None:
    """Execute a custom story workflow with a fresh background session."""
    logger.info("[CUSTOM_WORKFLOW_BACKGROUND] Starting workflow %s", workflow_id)
    async with AsyncSessionLocal() as session:
        try:
            await CustomStoryWorkflowService(session).run(workflow_id)
            logger.info("[CUSTOM_WORKFLOW_BACKGROUND] Workflow completed or deferred: %s", workflow_id)
        except Exception as exc:
            logger.error("[CUSTOM_WORKFLOW_BACKGROUND] Workflow failed: %s", workflow_id)
            logger.exception("[CUSTOM_WORKFLOW_BACKGROUND] Exception: %s", str(exc))


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
    background_tasks: BackgroundTasks,
    response: Response,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CustomStoryWorkflowResponse]:
    """Create a custom story workflow and start it in the background."""
    data = await CustomStoryWorkflowService(session).create(current_user.id, payload)
    if data.execute_workflow:
        response.status_code = status.HTTP_202_ACCEPTED
        background_tasks.add_task(execute_custom_story_workflow_background, data.workflow_id)
        return success_response(data, "Custom story workflow started successfully")
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


@router.post(
    "/workflows/{workflow_id}/retry",
    response_model=ApiResponse[CustomStoryWorkflowResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_custom_story_workflow(
    workflow_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[CustomStoryWorkflowResponse]:
    data = await CustomStoryWorkflowService(session).retry(current_user.id, workflow_id)
    background_tasks.add_task(execute_custom_story_workflow_background, workflow_id)
    return success_response(data, "Custom story workflow retry started successfully")


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
