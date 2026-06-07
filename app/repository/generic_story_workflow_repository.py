from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from app.entity.generic_story_workflow import GenericStoryWorkflow


class GenericStoryWorkflowRepository:
    """Persistence operations for generic story workflow state."""

    LATEST_WORKFLOW_INDEX = "ix_generic_story_workflows_user_story_created_at"

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
        workflow_id = await self.session.scalar(
            self._latest_for_user_generic_story_id_statement(user_id, generic_story_id)
        )
        if workflow_id is None:
            return None
        return await self.get_by_id(workflow_id)

    @classmethod
    def _latest_for_user_generic_story_id_statement(cls, user_id: UUID, generic_story_id: UUID):
        return (
            select(GenericStoryWorkflow.id)
            .with_hint(
                GenericStoryWorkflow,
                f"FORCE INDEX ({cls.LATEST_WORKFLOW_INDEX})",
                "mysql",
            )
            .where(
                GenericStoryWorkflow.user_id == user_id,
                GenericStoryWorkflow.generic_story_id == generic_story_id,
            )
            .order_by(GenericStoryWorkflow.created_at.desc())
            .limit(1)
        )

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
            self._list_for_user_statement(user_id, page=page, page_size=page_size)
        )
        return list(result.scalars().all()), int(total or 0)

    @staticmethod
    def _list_for_user_statement(user_id: UUID, *, page: int, page_size: int):
        return (
            select(GenericStoryWorkflow)
            .options(
                load_only(
                    GenericStoryWorkflow.id,
                    GenericStoryWorkflow.workflow_name,
                    GenericStoryWorkflow.status,
                    GenericStoryWorkflow.current_step,
                    GenericStoryWorkflow.error_message,
                    GenericStoryWorkflow.generic_story_id,
                    GenericStoryWorkflow.actual_story,
                    GenericStoryWorkflow.age_group,
                    GenericStoryWorkflow.language,
                    GenericStoryWorkflow.requested_pages,
                    GenericStoryWorkflow.title,
                    GenericStoryWorkflow.summary,
                    GenericStoryWorkflow.theme,
                    GenericStoryWorkflow.genre,
                    GenericStoryWorkflow.moral,
                    GenericStoryWorkflow.learning_goal,
                    GenericStoryWorkflow.cover_image,
                    GenericStoryWorkflow.ai_provider,
                    GenericStoryWorkflow.text_model,
                    GenericStoryWorkflow.image_model,
                    GenericStoryWorkflow.created_at,
                    GenericStoryWorkflow.updated_at,
                )
            )
            .where(GenericStoryWorkflow.user_id == user_id)
            .order_by(GenericStoryWorkflow.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

    async def update(self, workflow: GenericStoryWorkflow) -> GenericStoryWorkflow:
        for field in (
            "character_analysis_json",
            "scene_plan_json",
            "visual_bible_json",
            "story_json",
            "image_plan_json",
            "input_request",
        ):
            if getattr(workflow, field, None) is not None:
                flag_modified(workflow, field)
        await self.session.flush()
        return workflow

    async def delete(self, workflow: GenericStoryWorkflow) -> None:
        await self.session.delete(workflow)
        await self.session.flush()
