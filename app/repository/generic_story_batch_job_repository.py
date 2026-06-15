from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app.entity.generic_story_batch_job import GenericStoryBatchJob
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType


class GenericStoryBatchJobRepository:
    """Persistence operations for generic story batch jobs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        generic_story_id: UUID | None,
        workflow_id: UUID,
        job_type: StoryBatchJobType,
        attempt: int,
        expected_item_count: int,
        request_keys: list[str],
        provider_model: str | None,
        provider: str = "google",
        request_payload: dict | None = None,
    ) -> GenericStoryBatchJob:
        job = GenericStoryBatchJob(
            generic_story_id=generic_story_id,
            workflow_id=workflow_id,
            job_type=job_type,
            status=StoryBatchJobStatus.SUBMITTED,
            provider=provider,
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

    async def latest_for_story_type(
        self,
        generic_story_id: UUID,
        job_type: StoryBatchJobType,
        provider: str | None = None,
    ) -> GenericStoryBatchJob | None:
        filters = [
            GenericStoryBatchJob.generic_story_id == generic_story_id,
            GenericStoryBatchJob.job_type == job_type,
        ]
        if provider:
            filters.append(GenericStoryBatchJob.provider == provider)

        result = await self.session.execute(
            select(GenericStoryBatchJob)
            .where(*filters)
            .order_by(GenericStoryBatchJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_for_story(self, generic_story_id: UUID, batch_job_id: UUID) -> GenericStoryBatchJob | None:
        result = await self.session.execute(
            select(GenericStoryBatchJob).where(
                GenericStoryBatchJob.id == batch_job_id,
                GenericStoryBatchJob.generic_story_id == generic_story_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        generic_story_id: UUID | None = None,
        workflow_id: UUID | None = None,
        status: StoryBatchJobStatus | None = None,
        job_type: StoryBatchJobType | None = None,
        provider: str | None = None,
    ) -> tuple[list[GenericStoryBatchJob], int]:
        filters = []
        if generic_story_id is not None:
            filters.append(GenericStoryBatchJob.generic_story_id == generic_story_id)
        if workflow_id is not None:
            filters.append(GenericStoryBatchJob.workflow_id == workflow_id)
        if status is not None:
            filters.append(GenericStoryBatchJob.status == status)
        if job_type is not None:
            filters.append(GenericStoryBatchJob.job_type == job_type)
        if provider is not None:
            filters.append(GenericStoryBatchJob.provider == provider)

        total = await self.session.scalar(
            select(func.count()).select_from(GenericStoryBatchJob).where(*filters)
        )
        result = await self.session.execute(
            select(GenericStoryBatchJob)
            .options(
                load_only(
                    GenericStoryBatchJob.id,
                    GenericStoryBatchJob.generic_story_id,
                    GenericStoryBatchJob.workflow_id,
                    GenericStoryBatchJob.job_type,
                    GenericStoryBatchJob.status,
                    GenericStoryBatchJob.provider,
                    GenericStoryBatchJob.provider_job_name,
                    GenericStoryBatchJob.provider_model,
                    GenericStoryBatchJob.provider_state,
                    GenericStoryBatchJob.attempt,
                    GenericStoryBatchJob.expected_item_count,
                    GenericStoryBatchJob.completed_item_count,
                    GenericStoryBatchJob.failed_item_count,
                    GenericStoryBatchJob.request_keys,
                    GenericStoryBatchJob.missing_keys,
                    GenericStoryBatchJob.error_message,
                    GenericStoryBatchJob.created_at,
                    GenericStoryBatchJob.updated_at,
                )
            )
            .where(*filters)
            .order_by(GenericStoryBatchJob.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def list_reconcilable(self, limit: int = 50) -> list[GenericStoryBatchJob]:
        id_result = await self.session.execute(
            select(GenericStoryBatchJob.id)
            .where(
                GenericStoryBatchJob.status.in_(
                    [
                        StoryBatchJobStatus.SUBMITTED,
                        StoryBatchJobStatus.RUNNING,
                    ]
                ),
                GenericStoryBatchJob.provider_job_name.is_not(None),
            )
            .order_by(GenericStoryBatchJob.updated_at.asc())
            .limit(limit)
        )
        job_ids = list(id_result.scalars().all())
        if not job_ids:
            return []

        result = await self.session.execute(
            select(GenericStoryBatchJob)
            .where(GenericStoryBatchJob.id.in_(job_ids))
            .order_by(GenericStoryBatchJob.updated_at.asc())
        )
        return list(result.scalars().all())

    async def list_active_for_workflow(self, workflow_id: UUID) -> list[GenericStoryBatchJob]:
        result = await self.session.execute(
            select(GenericStoryBatchJob).where(
                GenericStoryBatchJob.workflow_id == workflow_id,
                GenericStoryBatchJob.status.in_(
                    [
                        StoryBatchJobStatus.SUBMITTED,
                        StoryBatchJobStatus.RUNNING,
                    ]
                ),
            )
        )
        return list(result.scalars().all())

    async def update(self, job: GenericStoryBatchJob) -> GenericStoryBatchJob:
        for field_name in ("request_keys", "missing_keys", "request_payload", "response_payload"):
            if getattr(job, field_name, None) is not None:
                flag_modified(job, field_name)
        await self.session.flush()
        return job
