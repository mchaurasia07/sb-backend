from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app.entity.custom_story_workflow import (
    CustomStoryBatchJobEntity,
    CustomStoryWorkflowEntity,
    CustomStoryWorkflowEventEntity,
    CustomStoryWorkflowEventStatus,
    CustomStoryWorkflowStep,
    CustomStoryWorkflowStepRecord,
    CustomStoryWorkflowType,
)
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StepStatus
from app.repository.base_repository import BaseRepository


class WorkflowRepository(BaseRepository[CustomStoryWorkflowEntity]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CustomStoryWorkflowEntity)

    @staticmethod
    def _list_load_columns():
        return (
            CustomStoryWorkflowEntity.id,
            CustomStoryWorkflowEntity.user_id,
            CustomStoryWorkflowEntity.child_id,
            CustomStoryWorkflowEntity.story_id,
            CustomStoryWorkflowEntity.request_number,
            CustomStoryWorkflowEntity.story_type,
            CustomStoryWorkflowEntity.age_group,
            CustomStoryWorkflowEntity.category,
            CustomStoryWorkflowEntity.learning_goal,
            CustomStoryWorkflowEntity.context,
            CustomStoryWorkflowEntity.languages,
            CustomStoryWorkflowEntity.reader_category,
            CustomStoryWorkflowEntity.use_child_character,
            CustomStoryWorkflowEntity.execute_image,
            CustomStoryWorkflowEntity.execute_narration,
            CustomStoryWorkflowEntity.skip_validation,
            CustomStoryWorkflowEntity.execute_workflow,
            CustomStoryWorkflowEntity.status,
            CustomStoryWorkflowEntity.current_step,
            CustomStoryWorkflowEntity.error_message,
            CustomStoryWorkflowEntity.title,
            CustomStoryWorkflowEntity.summary,
            CustomStoryWorkflowEntity.moral,
            CustomStoryWorkflowEntity.ai_provider,
            CustomStoryWorkflowEntity.text_model,
            CustomStoryWorkflowEntity.created_at,
            CustomStoryWorkflowEntity.updated_at,
        )

    async def create(self, **kwargs) -> CustomStoryWorkflowEntity:
        if kwargs.get("request_number") is None:
            kwargs["request_number"] = await self.next_request_number()
        return await super().create(**kwargs)

    async def next_request_number(self) -> int:
        latest = await self.session.scalar(select(func.max(CustomStoryWorkflowEntity.request_number)))
        return int(latest or 0) + 1

    async def get_by_workflow_id(self, _user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflowEntity | None:
        return await self.get_one(
            filters=(
                CustomStoryWorkflowEntity.id == workflow_id,
            )
        )

    async def get_for_user_for_update(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflowEntity | None:
        return await self.get_one(
            filters=(
                CustomStoryWorkflowEntity.id == workflow_id,
                CustomStoryWorkflowEntity.user_id == user_id,
            ),
            for_update=True,
        )

    async def get_by_id_for_update(self, workflow_id: UUID) -> CustomStoryWorkflowEntity | None:
        return await self.get_by_id(workflow_id, for_update=True)

    async def list_workflows(
        self,
        user_id: UUID | None = None,
        *,
        page: int,
        page_size: int,
        workflow_id: UUID | None = None,
        workflow_type: CustomStoryWorkflowType | str | None = None,
        status_filter: str | None = None,
    ) -> tuple[list[CustomStoryWorkflowEntity], int]:
        filters = []
        if user_id is not None:
            filters.append(CustomStoryWorkflowEntity.user_id == user_id)
        if workflow_id is not None:
            filters.append(CustomStoryWorkflowEntity.id == workflow_id)
        if workflow_type is not None:
            filters.append(CustomStoryWorkflowEntity.story_type == workflow_type)
        if status_filter:
            filters.append(CustomStoryWorkflowEntity.status == status_filter)

        return await self.list_paginated(
            filters=filters,
            page=page,
            page_size=page_size,
            order_by=(CustomStoryWorkflowEntity.created_at.desc(),),
            load_columns=self._list_load_columns(),
        )

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
    ) -> tuple[list[CustomStoryWorkflowEntity], int]:
        filters = [CustomStoryWorkflowEntity.user_id == user_id]
        if story_type is not None:
            filters.append(CustomStoryWorkflowEntity.story_type == story_type)
        if child_id is not None:
            filters.append(CustomStoryWorkflowEntity.child_id == child_id)
        if status_filter:
            filters.append(CustomStoryWorkflowEntity.status == status_filter)
        if title:
            normalized_title = title.strip().lower()
            if normalized_title:
                filters.append(func.lower(CustomStoryWorkflowEntity.title).like(f"%{normalized_title}%"))

        return await self.list_paginated(
            filters=filters,
            page=page,
            page_size=page_size,
            order_by=(CustomStoryWorkflowEntity.created_at.desc(),),
            load_columns=self._list_load_columns(),
        )

    async def update(self, workflow: CustomStoryWorkflowEntity) -> CustomStoryWorkflowEntity:
        return await super().update(
            workflow,
            flag_modified_fields=("story_plan_json", "story_json", "image_plan_json", "languages"),
        )


class WorkflowStepRepository(BaseRepository[CustomStoryWorkflowStepRecord]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CustomStoryWorkflowStepRecord)

    async def create(self, workflow_id: UUID, step_name: str, retry_count: int = 0) -> CustomStoryWorkflowStepRecord:
        value = step_name.value if hasattr(step_name, "value") else str(step_name)
        return await super().create(
            workflow_id=workflow_id,
            step_name=CustomStoryWorkflowStep(value),
            status=StepStatus.PENDING,
            retry_count=retry_count,
        )

    async def latest_for_story_step(self, story_id: UUID, step_name) -> CustomStoryWorkflowStepRecord | None:
        value = step_name.value if hasattr(step_name, "value") else str(step_name)
        steps = await self.list(
            filters=(
                CustomStoryWorkflowStepRecord.workflow_id == story_id,
                CustomStoryWorkflowStepRecord.step_name == CustomStoryWorkflowStep(value),
            ),
            order_by=(CustomStoryWorkflowStepRecord.created_at.desc(),),
        )
        return steps[0] if steps else None

    async def latest_for_workflow_step(
        self, workflow_id: UUID, step_name: CustomStoryWorkflowStep
    ) -> CustomStoryWorkflowStepRecord | None:
        return await self.latest_for_story_step(workflow_id, step_name)

    async def list_by_workflow(self, workflow_id: UUID) -> list[CustomStoryWorkflowStepRecord]:
        return await self.list(
            filters=(CustomStoryWorkflowStepRecord.workflow_id == workflow_id,),
            order_by=(CustomStoryWorkflowStepRecord.created_at.asc(),),
        )

    async def update(self, step: CustomStoryWorkflowStepRecord) -> CustomStoryWorkflowStepRecord:
        return await super().update(step, flag_modified_fields=("input_json", "output_json"))


class WorkflowEventRepository(BaseRepository[CustomStoryWorkflowEventEntity]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CustomStoryWorkflowEventEntity)

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
    ) -> CustomStoryWorkflowEventEntity:
        return await super().create(
            workflow_id=workflow_id,
            story_type=await self._story_type_for_workflow(workflow_id),
            step_name=step_name,
            status=CustomStoryWorkflowEventStatus.PENDING,
            retry_count=retry_count,
            retry_flag=retry_flag,
            retry_comment=retry_comment,
            retry_source_event_id=retry_source_event_id,
            metadata_json=metadata_json,
        )

    async def _story_type_for_workflow(self, workflow_id: UUID) -> CustomStoryWorkflowType:
        story_type = await self.session.scalar(
            select(CustomStoryWorkflowEntity.story_type).where(CustomStoryWorkflowEntity.id == workflow_id)
        )
        if isinstance(story_type, CustomStoryWorkflowType):
            return story_type
        if isinstance(story_type, str):
            try:
                return CustomStoryWorkflowType(story_type)
            except ValueError:
                return CustomStoryWorkflowType.CUSTOM
        return CustomStoryWorkflowType.CUSTOM

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
    ) -> CustomStoryWorkflowEventEntity | None:
        existing_events = await self.list(
            filters=(
                CustomStoryWorkflowEventEntity.workflow_id == workflow_id,
                CustomStoryWorkflowEventEntity.step_name == step_name,
                CustomStoryWorkflowEventEntity.status.in_(
                    [
                        CustomStoryWorkflowEventStatus.PENDING,
                        CustomStoryWorkflowEventStatus.PROCESSING,
                        CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
                    ]
                ),
            )
        )
        requested_language = metadata_json.get("language") if isinstance(metadata_json, dict) else None
        for existing in existing_events:
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
    ) -> CustomStoryWorkflowEventEntity | None:
        events = await self.list(
            filters=(
                CustomStoryWorkflowEventEntity.workflow_id == workflow_id,
                CustomStoryWorkflowEventEntity.step_name == step_name,
                CustomStoryWorkflowEventEntity.status == status,
            ),
            order_by=(CustomStoryWorkflowEventEntity.created_at.desc(), CustomStoryWorkflowEventEntity.id.desc()),
        )
        return events[0] if events else None

    async def batch_submitted_for_job(
        self,
        *,
        workflow_id: UUID,
        step_name: CustomStoryWorkflowStep,
        batch_job_id: UUID,
    ) -> CustomStoryWorkflowEventEntity | None:
        events = await self.list(
            filters=(
                CustomStoryWorkflowEventEntity.workflow_id == workflow_id,
                CustomStoryWorkflowEventEntity.step_name == step_name,
                CustomStoryWorkflowEventEntity.status == CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
            ),
            order_by=(CustomStoryWorkflowEventEntity.created_at.desc(), CustomStoryWorkflowEventEntity.id.desc()),
        )
        batch_job_id_text = str(batch_job_id)
        for event in events:
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            if str(metadata.get("batch_job_id") or "") == batch_job_id_text:
                return event
        return None

    async def list_by_workflow_desc(
        self,
        workflow_id: UUID,
        *,
        story_type: CustomStoryWorkflowType | str | None = None,
    ) -> list[CustomStoryWorkflowEventEntity]:
        filters = [CustomStoryWorkflowEventEntity.workflow_id == workflow_id]
        if story_type is not None:
            filters.append(CustomStoryWorkflowEventEntity.story_type == story_type)
        return await self.list(
            filters=filters,
            order_by=(CustomStoryWorkflowEventEntity.created_at.desc(), CustomStoryWorkflowEventEntity.id.desc()),
        )

    async def claim_pending(self, limit: int) -> list[CustomStoryWorkflowEventEntity]:
        stale_before = datetime.utcnow() - timedelta(minutes=5)
        result = await self.session.execute(
            select(CustomStoryWorkflowEventEntity)
            .where(
                (CustomStoryWorkflowEventEntity.status == CustomStoryWorkflowEventStatus.PENDING)
                | (
                    (CustomStoryWorkflowEventEntity.status == CustomStoryWorkflowEventStatus.PROCESSING)
                    & (CustomStoryWorkflowEventEntity.locked_at < stale_before)
                )
            )
            .order_by(CustomStoryWorkflowEventEntity.created_at.asc(), CustomStoryWorkflowEventEntity.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars().all())
        for event in events:
            event.status = CustomStoryWorkflowEventStatus.PROCESSING
            event.locked_at = datetime.utcnow()
        await self.session.flush()
        return events

    async def update(self, event: CustomStoryWorkflowEventEntity) -> CustomStoryWorkflowEventEntity:
        return await super().update(event, flag_modified_fields=("metadata_json",))


class WorkflowBatchJobRepository(BaseRepository[CustomStoryBatchJobEntity]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CustomStoryBatchJobEntity)

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
    ) -> CustomStoryBatchJobEntity:
        return await super().create(
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

    async def latest_for_workflow_type(
        self, workflow_id: UUID, job_type: StoryBatchJobType
    ) -> CustomStoryBatchJobEntity | None:
        jobs = await self.list(
            filters=(
                CustomStoryBatchJobEntity.workflow_id == workflow_id,
                CustomStoryBatchJobEntity.job_type == job_type,
            ),
            order_by=(CustomStoryBatchJobEntity.created_at.desc(),),
        )
        return jobs[0] if jobs else None

    async def list_active_for_workflow(self, workflow_id: UUID) -> list[CustomStoryBatchJobEntity]:
        return await self.list(
            filters=(
                CustomStoryBatchJobEntity.workflow_id == workflow_id,
                CustomStoryBatchJobEntity.status.in_([StoryBatchJobStatus.SUBMITTED, StoryBatchJobStatus.RUNNING]),
            )
        )

    async def list_by_workflow(self, workflow_id: UUID) -> list[CustomStoryBatchJobEntity]:
        return await self.list(
            filters=(CustomStoryBatchJobEntity.workflow_id == workflow_id,),
            order_by=(CustomStoryBatchJobEntity.created_at.asc(),),
        )

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        workflow_id: UUID | None = None,
        status: StoryBatchJobStatus | None = None,
        story_type: CustomStoryWorkflowType | str | None = None,
        job_type: StoryBatchJobType | None = None,
        provider: str | None = None,
    ) -> tuple[list[CustomStoryBatchJobEntity], int]:
        filters = [CustomStoryWorkflowEntity.user_id == user_id]
        if workflow_id is not None:
            filters.append(CustomStoryBatchJobEntity.workflow_id == workflow_id)
        if story_type is not None:
            filters.append(CustomStoryWorkflowEntity.story_type == story_type)
        if status is not None:
            filters.append(CustomStoryBatchJobEntity.status == status)
        if job_type is not None:
            filters.append(CustomStoryBatchJobEntity.job_type == job_type)
        if provider is not None:
            filters.append(CustomStoryBatchJobEntity.provider == provider)

        total = await self.session.scalar(
            select(func.count(CustomStoryBatchJobEntity.id))
            .join(CustomStoryWorkflowEntity, CustomStoryBatchJobEntity.workflow_id == CustomStoryWorkflowEntity.id)
            .where(*filters)
        )
        id_result = await self.session.execute(
            select(CustomStoryBatchJobEntity.id)
            .join(CustomStoryWorkflowEntity, CustomStoryBatchJobEntity.workflow_id == CustomStoryWorkflowEntity.id)
            .where(*filters)
            .order_by(CustomStoryBatchJobEntity.created_at.desc(), CustomStoryBatchJobEntity.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        job_ids = list(id_result.scalars().all())
        if not job_ids:
            return [], int(total or 0)

        result = await self.session.execute(
            select(CustomStoryBatchJobEntity)
            .options(load_only(*self._list_load_columns()))
            .where(CustomStoryBatchJobEntity.id.in_(job_ids))
        )
        jobs_by_id = {str(job.id): job for job in result.scalars().all()}
        return [jobs_by_id[str(job_id)] for job_id in job_ids if str(job_id) in jobs_by_id], int(total or 0)

    @staticmethod
    def _list_load_columns():
        return (
            CustomStoryBatchJobEntity.id,
            CustomStoryBatchJobEntity.workflow_id,
            CustomStoryBatchJobEntity.story_id,
            CustomStoryBatchJobEntity.job_type,
            CustomStoryBatchJobEntity.status,
            CustomStoryBatchJobEntity.provider,
            CustomStoryBatchJobEntity.provider_job_name,
            CustomStoryBatchJobEntity.provider_model,
            CustomStoryBatchJobEntity.provider_state,
            CustomStoryBatchJobEntity.attempt,
            CustomStoryBatchJobEntity.expected_item_count,
            CustomStoryBatchJobEntity.completed_item_count,
            CustomStoryBatchJobEntity.failed_item_count,
            CustomStoryBatchJobEntity.request_keys,
            CustomStoryBatchJobEntity.missing_keys,
            CustomStoryBatchJobEntity.error_message,
            CustomStoryBatchJobEntity.created_at,
            CustomStoryBatchJobEntity.updated_at,
        )

    async def list_reconcilable(self, limit: int = 50) -> list[CustomStoryBatchJobEntity]:
        id_result = await self.session.execute(
            select(CustomStoryBatchJobEntity.id)
            .where(
                CustomStoryBatchJobEntity.status.in_([StoryBatchJobStatus.SUBMITTED, StoryBatchJobStatus.RUNNING]),
                CustomStoryBatchJobEntity.provider_job_name.is_not(None),
            )
            .order_by(CustomStoryBatchJobEntity.updated_at.asc())
            .limit(limit)
        )
        job_ids = list(id_result.scalars().all())
        if not job_ids:
            return []

        result = await self.session.execute(
            select(CustomStoryBatchJobEntity).where(CustomStoryBatchJobEntity.id.in_(job_ids))
        )
        jobs_by_id = {str(job.id): job for job in result.scalars().all()}
        return [jobs_by_id[str(job_id)] for job_id in job_ids if str(job_id) in jobs_by_id]

    async def update(self, job: CustomStoryBatchJobEntity) -> CustomStoryBatchJobEntity:
        return await super().update(
            job,
            flag_modified_fields=("request_keys", "missing_keys", "request_payload", "response_payload"),
        )


# Backwards-compatible names while callers are migrated.
CustomStoryWorkflowEventRepository = WorkflowEventRepository
CustomStoryBatchJobRepository = WorkflowBatchJobRepository
