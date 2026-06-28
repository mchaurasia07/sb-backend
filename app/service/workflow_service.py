from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.entity.custom_story_workflow import CustomStoryWorkflowType
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.model.request.story import StoryGenerationRequest
from app.model.response.common import PaginatedResponse
from app.model.response.custom_story_workflow import (
    CustomStoryWorkflowBatchJobResponse,
    CustomStoryWorkflowEventResponse,
    CustomStoryWorkflowResponse,
    CustomStoryWorkflowStepResponse,
)
from app.repository.workflow_repository import (
    WorkflowBatchJobRepository,
    WorkflowEventRepository,
    WorkflowRepository,
    WorkflowStepRepository,
)
from app.service.custom_story_workflow_service import CustomStoryWorkflowService


class WorkflowService(CustomStoryWorkflowService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.workflow_repo = WorkflowRepository(session)
        self.step_repo = WorkflowStepRepository(session)
        self.event_repo = WorkflowEventRepository(session)
        self.batch_job_repo = WorkflowBatchJobRepository(session)

        # The workflow engine inherited from CustomStoryWorkflowService still uses
        # these names internally; keep them as aliases while routing ownership here.
        self.workflows = self.workflow_repo
        self.steps = self.step_repo
        self.events = self.event_repo
        self.batch_jobs = self.batch_job_repo

    async def create(self, user_id: UUID, payload: StoryGenerationRequest) -> CustomStoryWorkflowResponse:
        if payload.story_type == CustomStoryWorkflowType.GENERIC:
            return await super().create_generic(user_id, payload)
        return await super().create(user_id, payload)

    async def list_workflows(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        child_id: UUID | None = None,
        status_filter: str | None = None,
        workflow_id: UUID | None = None,
        workflow_type: CustomStoryWorkflowType | None = None,
    ) -> PaginatedResponse[CustomStoryWorkflowResponse]:
        if workflow_id is not None:
            workflows, total = await self.workflow_repo.list_workflows(
                user_id,
                page=page,
                page_size=page_size,
                workflow_id=workflow_id,
                workflow_type=workflow_type,
            )
        else:
            workflows, total = await self.workflow_repo.list_for_user(
                user_id,
                page=page,
                page_size=page_size,
                child_id=child_id,
                status_filter=status_filter,
                story_type=workflow_type,
            )
        return PaginatedResponse[CustomStoryWorkflowResponse].create(
            items=[self._response(workflow) for workflow in workflows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def list(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        child_id: UUID | None = None,
        status_filter: str | None = None,
    ) -> PaginatedResponse[CustomStoryWorkflowResponse]:
        return await self.list_workflows(
            user_id,
            page=page,
            page_size=page_size,
            child_id=child_id,
            status_filter=status_filter,
            workflow_type=CustomStoryWorkflowType.CUSTOM,
        )

    async def get(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflowResponse:
        return self._response(await self._get_owned(user_id, workflow_id, story_type=None))

    async def delete(self, user_id: UUID, workflow_id: UUID) -> None:
        workflow = await self._get_owned(user_id, workflow_id, story_type=None)
        await self.workflow_repo.delete(workflow)
        await self.session.commit()

    async def get_steps(self, user_id: UUID, workflow_id: UUID) -> list[CustomStoryWorkflowStepResponse]:
        workflow = await self._get_owned(user_id, workflow_id, story_type=None)
        steps = await self.step_repo.list_by_workflow(workflow.id)
        return [self._step_response(step) for step in steps]

    async def get_events(
        self,
        user_id: UUID,
        workflow_id: UUID,
        story_type: CustomStoryWorkflowType | str | None = None,
    ) -> list[CustomStoryWorkflowEventResponse]:
        workflow = await self.workflow_repo.get_for_user(user_id, workflow_id)
        if workflow is None:
            raise NotFoundException("Custom story workflow not found")
        events = await self.event_repo.list_by_workflow_desc(workflow.id, story_type=story_type)
        return [self._event_response(event) for event in events]

    async def list_batch_jobs(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        workflow_id: UUID | None = None,
        status_filter: StoryBatchJobStatus | None = None,
        story_type: CustomStoryWorkflowType | None = None,
        generic_story_id: UUID | None = None,
        job_type: StoryBatchJobType | None = None,
        provider: str | None = None,
    ) -> PaginatedResponse[CustomStoryWorkflowBatchJobResponse]:
        jobs, total = await self.batch_job_repo.list_for_user(
            user_id,
            page=page,
            page_size=page_size,
            workflow_id=workflow_id,
            status=status_filter,
            story_type=story_type,
            generic_story_id=generic_story_id,
            job_type=job_type,
            provider=provider,
        )
        return PaginatedResponse[CustomStoryWorkflowBatchJobResponse].create(
            items=[self._batch_job_response(job) for job in jobs],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def list_batch_jobs_by_workflow_id(
        self,
        user_id: UUID,
        workflow_id: UUID,
    ) -> list[CustomStoryWorkflowBatchJobResponse]:
        workflow = await self._get_owned(user_id, workflow_id, story_type=None)
        batch_jobs = await self.batch_job_repo.list_by_workflow(workflow.id)
        return [self._batch_job_response(batch_job) for batch_job in batch_jobs]
