from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.core.container import RequestContainer, app_container, get_request_container
from app.core.dependencies import get_current_user
from app.entity.custom_story_workflow import CustomStoryWorkflowType
from app.entity.user import User
from app.model.response.common import ApiResponse, PaginatedResponse, success_response
from app.model.response.custom_story_workflow import CustomStoryWorkflowBatchJobResponse, CustomStoryWorkflowEventResponse, CustomStoryWorkflowResponse


class WorkflowsRouter:
    def __init__(self, container=app_container):
        self.container = container
        self.router = APIRouter()
        self.router.add_api_route("", self.list_workflows, methods=["GET"], response_model=ApiResponse[PaginatedResponse[CustomStoryWorkflowResponse]],)
        self.router.add_api_route("/{workflow_id}/events", self.get_story_workflow_events, methods=["GET"], response_model=ApiResponse[list[CustomStoryWorkflowEventResponse]],)
        self.router.add_api_route("/{workflow_id}/batch-jobs", self.list_batch_jobs_by_Workflow_id, methods=["GET"], response_model=ApiResponse[list[CustomStoryWorkflowBatchJobResponse]])
    
    async def list_workflows(self,page: int = Query(1, ge=1),page_size: int = Query(20, ge=1, le=100),workflow_id: UUID | None = Query(default=None),workflow_type: CustomStoryWorkflowType | None = Query(default=None, alias="type"),current_user: User = Depends(get_current_user),container: RequestContainer = Depends(get_request_container)) -> ApiResponse[PaginatedResponse[CustomStoryWorkflowResponse]]:
        data = await container.workflow_service.list_workflows(page=page,page_size=page_size,workflow_id=workflow_id,workflow_type=workflow_type)
        return success_response(data, "Workflows retrieved successfully")

    async def get_story_workflow_events(self,workflow_id: UUID = Path(...), story_type: str | None = Query(default=None, pattern="^(CUSTOM|GENERIC)$"), current_user: User = Depends(get_current_user), container: RequestContainer = Depends(get_request_container)) -> ApiResponse[list[CustomStoryWorkflowEventResponse]]:
        data = await container.workflow_service.get_events(workflow_id, story_type=story_type)
        return success_response(data, "Story workflow events retrieved successfully")

    async def list_batch_jobs_by_Workflow_id(self, workflow_id: UUID = Path(...), current_user: User = Depends(get_current_user), container: RequestContainer = Depends(get_request_container),
    ) -> ApiResponse[list[CustomStoryWorkflowBatchJobResponse]]:
        """List workflow batch jobs with optional filtering."""
        data = await container.workflow_service.list_batch_jobs_by_Workflow_id(workflow_id)
        return success_response(data, "Batch jobs retrieved successfully")
router = WorkflowsRouter(app_container).router
