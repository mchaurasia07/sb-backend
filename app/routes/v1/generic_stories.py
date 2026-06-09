import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.age_groups import validate_age_group
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db_session
from app.core.dependencies import get_current_user, get_auth_context, AuthContext
from app.core.exceptions import AppException
from app.entity.notification import NotificationAudience
from app.entity.user import User
from app.model.request.generic_story import (
    GenericStoryCreateRequest,
    GenericStoryPageTextUpdateRequest,
    GenericStoryStatusUpdateRequest,
    GenericStoryUpdateRequest,
)
from app.model.request.generic_story_workflow import (
    GenericStoryWorkflowCreateRequest,
    GenericStoryWorkflowExecuteRequest,
)
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.generic_story import (
    GenericStoryAudioUploadResponse,
    GenericStoryBatchJobCancelResponse,
    GenericStoryBatchImageSubmitResponse,
    GenericStoryBatchNarrationSubmitResponse,
    GenericStoryImageUploadResponse,
    GenericStoryNarrationPromptResponse,
    GenericStoryResponse,
)
from app.model.response.generic_story_workflow import (
    GenericStoryWorkflowListResponse,
    GenericStoryWorkflowResponse,
    GenericStoryWorkflowStepDetailResponse,
)
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.generic_story_batch_service import GenericStoryBatchService
from app.service.generic_story_multi_image_service import GenericStoryMultiImageTestService
from app.service.generic_story_service import GenericStoryService
from app.service.generic_story_workflow_service import GenericStoryWorkflowService
from app.service.image_optimizer import optimize_display_image
from app.service.notification_service import NotificationService
from app.service.story_catalog_service import StoryCatalogService

router = APIRouter()
logger = logging.getLogger(__name__)

IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _upload_image_extension(upload: UploadFile) -> str:
    if upload.content_type in IMAGE_CONTENT_TYPES:
        return IMAGE_CONTENT_TYPES[upload.content_type]

    filename = upload.filename or ""
    for extension in {".jpg", ".jpeg", ".png", ".webp"}:
        if filename.lower().endswith(extension):
            return ".jpg" if extension == ".jpeg" else extension

    raise AppException("Image must be a JPEG, PNG, or WEBP image", status.HTTP_400_BAD_REQUEST, "UNSUPPORTED_IMAGE_TYPE")


def _content_type_for_extension(extension: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(extension.lower(), "application/octet-stream")


def _is_upload_value(value) -> bool:
    return hasattr(value, "read") and hasattr(value, "filename")


async def _read_uploads_from_form(request: Request, *, duplicate_code: str) -> dict[str, UploadFile]:
    form = await request.form()
    uploads: dict[str, UploadFile] = {}
    for field_name, value in form.multi_items():
        if not _is_upload_value(value):
            continue
        normalized_field_name = str(field_name).strip()
        if normalized_field_name in uploads:
            raise AppException(
                f"Duplicate upload field: {normalized_field_name}",
                code=duplicate_code,
            )
        uploads[normalized_field_name] = value
    return uploads


async def send_new_generic_story_notification_background(*, story_id: UUID, title: str) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await NotificationService(session).send_to_audience(
                audience=NotificationAudience.CHILDREN,
                event_type="new_generic_story_added",
                title="New story available",
                body=f"{title} is now available in the story library.",
                data={
                    "event_type": "new_generic_story_added",
                    "generic_story_id": str(story_id),
                    "screen": "generic_story_detail",
                },
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send new generic story notification: story_id=%s", story_id)


@router.post("", response_model=ApiResponse[GenericStoryResponse], status_code=status.HTTP_201_CREATED)
async def create_generic_story(
    payload: GenericStoryCreateRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).create(payload)
    if data.status == "active":
        background_tasks.add_task(
            send_new_generic_story_notification_background,
            story_id=data.id,
            title=data.title,
        )
    return success_response(data, "Generic story created successfully")


@router.post("/workflows", response_model=ApiResponse[GenericStoryWorkflowResponse], status_code=status.HTTP_201_CREATED)
async def create_generic_story_workflow(
    payload: GenericStoryWorkflowCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryWorkflowResponse]:
    data = await GenericStoryWorkflowService(session).create(current_user.id, payload)
    return success_response(data, "Generic story workflow created successfully")


@router.get("/workflows", response_model=ApiResponse[PaginatedResponse[GenericStoryWorkflowListResponse]])
async def list_generic_story_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: UUID | None = Query(None),
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[GenericStoryWorkflowListResponse]]:
    data = await GenericStoryWorkflowService(session).list(
        user_id=user_id,
        page=page,
        page_size=page_size,
    )
    return success_response(data, "Generic story workflows retrieved successfully")


@router.get("/workflows/{workflow_id}", response_model=ApiResponse[GenericStoryWorkflowResponse])
async def get_generic_story_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryWorkflowResponse]:
    data = await GenericStoryWorkflowService(session).get(current_user.id, workflow_id)
    return success_response(data, "Generic story workflow retrieved successfully")


@router.delete("/workflows/{workflow_id}", response_model=ApiResponse[None])
async def delete_generic_story_workflow(
    workflow_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await GenericStoryWorkflowService(session).delete(current_user.id, workflow_id)
    return success_response(None, "Generic story workflow deleted successfully")


@router.get("/workflows/{workflow_id}/steps", response_model=ApiResponse[list[GenericStoryWorkflowStepDetailResponse]])
async def get_generic_story_workflow_steps(
    workflow_id: UUID,
    step_name: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[list[GenericStoryWorkflowStepDetailResponse]]:
    data = await GenericStoryWorkflowService(session).get_steps(current_user.id, workflow_id, step_name=step_name)
    return success_response(data, "Generic story workflow steps retrieved successfully")


@router.post("/workflows/{workflow_id}/execute", response_model=ApiResponse[GenericStoryWorkflowResponse])
async def execute_generic_story_workflow(
    workflow_id: UUID,
    payload: GenericStoryWorkflowExecuteRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryWorkflowResponse]:
    data = await GenericStoryWorkflowService(session).execute(
        current_user.id,
        workflow_id,
        payload,
        public_base_url=str(request.base_url).rstrip("/"),
    )
    message = (
        "Generic story workflow completed successfully"
        if data.status == "COMPLETED"
        else "Generic story workflow step executed successfully"
    )
    return success_response(data, message)


@router.post("/workflows/{workflow_id}/retry", response_model=ApiResponse[GenericStoryWorkflowResponse])
async def retry_generic_story_workflow(
    workflow_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryWorkflowResponse]:
    data = await GenericStoryWorkflowService(session).retry(
        current_user.id,
        workflow_id,
        public_base_url=str(request.base_url).rstrip("/"),
    )
    message = (
        "Generic story workflow completed successfully"
        if data.status == "COMPLETED"
        else "Generic story workflow retry executed successfully"
    )
    return success_response(data, message)


@router.post(
    "/images/reduce",
    response_class=Response,
)
async def reduce_generic_story_image_for_preview(
    image: UploadFile = File(...),
    max_dimension: int = Query(1600, ge=320, le=4096),
    _: User = Depends(get_current_user),
) -> Response:
    extension = _upload_image_extension(image)
    content = await image.read()
    if not content:
        raise AppException("Image file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")
    if len(content) > settings.IMAGE_MAX_UPLOAD_BYTES:
        raise AppException(
            "Image must be 5 MB or smaller",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "IMAGE_TOO_LARGE",
        )

    filename = image.filename or f"image{extension}"
    if "." not in filename:
        filename = f"{filename}{extension}"
    reduced = optimize_display_image(content, filename, max_dimension=max_dimension)
    media_type = image.content_type if image.content_type in IMAGE_CONTENT_TYPES else _content_type_for_extension(extension)
    return Response(
        content=reduced,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="reduced_{filename}"'},
    )


@router.post(
    "/workflows/{workflow_id}/stories/{generic_story_id}/images",
    response_model=ApiResponse[GenericStoryImageUploadResponse],
)
async def upload_generic_story_workflow_images(
    workflow_id: UUID,
    generic_story_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryImageUploadResponse]:
    form = await request.form()
    uploads: dict[str, UploadFile] = {}
    for field_name, value in form.multi_items():
        if not hasattr(value, "read") or not hasattr(value, "filename"):
            continue
        normalized_field_name = str(field_name).strip()
        if normalized_field_name in uploads:
            raise AppException(
                f"Duplicate upload field: {normalized_field_name}",
                code="GENERIC_STORY_IMAGE_UPLOAD_DUPLICATE_FIELD",
            )
        uploads[normalized_field_name] = value

    data = await GenericStoryWorkflowService(session).upload_published_story_images(
        current_user.id,
        workflow_id,
        generic_story_id,
        uploads,
        public_base_url=str(request.base_url).rstrip("/"),
    )
    return success_response(data, "Generic story images uploaded successfully")


@router.post(
    "/workflows/{workflow_id}/stories/{generic_story_id}/audio",
    response_model=ApiResponse[GenericStoryAudioUploadResponse],
)
async def upload_generic_story_workflow_audio(
    workflow_id: UUID,
    generic_story_id: UUID,
    request: Request,
    language: str = Query(..., min_length=2, max_length=16),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryAudioUploadResponse]:
    form = await request.form()
    uploads: dict[str, UploadFile] = {}
    for field_name, value in form.multi_items():
        if not hasattr(value, "read") or not hasattr(value, "filename"):
            continue
        normalized_field_name = str(field_name).strip()
        if normalized_field_name in uploads:
            raise AppException(
                f"Duplicate upload field: {normalized_field_name}",
                code="GENERIC_STORY_AUDIO_UPLOAD_DUPLICATE_FIELD",
            )
        uploads[normalized_field_name] = value

    data = await GenericStoryWorkflowService(session).upload_published_story_audio(
        current_user.id,
        workflow_id,
        generic_story_id,
        language,
        uploads,
    )
    return success_response(data, "Generic story audio uploaded successfully")


@router.post(
    "/{generic_story_id}/images/batch",
    response_model=ApiResponse[GenericStoryBatchImageSubmitResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_generic_story_image_batch(
    generic_story_id: UUID,
    force: bool = Query(False, description="Regenerate all images even when existing image URLs are readable"),
    provider: str = Query("google", pattern="^(google|openai)$", description="Batch provider to use for image generation"),
    pages: list[int] | None = Query(
        default=None,
        description="Optional page numbers to submit, for example ?force=true&pages=7",
    ),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryBatchImageSubmitResponse]:
    data = await GenericStoryBatchService(session).submit_image_batch(
        user_id=current_user.id,
        generic_story_id=generic_story_id,
        force=force,
        page_numbers=set(pages or []) or None,
        provider=provider,
    )
    return success_response(data, data.message)


@router.post(
    "/{generic_story_id}/narration/batch",
    response_model=ApiResponse[GenericStoryBatchNarrationSubmitResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_generic_story_narration_batch(
    generic_story_id: UUID,
    language: str = Query("en", pattern="^(en|hi|mr)$", description="Story content language to narrate"),
    force: bool = Query(False, description="Regenerate narration even when existing audio metadata is present"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryBatchNarrationSubmitResponse]:
    data = await GenericStoryBatchService(session).submit_narration_batch(
        user_id=current_user.id,
        generic_story_id=generic_story_id,
        language=language,
        force=force,
    )
    return success_response(data, data.message)


@router.get(
    "/{generic_story_id}/narration/prompt",
    response_model=ApiResponse[GenericStoryNarrationPromptResponse],
)
async def get_generic_story_narration_prompt(
    generic_story_id: UUID,
    language: str = Query("en", pattern="^(en|hi|mr)$", description="Story content language to preview"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryNarrationPromptResponse]:
    _ = current_user
    data = await GenericStoryBatchService(session).get_narration_prompts(
        generic_story_id=generic_story_id,
        language=language,
    )
    return success_response(data, "Generic story narration prompts generated successfully")


@router.post(
    "/{generic_story_id}/images/multi-generate-test",
    response_model=ApiResponse[dict],
)
async def multi_generate_generic_story_images_test(
    generic_story_id: UUID,
    request: Request,
    language: str = Query("en", min_length=2, max_length=16),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[dict]:
    data = await GenericStoryMultiImageTestService(session).generate(
        current_user.id,
        generic_story_id,
        language=language,
        public_base_url=str(request.base_url).rstrip("/"),
    )
    return success_response(data, "Generic story multi-image test generation completed")


@router.post(
    "/{generic_story_id}/images/regenerate",
    response_model=ApiResponse[GenericStoryBatchImageSubmitResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_generic_story_page_image(
    generic_story_id: UUID,
    page_number: int = Query(..., ge=1, description="Story page number to regenerate"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryBatchImageSubmitResponse]:
    data = await GenericStoryBatchService(session).submit_image_batch(
        user_id=current_user.id,
        generic_story_id=generic_story_id,
        force=True,
        page_numbers={page_number},
    )
    return success_response(data, f"Generic story page {page_number} image regeneration submitted")


@router.post("/batch-jobs/reconcile", response_model=ApiResponse[dict])
async def reconcile_generic_story_batch_jobs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[dict]:
    _ = current_user
    data = await GenericStoryBatchService(session).reconcile_batch_jobs(limit=limit)
    return success_response(data, "Generic story batch jobs reconciled successfully")


@router.post(
    "/{generic_story_id}/batch-jobs/{batch_job_id}/cancel",
    response_model=ApiResponse[GenericStoryBatchJobCancelResponse],
)
async def cancel_generic_story_batch_job(
    generic_story_id: UUID,
    batch_job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryBatchJobCancelResponse]:
    data = await GenericStoryBatchService(session).cancel_batch_job(
        user_id=current_user.id,
        generic_story_id=generic_story_id,
        batch_job_id=batch_job_id,
    )
    return success_response(GenericStoryBatchJobCancelResponse(**data), data["message"])


@router.put("/{generic_story_id}", response_model=ApiResponse[GenericStoryResponse])
async def update_generic_story(
    generic_story_id: UUID,
    payload: GenericStoryUpdateRequest,
    language: str = Query("en", min_length=2, max_length=16),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).update(generic_story_id, payload, language=language)
    return success_response(data, "Generic story updated successfully")


@router.patch("/{generic_story_id}/status", response_model=ApiResponse[GenericStoryResponse])
async def update_generic_story_status(
    generic_story_id: UUID,
    payload: GenericStoryStatusUpdateRequest,
    language: str = Query("en", min_length=2, max_length=16),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).update_status(generic_story_id, payload, language=language)
    return success_response(data, "Generic story status updated successfully")


@router.delete("/{generic_story_id}", response_model=ApiResponse[None])
async def delete_generic_story(
    generic_story_id: UUID,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[None]:
    await GenericStoryService(session).delete(generic_story_id)
    return success_response(None, "Generic story deleted successfully")


@router.get("", response_model=ApiResponse[PaginatedResponse[StoryCatalogResponse]])
async def list_generic_stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    age_group: str = Query(..., min_length=1, max_length=32),
    theme: str | None = Query(default=None, min_length=1, max_length=100),
    language: str | None = Query(default=None, min_length=2, max_length=16),
    status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
    age_group = validate_age_group(age_group)
    data = await StoryCatalogService(session).list_generic_paginated(
        page=page,
        page_size=page_size,
        age_group=age_group,
        theme=theme,
        language=language,
        status_filter=status_filter,
    )
    return success_response(data, "Generic stories retrieved successfully")


@router.get(
    "/{generic_story_id}/content",
    response_model=ApiResponse[StoryContentResponse],
    response_model_exclude_none=True,
)
async def get_generic_story_content(
    generic_story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[StoryContentResponse]:
    data = await GenericStoryService(session).get_content(generic_story_id, language=language)
    return success_response(data, "Generic story content retrieved successfully")


@router.patch("/{generic_story_id}/content/page-text", response_model=ApiResponse[GenericStoryResponse])
async def update_generic_story_page_text(
    generic_story_id: UUID,
    payload: GenericStoryPageTextUpdateRequest,
    language: str = Query("en", min_length=2, max_length=16),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).update_page_text(generic_story_id, payload, language=language)
    return success_response(data, "Generic story retrieved successfully")


@router.patch("/{generic_story_id}/content/page-images", response_model=ApiResponse[GenericStoryResponse])
async def update_generic_story_page_images(
    generic_story_id: UUID,
    request: Request,
    language: str = Query("en", min_length=2, max_length=16),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    page_image_uploads = await _read_uploads_from_form(
        request,
        duplicate_code="GENERIC_STORY_PAGE_IMAGE_DUPLICATE_FIELD",
    )
    data = await GenericStoryService(session).update_page_images(
        generic_story_id,
        page_image_uploads,
        language=language,
        public_base_url=str(request.base_url).rstrip("/"),
    )
    return success_response(data, "Generic story page images updated successfully")


@router.patch("/{generic_story_id}/content/page-audio", response_model=ApiResponse[GenericStoryResponse])
async def update_generic_story_page_audio(
    generic_story_id: UUID,
    request: Request,
    language: str = Query("en", min_length=2, max_length=16),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    page_audio_uploads = await _read_uploads_from_form(
        request,
        duplicate_code="GENERIC_STORY_PAGE_AUDIO_DUPLICATE_FIELD",
    )
    data = await GenericStoryService(session).update_page_audio(
        generic_story_id,
        page_audio_uploads,
        language=language,
    )
    return success_response(data, "Generic story page audio updated successfully")


@router.get("/{generic_story_id}", response_model=ApiResponse[GenericStoryResponse])
async def get_generic_story(
    generic_story_id: UUID,
    language: str = Query("en", min_length=2, max_length=16),
    _: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ApiResponse[GenericStoryResponse]:
    data = await GenericStoryService(session).get(generic_story_id, language=language)
    return success_response(data, "Generic story retrieved successfully")
