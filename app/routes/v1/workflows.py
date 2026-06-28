from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.dependencies import get_current_user
from app.entity.custom_story_workflow import CustomStoryWorkflowType
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.user import User
from app.model.request.story import StoryGenerationRequest, age_group_for_reader_category
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.custom_story_workflow import (
    CustomStoryWorkflowBatchJobCancelResponse,
    CustomStoryWorkflowBatchJobResponse,
    CustomStoryWorkflowEventResponse,
    CustomStoryWorkflowResponse,
    CustomStoryWorkflowStepResponse,
)
from app.model.response.story import StoryBatchJobReconcileResponse


class WorkflowsRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route(
            "",
            self.create_workflow,
            methods=["POST"],
            response_model=ApiResponse[CustomStoryWorkflowResponse],
            status_code=status.HTTP_201_CREATED,
        )
        self.router.add_api_route(
            "",
            self.list_workflows,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[CustomStoryWorkflowResponse]],
        )
        self.router.add_api_route(
            "/events/process",
            self.process_workflow_events,
            methods=["POST"],
            response_model=ApiResponse[dict[str, Any]],
        )
        self.router.add_api_route(
            "/batch-jobs",
            self.list_batch_jobs,
            methods=["GET"],
            response_model=ApiResponse[PaginatedResponse[CustomStoryWorkflowBatchJobResponse]],
        )
        self.router.add_api_route(
            "/batch-jobs/reconcile",
            self.reconcile_batch_jobs,
            methods=["POST"],
            response_model=ApiResponse[StoryBatchJobReconcileResponse],
        )
        self.router.add_api_route(
            "/{workflow_id}",
            self.get_workflow,
            methods=["GET"],
            response_model=ApiResponse[CustomStoryWorkflowResponse],
        )
        self.router.add_api_route(
            "/{workflow_id}",
            self.delete_workflow,
            methods=["DELETE"],
            response_model=ApiResponse[None],
        )
        self.router.add_api_route(
            "/{workflow_id}/steps",
            self.get_workflow_steps,
            methods=["GET"],
            response_model=ApiResponse[list[CustomStoryWorkflowStepResponse]],
        )
        self.router.add_api_route(
            "/{workflow_id}/events",
            self.get_workflow_events,
            methods=["GET"],
            response_model=ApiResponse[list[CustomStoryWorkflowEventResponse]],
        )
        self.router.add_api_route(
            "/{workflow_id}/retry",
            self.retry_workflow,
            methods=["POST"],
            response_model=ApiResponse[CustomStoryWorkflowResponse],
            status_code=status.HTTP_202_ACCEPTED,
        )
        self.router.add_api_route(
            "/{workflow_id}/batch-jobs",
            self.list_batch_jobs_by_workflow_id,
            methods=["GET"],
            response_model=ApiResponse[list[CustomStoryWorkflowBatchJobResponse]],
        )
        self.router.add_api_route(
            "/{workflow_id}/batch-jobs/{batch_job_id}/cancel",
            self.cancel_batch_job,
            methods=["POST"],
            response_model=ApiResponse[CustomStoryWorkflowBatchJobCancelResponse],
        )

    async def create_workflow(
        self,
        payload: StoryGenerationRequest,
        response: Response,
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[CustomStoryWorkflowResponse]:
        self._populate_age_group_from_reader_category(payload)
        data = await container.workflow_service.create(current_user.id, payload)
        workflow_label = "Generic story" if data.story_type == "GENERIC" else "Custom story"
        if data.execute_workflow:
            response.status_code = status.HTTP_202_ACCEPTED
            return success_response(data, f"{workflow_label} workflow queued successfully")
        response.status_code = status.HTTP_201_CREATED
        return success_response(data, f"{workflow_label} workflow saved successfully; execution skipped")

    async def list_workflows(
        self,
        child_id: UUID | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        workflow_id: UUID | None = Query(default=None),
        workflow_type: CustomStoryWorkflowType | None = Query(default=None, alias="type"),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[CustomStoryWorkflowResponse]]:
        data = await container.workflow_service.list_workflows(
            current_user.id,
            page=page,
            page_size=page_size,
            child_id=child_id,
            status_filter=status_filter,
            workflow_id=workflow_id,
            workflow_type=workflow_type,
        )
        return success_response(data, "Workflows retrieved successfully")

    async def process_workflow_events(
        self,
        limit: int = Query(10, ge=1, le=100),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[dict[str, Any]]:
        _ = current_user
        data = await container.workflow_service.process_events(limit=limit)
        return success_response(data, "Custom story workflow events processed successfully")

    async def list_batch_jobs(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        workflow_id: UUID | None = Query(default=None),
        story_type: CustomStoryWorkflowType | None = Query(default=None),
        status_filter: StoryBatchJobStatus | None = Query(default=None, alias="status"),
        generic_story_id: UUID | None = Query(default=None),
        job_type: StoryBatchJobType | None = Query(default=None),
        provider: str | None = Query(default=None),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[PaginatedResponse[CustomStoryWorkflowBatchJobResponse]]:
        data = await container.workflow_service.list_batch_jobs(
            current_user.id,
            page=page,
            page_size=page_size,
            workflow_id=workflow_id,
            story_type=story_type,
            status_filter=status_filter,
            generic_story_id=generic_story_id,
            job_type=job_type,
            provider=provider,
        )
        return success_response(data, "Batch jobs retrieved successfully")

    async def reconcile_batch_jobs(
        self,
        limit: int = Query(50, ge=1, le=200),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[StoryBatchJobReconcileResponse]:
        _ = current_user
        workflow_data = await container.workflow_service.reconcile_batch_jobs(limit=limit)
        return success_response(
            StoryBatchJobReconcileResponse(**workflow_data),
            "Workflow batch jobs reconciled successfully",
        )

    async def get_workflow(
        self,
        workflow_id: UUID = Path(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[CustomStoryWorkflowResponse]:
        data = await container.workflow_service.get(current_user.id, workflow_id)
        return success_response(data, "Custom story workflow retrieved successfully")

    async def delete_workflow(
        self,
        workflow_id: UUID = Path(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[None]:
        await container.workflow_service.delete(current_user.id, workflow_id)
        return success_response(None, "Custom story workflow deleted successfully")

    async def get_workflow_steps(
        self,
        workflow_id: UUID = Path(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[CustomStoryWorkflowStepResponse]]:
        data = await container.workflow_service.get_steps(current_user.id, workflow_id)
        return success_response(data, "Custom story workflow steps retrieved successfully")

    async def get_workflow_events(
        self,
        workflow_id: UUID = Path(...),
        story_type: str | None = Query(default=None, pattern="^(CUSTOM|GENERIC)$"),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[CustomStoryWorkflowEventResponse]]:
        data = await container.workflow_service.get_events(current_user.id, workflow_id, story_type=story_type)
        return success_response(data, "Story workflow events retrieved successfully")

    async def retry_workflow(
        self,
        workflow_id: UUID = Path(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[CustomStoryWorkflowResponse]:
        data = await container.workflow_service.retry(current_user.id, workflow_id)
        return success_response(data, "Story workflow retry queued successfully")

    async def list_batch_jobs_by_workflow_id(
        self,
        workflow_id: UUID = Path(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[CustomStoryWorkflowBatchJobResponse]]:
        data = await container.workflow_service.list_batch_jobs_by_workflow_id(current_user.id, workflow_id)
        return success_response(data, "Batch jobs retrieved successfully")

    async def cancel_batch_job(
        self,
        workflow_id: UUID = Path(...),
        batch_job_id: UUID = Path(...),
        current_user: User = Depends(get_current_user),
        container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[CustomStoryWorkflowBatchJobCancelResponse]:
        data = await container.workflow_service.cancel_batch_job(
            user_id=current_user.id,
            workflow_id=workflow_id,
            batch_job_id=batch_job_id,
        )
        return success_response(CustomStoryWorkflowBatchJobCancelResponse(**data), "Batch job cancelled successfully")

    @staticmethod
    def _populate_age_group_from_reader_category(payload: StoryGenerationRequest) -> None:
        if payload.reader_category:
            payload.age_group = age_group_for_reader_category(payload.reader_category)


router = WorkflowsRouter(app_container).router
