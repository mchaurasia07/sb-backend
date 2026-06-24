from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.custom_story_workflow import CustomStoryWorkflowType
from app.model.response.common import PaginatedResponse
from app.model.response.custom_story_workflow import CustomStoryWorkflowBatchJobResponse, CustomStoryWorkflowEventResponse, CustomStoryWorkflowResponse
from app.repository.workflow_repository import CustomStoryBatchJobRepository, CustomStoryWorkflowEventRepository, WorkflowRepository
from app.service.custom_story_workflow_service import CustomStoryWorkflowService


class WorkflowService:
    def __init__(self, session: AsyncSession):
        self.workflow_repo = WorkflowRepository(session)
        self.event_repo = CustomStoryWorkflowEventRepository(session)
        self.batch_job_repo = CustomStoryBatchJobRepository(session)

    async def list_workflows(
        self,
        page: int,
        page_size: int,
        workflow_id: UUID | None = None,
        workflow_type: CustomStoryWorkflowType | None = None,
    ) -> PaginatedResponse[CustomStoryWorkflowResponse]:
        workflows, total = await self.workflow_repo.list_workflows(
            page=page,
            page_size=page_size,
            workflow_id=workflow_id,
            workflow_type=workflow_type,
        )
        return PaginatedResponse[CustomStoryWorkflowResponse].create(
            items=[CustomStoryWorkflowService._response(workflow) for workflow in workflows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_events(self, workflow_id: UUID, story_type: CustomStoryWorkflowType | str | None = None) -> list[CustomStoryWorkflowEventResponse]:
        events = await self.event_repo.list_by_workflow_desc(workflow_id, story_type=story_type)
        return [CustomStoryWorkflowService._event_response(event) for event in events]

    async def list_batch_jobs_by_Workflow_id(self, workflow_id: UUID) -> list[CustomStoryWorkflowBatchJobResponse]:
        """List batch jobs for user with optional filtering by workflow and status."""

        batch_jobs = await self.batch_job_repo.list_batch_jobs_by_Workflow_id(workflow_id)
        return [CustomStoryWorkflowService._batch_job_response(batch_job) for batch_job in batch_jobs]