import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.entity.user import User
from app.model.request.story import StoryGenerationRequest
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.story import StoryResponse, StoryStatusResponse, StoryStepResponse
from app.service.story_service import StoryService, StoryGenerationFlags

logger = logging.getLogger(__name__)
router = APIRouter()


async def execute_story_workflow_background(story_id: UUID, user_id: UUID, resume: bool = False) -> None:
    """Background task that executes story generation workflow.

    Creates new database session since background tasks don't share request session.
    """
    logger.info(f"[BACKGROUND] Starting workflow for story {story_id}")
    async with AsyncSessionLocal() as session:
        try:
            logger.info(f"[BACKGROUND] Created session, initializing service")
            service = StoryService(session)
            logger.info(f"[BACKGROUND] Service initialized, executing workflow")
            await service.execute_workflow(story_id, flags=StoryGenerationFlags(), resume=resume)
            logger.info(f"[BACKGROUND] Workflow completed successfully for story {story_id}")
        except Exception as e:
            logger.error(f"[BACKGROUND] Workflow failed for story {story_id}")
            logger.exception(f"[BACKGROUND] Exception: {str(e)}")


@router.post("/generate", response_model=ApiResponse[StoryResponse], status_code=status.HTTP_202_ACCEPTED)
async def generate_story(
    payload: StoryGenerationRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryResponse]:
    """Generate a new story (asynchronous with polling).

    Returns immediately with story_id and status=PENDING.
    Client polls GET /stories/{story_id} to check generation progress.

    Workflow steps executed in background:
    1. Story Plan Generation
    2. Story Plan Validation (3 retries)
    3. Story Text Generation
    4. Image Plan Generation
    5. Image Plan Validation (optional)
    6. Image Generation
    7. Narration Generation
    """
    service = StoryService(session)

    # Create story record (request session handles this)
    story_response = await service.generate_story_async(
        user_id=current_user.id,
        child_id=payload.child_id,
        payload=payload,
        public_base_url="",  # Will be set during image generation
    )

    # Kick off background workflow task
    background_tasks.add_task(execute_story_workflow_background, story_response.id, current_user.id)

    logger.info(f"Story {story_response.id} generation started in background")
    return success_response(story_response, "Story generation started successfully")


@router.post("/{story_id}/retry", response_model=ApiResponse[StoryStatusResponse], status_code=status.HTTP_202_ACCEPTED)
async def retry_story_generation(
    story_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryStatusResponse]:
    """Retry a failed story generation workflow from the last saved checkpoint."""
    service = StoryService(session)
    data = await service.retry_story_async(current_user.id, story_id)
    background_tasks.add_task(execute_story_workflow_background, story_id, current_user.id, True)
    logger.info("Story %s retry accepted", story_id)
    return success_response(data, "Story generation retry accepted")


@router.post("/{story_id}/recover", response_model=ApiResponse[StoryStatusResponse])
async def recover_story_generation(
    story_id: UUID,
    stale_after_minutes: int = Query(15, ge=1, le=1440),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryStatusResponse]:
    """Recover a stale in-progress story so it can be retried from checkpoint."""
    service = StoryService(session)
    data = await service.recover_story_async(
        current_user.id,
        story_id,
        stale_after_minutes=stale_after_minutes,
    )
    return success_response(data, "Story recovery checked successfully")


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
