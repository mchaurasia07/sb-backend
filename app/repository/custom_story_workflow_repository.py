from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.entity.custom_story_workflow import (
    CustomStoryBatchJob,
    CustomStoryWorkflow,
    CustomStoryWorkflowStep,
    CustomStoryWorkflowStepRecord,
)
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StepStatus


class CustomStoryWorkflowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> CustomStoryWorkflow:
        if kwargs.get("request_number") is None:
            kwargs["request_number"] = await self.next_request_number()
        workflow = CustomStoryWorkflow(**kwargs)
        self.session.add(workflow)
        await self.session.flush()
        return workflow

    async def next_request_number(self) -> int:
        latest = await self.session.scalar(select(func.max(CustomStoryWorkflow.request_number)))
        return int(latest or 0) + 1

    async def get_by_id(self, workflow_id: UUID) -> CustomStoryWorkflow | None:
        result = await self.session.execute(
            select(CustomStoryWorkflow).where(CustomStoryWorkflow.id == workflow_id)
        )
        return result.scalar_one_or_none()

    async def get_for_user(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflow | None:
        result = await self.session.execute(
            select(CustomStoryWorkflow).where(
                CustomStoryWorkflow.id == workflow_id,
                CustomStoryWorkflow.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_user_for_update(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflow | None:
        result = await self.session.execute(
            select(CustomStoryWorkflow)
            .where(CustomStoryWorkflow.id == workflow_id, CustomStoryWorkflow.user_id == user_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, workflow_id: UUID) -> CustomStoryWorkflow | None:
        result = await self.session.execute(
            select(CustomStoryWorkflow).where(CustomStoryWorkflow.id == workflow_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        child_id: UUID | None = None,
        status_filter: str | None = None,
    ) -> tuple[list[CustomStoryWorkflow], int]:
        filters = [CustomStoryWorkflow.user_id == user_id]
        if child_id is not None:
            filters.append(CustomStoryWorkflow.child_id == child_id)
        if status_filter:
            filters.append(CustomStoryWorkflow.status == status_filter)
        total = await self.session.scalar(select(func.count()).select_from(CustomStoryWorkflow).where(*filters))
        result = await self.session.execute(
            select(CustomStoryWorkflow)
            .where(*filters)
            .order_by(CustomStoryWorkflow.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def update(self, workflow: CustomStoryWorkflow) -> CustomStoryWorkflow:
        for field_name in ("story_plan_json", "story_json", "image_plan_json"):
            if getattr(workflow, field_name, None) is not None:
                flag_modified(workflow, field_name)
        await self.session.flush()
        return workflow

    async def delete(self, workflow: CustomStoryWorkflow) -> None:
        await self.session.delete(workflow)
        await self.session.flush()


class CustomStoryWorkflowStepRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, workflow_id: UUID, step_name: str, retry_count: int = 0) -> CustomStoryWorkflowStepRecord:
        value = step_name.value if hasattr(step_name, "value") else str(step_name)
        step = CustomStoryWorkflowStepRecord(
            workflow_id=workflow_id,
            step_name=CustomStoryWorkflowStep(value),
            status=StepStatus.PENDING,
            retry_count=retry_count,
        )
        self.session.add(step)
        await self.session.flush()
        return step

    async def latest_for_story_step(self, story_id: UUID, step_name) -> CustomStoryWorkflowStepRecord | None:
        value = step_name.value if hasattr(step_name, "value") else str(step_name)
        id_result = await self.session.execute(
            select(CustomStoryWorkflowStepRecord.id)
            .where(
                CustomStoryWorkflowStepRecord.workflow_id == story_id,
                CustomStoryWorkflowStepRecord.step_name == CustomStoryWorkflowStep(value),
            )
            .order_by(CustomStoryWorkflowStepRecord.created_at.desc())
            .limit(1)
        )
        step_id = id_result.scalar_one_or_none()
        if step_id is None:
            return None

        result = await self.session.execute(
            select(CustomStoryWorkflowStepRecord).where(CustomStoryWorkflowStepRecord.id == step_id)
        )
        return result.scalar_one_or_none()

    async def latest_for_workflow_step(
        self, workflow_id: UUID, step_name: CustomStoryWorkflowStep
    ) -> CustomStoryWorkflowStepRecord | None:
        return await self.latest_for_story_step(workflow_id, step_name)

    async def list_by_workflow(self, workflow_id: UUID) -> list[CustomStoryWorkflowStepRecord]:
        id_result = await self.session.execute(
            select(CustomStoryWorkflowStepRecord.id)
            .where(CustomStoryWorkflowStepRecord.workflow_id == workflow_id)
            .order_by(CustomStoryWorkflowStepRecord.created_at.asc())
        )
        step_ids = list(id_result.scalars().all())
        if not step_ids:
            return []

        result = await self.session.execute(
            select(CustomStoryWorkflowStepRecord).where(CustomStoryWorkflowStepRecord.id.in_(step_ids))
        )
        steps_by_id = {str(step.id): step for step in result.scalars().all()}
        return [steps_by_id[str(step_id)] for step_id in step_ids if str(step_id) in steps_by_id]

    async def update(self, step: CustomStoryWorkflowStepRecord) -> CustomStoryWorkflowStepRecord:
        for field_name in ("input_json", "output_json"):
            if getattr(step, field_name, None) is not None:
                flag_modified(step, field_name)
        await self.session.flush()
        return step


class CustomStoryBatchJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        workflow_id: UUID,
        job_type: StoryBatchJobType,
        attempt: int,
        expected_item_count: int,
        request_keys: list[str],
        provider_model: str | None,
        request_payload: dict | None = None,
        story_id: UUID | None = None,
    ) -> CustomStoryBatchJob:
        job = CustomStoryBatchJob(
            workflow_id=workflow_id,
            story_id=story_id,
            job_type=job_type,
            status=StoryBatchJobStatus.SUBMITTED,
            provider="google",
            provider_model=provider_model,
            attempt=attempt,
            expected_item_count=expected_item_count,
            request_keys=request_keys,
            missing_keys=request_keys,
            request_payload=request_payload,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def latest_for_workflow_type(
        self, workflow_id: UUID, job_type: StoryBatchJobType
    ) -> CustomStoryBatchJob | None:
        id_result = await self.session.execute(
            select(CustomStoryBatchJob.id)
            .where(CustomStoryBatchJob.workflow_id == workflow_id, CustomStoryBatchJob.job_type == job_type)
            .order_by(CustomStoryBatchJob.created_at.desc())
            .limit(1)
        )
        job_id = id_result.scalar_one_or_none()
        if job_id is None:
            return None

        result = await self.session.execute(
            select(CustomStoryBatchJob).where(CustomStoryBatchJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_active_for_workflow(self, workflow_id: UUID) -> list[CustomStoryBatchJob]:
        result = await self.session.execute(
            select(CustomStoryBatchJob).where(
                CustomStoryBatchJob.workflow_id == workflow_id,
                CustomStoryBatchJob.status.in_([StoryBatchJobStatus.SUBMITTED, StoryBatchJobStatus.RUNNING]),
            )
        )
        return list(result.scalars().all())

    async def list_by_workflow(self, workflow_id: UUID) -> list[CustomStoryBatchJob]:
        id_result = await self.session.execute(
            select(CustomStoryBatchJob.id)
            .where(CustomStoryBatchJob.workflow_id == workflow_id)
            .order_by(CustomStoryBatchJob.created_at.asc())
        )
        job_ids = list(id_result.scalars().all())
        if not job_ids:
            return []

        result = await self.session.execute(
            select(CustomStoryBatchJob).where(CustomStoryBatchJob.id.in_(job_ids))
        )
        jobs_by_id = {str(job.id): job for job in result.scalars().all()}
        return [jobs_by_id[str(job_id)] for job_id in job_ids if str(job_id) in jobs_by_id]

    async def list_reconcilable(self, limit: int = 50) -> list[CustomStoryBatchJob]:
        id_result = await self.session.execute(
            select(CustomStoryBatchJob.id)
            .where(
                CustomStoryBatchJob.status.in_([StoryBatchJobStatus.SUBMITTED, StoryBatchJobStatus.RUNNING]),
                CustomStoryBatchJob.provider_job_name.is_not(None),
            )
            .order_by(CustomStoryBatchJob.updated_at.asc())
            .limit(limit)
        )
        job_ids = list(id_result.scalars().all())
        if not job_ids:
            return []

        result = await self.session.execute(
            select(CustomStoryBatchJob).where(CustomStoryBatchJob.id.in_(job_ids))
        )
        jobs_by_id = {str(job.id): job for job in result.scalars().all()}
        return [jobs_by_id[str(job_id)] for job_id in job_ids if str(job_id) in jobs_by_id]

    async def update(self, job: CustomStoryBatchJob) -> CustomStoryBatchJob:
        for field_name in ("request_keys", "missing_keys", "request_payload", "response_payload"):
            if getattr(job, field_name, None) is not None:
                flag_modified(job, field_name)
        await self.session.flush()
        return job
