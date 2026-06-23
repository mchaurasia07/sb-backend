import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile, status

from app.core.age_groups import validate_age_group
from app.core.config import settings
from app.core.container import RequestContainer, app_container, get_request_container
from app.core.database import AsyncSessionLocal
from app.core.dependencies import AuthContext, get_auth_context, get_current_user
from app.core.exceptions import AppException
from app.entity.notification import NotificationAudience
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
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
    GenericStoryBatchJobResponse,
    GenericStoryImageUploadResponse,
    GenericStoryResponse,
)
from app.model.response.generic_story_workflow import (
    GenericStoryWorkflowListResponse,
    GenericStoryWorkflowResponse,
    GenericStoryWorkflowStepDetailResponse,
)
from app.model.response.story_catalog import StoryCatalogResponse
from app.model.response.story_content import StoryContentResponse
from app.service.custom_story_workflow_service import CustomStoryWorkflowService
from app.service.image_optimizer import optimize_display_image

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
            container = app_container.request(session)
            await container.notification.send_to_audience(
                audience=NotificationAudience.CHILDREN,
                event_type="new_generic_story_added",
                title="New story in the library",
                body=f"{title} is ready to read.",
                data=container.notification._build_deep_link_data(
                    event_type="new_generic_story_added",
                    route="generic_story_detail",
                    fallback_route="child_dashboard",
                    params={"generic_story_id": str(story_id)},
                ),
                delivery={"channelId": "story-updates", "priority": "high", "sound": "default"},
            )
        except Exception:
            await session.rollback()
            logger.exception("Failed to send new generic story notification: story_id=%s", story_id)


class GenericStoriesRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "",
            self.create_generic_story,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "/workflows",
            self.create_generic_story_workflow,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryWorkflowResponse],
            status_code=status.HTTP_202_ACCEPTED,
        )
        self.router.add_api_route(
            "/workflow",
            self.create_generic_story_workflow,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryWorkflowResponse],
            status_code=status.HTTP_202_ACCEPTED,
        )
        self.router.add_api_route(
            "/workflows",
            self.list_generic_story_workflows,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[GenericStoryWorkflowListResponse]],
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}",
            self.get_generic_story_workflow,
            methods=["GET"],
            response_model=ApiResponse[GenericStoryWorkflowResponse],
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}",
            self.delete_generic_story_workflow,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}/steps",
            self.get_generic_story_workflow_steps,
            methods=["GET"],
            response_model=ApiResponse[list[GenericStoryWorkflowStepDetailResponse]],
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}/execute",
            self.execute_generic_story_workflow,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryWorkflowResponse],
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}/retry",
            self.retry_generic_story_workflow,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryWorkflowResponse],
        )
        self.router.add_api_route(
            "/images/reduce",
            self.reduce_generic_story_image_for_preview,
            methods=["POST"],
            response_class=Response,
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}/stories/{generic_story_id}/images",
            self.upload_generic_story_workflow_images,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryImageUploadResponse],
        )
        self.router.add_api_route(
            "/workflows/{workflow_id}/stories/{generic_story_id}/audio",
            self.upload_generic_story_workflow_audio,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryAudioUploadResponse],
        )
        self.router.add_api_route(
            "/batch-jobs",
            self.list_generic_story_batch_jobs,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[GenericStoryBatchJobResponse]],
        )
        self.router.add_api_route(
            "/batch-jobs/reconcile",
            self.reconcile_generic_story_batch_jobs,
            methods=["POST"],
            response_model=ApiResponse[dict],
        )
        self.router.add_api_route(
            "/{generic_story_id}/batch-jobs/{batch_job_id}/cancel",
            self.cancel_generic_story_batch_job,
            methods=["POST"],
            response_model=ApiResponse[GenericStoryBatchJobCancelResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}",
            self.update_generic_story,
            methods=["PUT"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}/status",
            self.update_generic_story_status,
            methods=["PATCH"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}",
            self.delete_generic_story,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "",
            self.list_generic_stories,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[StoryCatalogResponse]],
        )
        self.router.add_api_route(
            "/{generic_story_id}/content",
            self.get_generic_story_content,
            methods=["GET"],
            response_model=ApiResponse[StoryContentResponse],
            response_model_exclude_none=True,
        )
        self.router.add_api_route(
            "/{generic_story_id}/content/page-text",
            self.update_generic_story_page_text,
            methods=["PATCH"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}/content/page-images",
            self.update_generic_story_page_images,
            methods=["PATCH"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}/content/page-audio",
            self.update_generic_story_page_audio,
            methods=["PATCH"],
            response_model=ApiResponse[GenericStoryResponse],
        )
        self.router.add_api_route(
            "/{generic_story_id}",
            self.get_generic_story,
            methods=["GET"],
            response_model=ApiResponse[GenericStoryResponse],
        )

    async def create_generic_story(
        self,
        payload: GenericStoryCreateRequest,
        background_tasks: BackgroundTasks,
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.create(payload)
        if data.status == "active":
            background_tasks.add_task(
                send_new_generic_story_notification_background,
                story_id=data.id,
                title=data.title,
            )
        return success_response(data, "Generic story created successfully")

    async def create_generic_story_workflow(
        self,
        payload: GenericStoryWorkflowCreateRequest,
        response: Response,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryWorkflowResponse]:
        data = await container.custom_story_workflow.create_generic(current_user.id, payload)
        response.status_code = status.HTTP_202_ACCEPTED
        return success_response(data, "Generic story workflow queued successfully")

    async def list_generic_story_workflows(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        user_id: UUID | None = Query(None),
        title: str | None = Query(default=None, max_length=255),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[GenericStoryWorkflowListResponse]]:
        data = await container.custom_story_workflow.list_generic(
            user_id=user_id or current_user.id,
            title=title,
            page=page,
            page_size=page_size,
        )
        return success_response(data, "Generic story workflows retrieved successfully")

    async def get_generic_story_workflow(
        self,
        workflow_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryWorkflowResponse]:
        data = await container.custom_story_workflow.get_generic(current_user.id, workflow_id)
        return success_response(data, "Generic story workflow retrieved successfully")

    async def delete_generic_story_workflow(
        self,
        workflow_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.custom_story_workflow.delete_generic(current_user.id, workflow_id)
        return success_response(None, "Generic story workflow deleted successfully")

    async def get_generic_story_workflow_steps(
        self,
        workflow_id: UUID,
        step_name: str | None = Query(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[GenericStoryWorkflowStepDetailResponse]]:
        data = await container.custom_story_workflow.get_generic_steps(
            current_user.id,
            workflow_id,
            step_name=step_name,
        )
        return success_response(data, "Generic story workflow steps retrieved successfully")

    async def execute_generic_story_workflow(
        self,
        workflow_id: UUID,
        payload: GenericStoryWorkflowExecuteRequest,
        request: Request,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryWorkflowResponse]:
        _ = payload, request
        data = await container.custom_story_workflow.retry_generic(current_user.id, workflow_id)
        message = (
            "Generic story workflow completed successfully"
            if data.status == "COMPLETED"
            else "Generic story workflow step executed successfully"
        )
        return success_response(data, message)

    async def retry_generic_story_workflow(
        self,
        workflow_id: UUID,
        request: Request,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryWorkflowResponse]:
        _ = request
        data = await container.custom_story_workflow.retry_generic(current_user.id, workflow_id)
        message = (
            "Generic story workflow completed successfully"
            if data.status == "COMPLETED"
            else "Generic story workflow retry executed successfully"
        )
        return success_response(data, message)

    async def reduce_generic_story_image_for_preview(
        self,
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

    async def upload_generic_story_workflow_images(
        self,
        workflow_id: UUID,
        generic_story_id: UUID,
        request: Request,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryImageUploadResponse]:
        uploads = await _read_uploads_from_form(
            request,
            duplicate_code="GENERIC_STORY_IMAGE_UPLOAD_DUPLICATE_FIELD",
        )
        workflow = await container.custom_story_workflow.get_generic(current_user.id, workflow_id)
        if workflow.generic_story_id != generic_story_id:
            raise AppException("Generic story does not belong to this workflow", status.HTTP_404_NOT_FOUND, "GENERIC_STORY_WORKFLOW_NOT_FOUND")
        story = await container.generic_story.update_page_images(
            generic_story_id,
            uploads,
            language=workflow.language,
            public_base_url=str(request.base_url).rstrip("/"),
        )
        pages = story.story_json.get("pages") if isinstance(story.story_json, dict) else []
        data = GenericStoryImageUploadResponse(
            workflow_id=workflow_id,
            generic_story_id=generic_story_id,
            cover_image_url=story.cover_image or "",
            page_image_urls={
                int(page.get("page_number")): str(page.get("image_url"))
                for page in pages
                if isinstance(page, dict) and page.get("page_number") and page.get("image_url")
            },
            updated_languages=story.available_languages,
        )
        return success_response(data, "Generic story images uploaded successfully")

    async def upload_generic_story_workflow_audio(
        self,
        workflow_id: UUID,
        generic_story_id: UUID,
        request: Request,
        language: str = Query(..., min_length=2, max_length=16),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryAudioUploadResponse]:
        uploads = await _read_uploads_from_form(
            request,
            duplicate_code="GENERIC_STORY_AUDIO_UPLOAD_DUPLICATE_FIELD",
        )
        workflow = await container.custom_story_workflow.get_generic(current_user.id, workflow_id)
        if workflow.generic_story_id != generic_story_id:
            raise AppException("Generic story does not belong to this workflow", status.HTTP_404_NOT_FOUND, "GENERIC_STORY_WORKFLOW_NOT_FOUND")
        story = await container.generic_story.update_page_audio(
            generic_story_id,
            uploads,
            language,
        )
        pages = story.story_json.get("pages") if isinstance(story.story_json, dict) else []
        data = GenericStoryAudioUploadResponse(
            workflow_id=workflow_id,
            generic_story_id=generic_story_id,
            language=language,
            page_audio_urls={
                int(page.get("page_number")): str(page.get("audio_url"))
                for page in pages
                if isinstance(page, dict) and page.get("page_number") and page.get("audio_url")
            },
            updated_languages=story.available_languages,
        )
        return success_response(data, "Generic story audio uploaded successfully")

    async def list_generic_story_batch_jobs(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        generic_story_id: UUID | None = Query(default=None),
        workflow_id: UUID | None = Query(default=None),
        status_filter: StoryBatchJobStatus | None = Query(default=None, alias="status"),
        job_type: StoryBatchJobType | None = Query(default=None),
        provider: str | None = Query(default=None, min_length=1, max_length=32),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[GenericStoryBatchJobResponse]]:
        data = await container.custom_story_workflow.list_batch_jobs(
            current_user.id,
            page=page,
            page_size=page_size,
            workflow_id=workflow_id,
            status_filter=status_filter,
            story_type=CustomStoryWorkflowService._workflow_type_generic(),
            generic_story_id=generic_story_id,
            job_type=job_type,
            provider=provider,
        )
        return success_response(data, "Generic story batch jobs retrieved successfully")

    async def reconcile_generic_story_batch_jobs(
        self,
        limit: int = Query(50, ge=1, le=200),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[dict]:
        _ = current_user
        data = await container.custom_story_workflow.reconcile_batch_jobs(limit=limit)
        return success_response(data, "Generic story batch jobs reconciled successfully")

    async def cancel_generic_story_batch_job(
        self,
        generic_story_id: UUID,
        batch_job_id: UUID,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryBatchJobCancelResponse]:
        data = await container.custom_story_workflow.cancel_generic_batch_job(
            user_id=current_user.id,
            generic_story_id=generic_story_id,
            batch_job_id=batch_job_id,
        )
        return success_response(GenericStoryBatchJobCancelResponse(**data), data["message"])

    async def update_generic_story(
        self,
        generic_story_id: UUID,
        payload: GenericStoryUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.update(generic_story_id, payload, language=language)
        return success_response(data, "Generic story updated successfully")

    async def update_generic_story_status(
        self,
        generic_story_id: UUID,
        payload: GenericStoryStatusUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.update_status(generic_story_id, payload, language=language)
        return success_response(data, "Generic story status updated successfully")

    async def delete_generic_story(
        self,
        generic_story_id: UUID,
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.generic_story.delete(generic_story_id)
        return success_response(None, "Generic story deleted successfully")

    async def list_generic_stories(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        age_group: str = Query(..., min_length=1, max_length=32),
        theme: str | None = Query(default=None, min_length=1, max_length=100),
        language: str | None = Query(default=None, min_length=2, max_length=16),
        status_filter: Literal["active", "inactive"] | None = Query(default=None, alias="status"),
        _: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[StoryCatalogResponse]]:
        age_group = validate_age_group(age_group)
        data = await container.story_catalog.list_generic_paginated(
            page=page,
            page_size=page_size,
            age_group=age_group,
            theme=theme,
            language=language,
            status_filter=status_filter,
        )
        return success_response(data, "Generic stories retrieved successfully")

    async def get_generic_story_content(
        self,
        generic_story_id: UUID,
        language: str = Query("en", min_length=2, max_length=16),
        _: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryContentResponse]:
        data = await container.generic_story.get_content(generic_story_id, language=language)
        return success_response(data, "Generic story content retrieved successfully")

    async def update_generic_story_page_text(
        self,
        generic_story_id: UUID,
        payload: GenericStoryPageTextUpdateRequest,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.update_page_text(generic_story_id, payload, language=language)
        return success_response(data, "Generic story retrieved successfully")

    async def update_generic_story_page_images(
        self,
        generic_story_id: UUID,
        request: Request,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        page_image_uploads = await _read_uploads_from_form(
            request,
            duplicate_code="GENERIC_STORY_PAGE_IMAGE_DUPLICATE_FIELD",
        )
        data = await container.generic_story.update_page_images(
            generic_story_id,
            page_image_uploads,
            language=language,
            public_base_url=str(request.base_url).rstrip("/"),
        )
        return success_response(data, "Generic story page images updated successfully")

    async def update_generic_story_page_audio(
        self,
        generic_story_id: UUID,
        request: Request,
        language: str = Query("en", min_length=2, max_length=16),
        _: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        page_audio_uploads = await _read_uploads_from_form(
            request,
            duplicate_code="GENERIC_STORY_PAGE_AUDIO_DUPLICATE_FIELD",
        )
        data = await container.generic_story.update_page_audio(
            generic_story_id,
            page_audio_uploads,
            language=language,
        )
        return success_response(data, "Generic story page audio updated successfully")

    async def get_generic_story(
        self,
        generic_story_id: UUID,
        language: str = Query("en", min_length=2, max_length=16),
        _: AuthContext = Depends(get_auth_context),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[GenericStoryResponse]:
        data = await container.generic_story.get(generic_story_id, language=language)
        return success_response(data, "Generic story retrieved successfully")


router = GenericStoriesRouter(app_container).router
