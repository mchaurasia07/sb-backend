from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.entity.story_batch_job import StoryBatchJob, StoryBatchJobStatus, StoryBatchJobType


class StoryBatchJobRepository:
    """Persistence operations for delayed story batch jobs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        story_id: UUID,
        job_type: StoryBatchJobType,
        attempt: int,
        expected_item_count: int,
        request_keys: list[str],
        provider_model: str | None,
        request_payload: dict | None = None,
    ) -> StoryBatchJob:
        job = StoryBatchJob(
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

    async def latest_for_story_type(
        self,
        story_id: UUID,
        job_type: StoryBatchJobType,
    ) -> StoryBatchJob | None:
        result = await self.session.execute(
            select(StoryBatchJob)
            .where(StoryBatchJob.story_id == story_id, StoryBatchJob.job_type == job_type)
            .order_by(StoryBatchJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_for_story(self, story_id: UUID, batch_job_id: UUID) -> StoryBatchJob | None:
        result = await self.session.execute(
            select(StoryBatchJob).where(
                StoryBatchJob.id == batch_job_id,
                StoryBatchJob.story_id == story_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_reconcilable(self, limit: int = 50) -> list[StoryBatchJob]:
        result = await self.session.execute(
            select(StoryBatchJob)
            .where(
                StoryBatchJob.status.in_(
                    [
                        StoryBatchJobStatus.SUBMITTED,
                        StoryBatchJobStatus.RUNNING,
                    ]
                ),
                StoryBatchJob.provider_job_name.is_not(None),
            )
            .order_by(StoryBatchJob.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update(self, job: StoryBatchJob) -> StoryBatchJob:
        for field_name in ("request_keys", "missing_keys", "request_payload", "response_payload"):
            if getattr(job, field_name, None) is not None:
                flag_modified(job, field_name)
        await self.session.flush()
        return job
