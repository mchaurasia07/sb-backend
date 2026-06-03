from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.entity.generic_story_workflow import GenericStoryWorkflow


class GenericStoryWorkflowRepository:
    """Persistence operations for generic story workflow state."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data) -> GenericStoryWorkflow:
        workflow = GenericStoryWorkflow(**data)
        self.session.add(workflow)
        await self.session.flush()
        return workflow

    async def get_by_id(self, workflow_id: UUID) -> GenericStoryWorkflow | None:
        result = await self.session.execute(
            select(GenericStoryWorkflow).where(GenericStoryWorkflow.id == workflow_id)
        )
        return result.scalar_one_or_none()

    async def get_for_user(self, user_id: UUID, workflow_id: UUID) -> GenericStoryWorkflow | None:
        result = await self.session.execute(
            select(GenericStoryWorkflow).where(
                GenericStoryWorkflow.id == workflow_id,
                GenericStoryWorkflow.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def latest_for_user_generic_story(
        self,
        user_id: UUID,
        generic_story_id: UUID,
    ) -> GenericStoryWorkflow | None:
        result = await self.session.execute(
            select(GenericStoryWorkflow)
            .where(
                GenericStoryWorkflow.user_id == user_id,
                GenericStoryWorkflow.generic_story_id == generic_story_id,
            )
            .order_by(GenericStoryWorkflow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[GenericStoryWorkflow], int]:
        filters = [GenericStoryWorkflow.user_id == user_id]
        total = await self.session.scalar(
            select(func.count()).select_from(GenericStoryWorkflow).where(*filters)
        )
        result = await self.session.execute(
            select(GenericStoryWorkflow)
            .where(*filters)
            .order_by(GenericStoryWorkflow.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    async def update(self, workflow: GenericStoryWorkflow) -> GenericStoryWorkflow:
        for field in (
            "character_analysis_json",
            "scene_plan_json",
            "story_json",
            "image_plan_json",
            "input_request",
        ):
            if getattr(workflow, field, None) is not None:
                flag_modified(workflow, field)
        await self.session.flush()
        return workflow
