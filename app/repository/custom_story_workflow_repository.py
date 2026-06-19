from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app.entity.custom_story_workflow import (
    CustomStoryBatchJob,
    CustomStoryWorkflowEvent,
    CustomStoryWorkflowEventStatus,
    CustomStoryWorkflow,
    CustomStoryWorkflowStep,
    CustomStoryWorkflowStepRecord,
    CustomStoryWorkflowType,
)
from app.entity.custom_story_input_safety_audit import CustomStoryInputSafetyAudit, CustomStoryInputSafetyAuditStatus
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
        story_type: CustomStoryWorkflowType | str | None = CustomStoryWorkflowType.CUSTOM,
        title: str | None = None,
    ) -> tuple[list[CustomStoryWorkflow], int]:
        filters = [CustomStoryWorkflow.user_id == user_id]
        if story_type is not None:
            filters.append(CustomStoryWorkflow.story_type == story_type)
        if child_id is not None:
            filters.append(CustomStoryWorkflow.child_id == child_id)
        if status_filter:
            filters.append(CustomStoryWorkflow.status == status_filter)
        if title:
            normalized_title = title.strip().lower()
            if normalized_title:
                filters.append(
                    or_(
                        func.lower(CustomStoryWorkflow.title).like(f"%{normalized_title}%"),
                        func.lower(CustomStoryWorkflow.source_title).like(f"%{normalized_title}%"),
                    )
                )
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
        for field_name in ("story_plan_json", "story_json", "image_plan_json", "input_request", "languages"):
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


class CustomStoryWorkflowEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        workflow_id: UUID,
        step_name: CustomStoryWorkflowStep,
        retry_count: int = 0,
        metadata_json: dict | None = None,
        retry_flag: bool = False,
        retry_comment: str | None = None,
        retry_source_event_id: UUID | None = None,
    ) -> CustomStoryWorkflowEvent:
        event = CustomStoryWorkflowEvent(
            workflow_id=workflow_id,
            step_name=step_name,
            status=CustomStoryWorkflowEventStatus.PENDING,
            retry_count=retry_count,
            retry_flag=retry_flag,
            retry_comment=retry_comment,
            retry_source_event_id=retry_source_event_id,
            metadata_json=metadata_json,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def create_if_absent(
        self,
        *,
        workflow_id: UUID,
        step_name: CustomStoryWorkflowStep,
        retry_count: int = 0,
        metadata_json: dict | None = None,
        retry_flag: bool = False,
        retry_comment: str | None = None,
        retry_source_event_id: UUID | None = None,
    ) -> CustomStoryWorkflowEvent | None:
        result = await self.session.execute(
            select(CustomStoryWorkflowEvent).where(
                CustomStoryWorkflowEvent.workflow_id == workflow_id,
                CustomStoryWorkflowEvent.step_name == step_name,
                CustomStoryWorkflowEvent.status.in_(
                    [
                        CustomStoryWorkflowEventStatus.PENDING,
                        CustomStoryWorkflowEventStatus.PROCESSING,
                        CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
                    ]
                ),
            )
        )
        requested_language = metadata_json.get("language") if isinstance(metadata_json, dict) else None
        for existing in result.scalars().all():
            if requested_language:
                existing_metadata = existing.metadata_json if isinstance(existing.metadata_json, dict) else {}
                if existing_metadata.get("language") == requested_language:
                    return None
                continue
            return None
        return await self.create(
            workflow_id=workflow_id,
            step_name=step_name,
            retry_count=retry_count,
            metadata_json=metadata_json,
            retry_flag=retry_flag,
            retry_comment=retry_comment,
            retry_source_event_id=retry_source_event_id,
        )

    async def latest_for_workflow_step_status(
        self,
        *,
        workflow_id: UUID,
        step_name: CustomStoryWorkflowStep,
        status: CustomStoryWorkflowEventStatus,
    ) -> CustomStoryWorkflowEvent | None:
        result = await self.session.execute(
            select(CustomStoryWorkflowEvent)
            .where(
                CustomStoryWorkflowEvent.workflow_id == workflow_id,
                CustomStoryWorkflowEvent.step_name == step_name,
                CustomStoryWorkflowEvent.status == status,
            )
            .order_by(CustomStoryWorkflowEvent.created_at.desc(), CustomStoryWorkflowEvent.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def batch_submitted_for_job(
        self,
        *,
        workflow_id: UUID,
        step_name: CustomStoryWorkflowStep,
        batch_job_id: UUID,
    ) -> CustomStoryWorkflowEvent | None:
        result = await self.session.execute(
            select(CustomStoryWorkflowEvent)
            .where(
                CustomStoryWorkflowEvent.workflow_id == workflow_id,
                CustomStoryWorkflowEvent.step_name == step_name,
                CustomStoryWorkflowEvent.status == CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
            )
            .order_by(CustomStoryWorkflowEvent.created_at.desc(), CustomStoryWorkflowEvent.id.desc())
        )
        batch_job_id_text = str(batch_job_id)
        for event in result.scalars().all():
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            if str(metadata.get("batch_job_id") or "") == batch_job_id_text:
                return event
        return None

    async def list_by_workflow_desc(self, workflow_id: UUID) -> list[CustomStoryWorkflowEvent]:
        result = await self.session.execute(
            select(CustomStoryWorkflowEvent)
            .where(CustomStoryWorkflowEvent.workflow_id == workflow_id)
            .order_by(CustomStoryWorkflowEvent.created_at.desc(), CustomStoryWorkflowEvent.id.desc())
        )
        return list(result.scalars().all())

    async def claim_pending(self, limit: int) -> list[CustomStoryWorkflowEvent]:
        stale_before = datetime.utcnow() - timedelta(minutes=5)
        result = await self.session.execute(
            select(CustomStoryWorkflowEvent)
            .where(
                (
                    CustomStoryWorkflowEvent.status == CustomStoryWorkflowEventStatus.PENDING
                )
                | (
                    (CustomStoryWorkflowEvent.status == CustomStoryWorkflowEventStatus.PROCESSING)
                    & (CustomStoryWorkflowEvent.locked_at < stale_before)
                )
            )
            .order_by(CustomStoryWorkflowEvent.created_at.asc(), CustomStoryWorkflowEvent.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars().all())
        for event in events:
            event.status = CustomStoryWorkflowEventStatus.PROCESSING
            event.locked_at = datetime.utcnow()
        await self.session.flush()
        return events

    async def update(self, event: CustomStoryWorkflowEvent) -> CustomStoryWorkflowEvent:
        if event.metadata_json is not None:
            flag_modified(event, "metadata_json")
        await self.session.flush()
        return event


class CustomStoryInputSafetyAuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        child_id: UUID | None,
        provider: str,
        model: str | None,
        request_json: dict,
        request_idea_json: dict,
        prompt: str,
        status: CustomStoryInputSafetyAuditStatus = CustomStoryInputSafetyAuditStatus.IN_PROGRESS,
        workflow_id: UUID | None = None,
        response_text: str | None = None,
        response_json: dict | None = None,
        safe: bool | None = None,
        risk_level: str | None = None,
        blocked_categories: list[str] | None = None,
        reason: str | None = None,
        safe_rewrite: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> CustomStoryInputSafetyAudit:
        audit = CustomStoryInputSafetyAudit(
            user_id=user_id,
            child_id=child_id,
            workflow_id=workflow_id,
            status=status,
            provider=provider,
            model=model,
            request_json=request_json,
            request_idea_json=request_idea_json,
            prompt=prompt,
            response_text=response_text,
            response_json=response_json,
            safe=safe,
            risk_level=risk_level,
            blocked_categories=blocked_categories,
            reason=reason,
            safe_rewrite=safe_rewrite,
            error_code=error_code,
            error_message=error_message,
        )
        self.session.add(audit)
        await self.session.flush()
        return audit

    async def update(self, audit: CustomStoryInputSafetyAudit) -> CustomStoryInputSafetyAudit:
        for field_name in (
            "request_json",
            "request_idea_json",
            "response_json",
            "response_text",
            "blocked_categories",
        ):
            if getattr(audit, field_name, None) is not None:
                flag_modified(audit, field_name)
        await self.session.flush()
        return audit


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
        generic_story_id: UUID | None = None,
    ) -> CustomStoryBatchJob:
        job = CustomStoryBatchJob(
            workflow_id=workflow_id,
            story_id=story_id,
            generic_story_id=generic_story_id,
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

    async def get_by_id(self, batch_job_id: UUID) -> CustomStoryBatchJob | None:
        result = await self.session.execute(
            select(CustomStoryBatchJob).where(CustomStoryBatchJob.id == batch_job_id)
        )
        return result.scalar_one_or_none()

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

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        workflow_id: UUID | None = None,
        status: StoryBatchJobStatus | None = None,
        story_type: CustomStoryWorkflowType | str | None = None,
        generic_story_id: UUID | None = None,
        job_type: StoryBatchJobType | None = None,
        provider: str | None = None,
    ) -> tuple[list[CustomStoryBatchJob], int]:
        """List batch jobs for user with optional filtering by workflow_id and status."""
        filters = [CustomStoryWorkflow.user_id == user_id]

        if workflow_id is not None:
            filters.append(CustomStoryBatchJob.workflow_id == workflow_id)

        if story_type is not None:
            filters.append(CustomStoryWorkflow.story_type == story_type)

        if generic_story_id is not None:
            filters.append(CustomStoryBatchJob.generic_story_id == generic_story_id)

        if status is not None:
            filters.append(CustomStoryBatchJob.status == status)

        if job_type is not None:
            filters.append(CustomStoryBatchJob.job_type == job_type)

        if provider is not None:
            filters.append(CustomStoryBatchJob.provider == provider)

        total = await self.session.scalar(
            select(func.count(CustomStoryBatchJob.id))
            .join(CustomStoryWorkflow, CustomStoryBatchJob.workflow_id == CustomStoryWorkflow.id)
            .where(*filters)
        )

        id_result = await self.session.execute(
            select(CustomStoryBatchJob.id)
            .join(CustomStoryWorkflow, CustomStoryBatchJob.workflow_id == CustomStoryWorkflow.id)
            .where(*filters)
            .order_by(CustomStoryBatchJob.created_at.desc(), CustomStoryBatchJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        job_ids = list(id_result.scalars().all())
        if not job_ids:
            return [], int(total or 0)

        result = await self.session.execute(
            select(CustomStoryBatchJob)
            .options(
                load_only(
                    CustomStoryBatchJob.id,
                    CustomStoryBatchJob.workflow_id,
                    CustomStoryBatchJob.story_id,
                    CustomStoryBatchJob.generic_story_id,
                    CustomStoryBatchJob.job_type,
                    CustomStoryBatchJob.status,
                    CustomStoryBatchJob.provider,
                    CustomStoryBatchJob.provider_job_name,
                    CustomStoryBatchJob.provider_model,
                    CustomStoryBatchJob.provider_state,
                    CustomStoryBatchJob.attempt,
                    CustomStoryBatchJob.expected_item_count,
                    CustomStoryBatchJob.completed_item_count,
                    CustomStoryBatchJob.failed_item_count,
                    CustomStoryBatchJob.request_keys,
                    CustomStoryBatchJob.missing_keys,
                    CustomStoryBatchJob.error_message,
                    CustomStoryBatchJob.created_at,
                    CustomStoryBatchJob.updated_at,
                )
            )
            .where(CustomStoryBatchJob.id.in_(job_ids))
        )
        jobs_by_id = {str(job.id): job for job in result.scalars().all()}
        return [jobs_by_id[str(job_id)] for job_id in job_ids if str(job_id) in jobs_by_id], int(total or 0)

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
