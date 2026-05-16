from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.story_step import StoryStep, StoryStepName, StepStatus


class StoryStepRepository:
    """Persistence operations for story workflow steps."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        story_id: UUID,
        step_name: str,
        retry_count: int = 0,
    ) -> StoryStep:
        """Create a new step record for workflow tracking."""
        step = StoryStep(
            story_id=story_id,
            step_name=StoryStepName(step_name),
            status=StepStatus.PENDING,
            retry_count=retry_count,
        )
        self.session.add(step)
        await self.session.flush()
        return step

    async def list_by_story(self, story_id: UUID) -> list[StoryStep]:
        """List all steps for a story in order of creation."""
        result = await self.session.execute(
            select(StoryStep)
            .where(StoryStep.story_id == story_id)
            .order_by(StoryStep.created_at.asc())
        )
        return list(result.scalars().all())

    async def update(self, step: StoryStep) -> StoryStep:
        """Update an existing step."""
        await self.session.flush()
        return step
