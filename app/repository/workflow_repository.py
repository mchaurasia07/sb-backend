from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.custom_story_workflow import CustomStoryBatchJobEntity, CustomStoryWorkflowEntity, CustomStoryWorkflowEventEntity, CustomStoryWorkflowType
from app.repository.base_repository import BaseRepository

class CustomStoryBatchJobRepository(BaseRepository[CustomStoryBatchJobEntity]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CustomStoryBatchJobEntity)

    async def list_batch_jobs_by_Workflow_id(self, workflow_id: UUID) -> list[CustomStoryBatchJobEntity]:
        filters = [CustomStoryBatchJobEntity.workflow_id == workflow_id]
        return await self.list(filters=filters, order_by=(CustomStoryBatchJobEntity.created_at.desc(),))

class CustomStoryWorkflowEventRepository(BaseRepository[CustomStoryWorkflowEventEntity]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CustomStoryWorkflowEventEntity)

    async def list_by_workflow_desc(
        self,
        workflow_id: UUID,
        story_type: CustomStoryWorkflowType | str | None = None,
    ) -> list[CustomStoryWorkflowEventEntity]:
        filters = [CustomStoryWorkflowEventEntity.workflow_id == workflow_id]
        if story_type is not None:
            filters.append(CustomStoryWorkflowEventEntity.story_type == story_type)

        return await self.list(
            filters=filters,
            order_by=(CustomStoryWorkflowEventEntity.created_at.desc(),),
        )

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
            CustomStoryWorkflowEntity.generic_story_id,
            CustomStoryWorkflowEntity.request_number,
            CustomStoryWorkflowEntity.story_type,
            CustomStoryWorkflowEntity.age_group,
            CustomStoryWorkflowEntity.category,
            CustomStoryWorkflowEntity.learning_goal,
            CustomStoryWorkflowEntity.context,
            CustomStoryWorkflowEntity.languages,
            CustomStoryWorkflowEntity.publish_status,
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

    async def list_workflows(
        self,
        page: int,
        page_size: int,
        workflow_id: UUID | None = None,
        workflow_type: CustomStoryWorkflowType | str | None = None,
    ) -> tuple[list[CustomStoryWorkflowEntity], int]:
        filters = []
        if workflow_id is not None:
            filters.append(CustomStoryWorkflowEntity.id == workflow_id)
        if workflow_type is not None:
            filters.append(CustomStoryWorkflowEntity.story_type == workflow_type)

        return await self.list_paginated(
            filters=filters,
            page=page,
            page_size=page_size,
            order_by=(CustomStoryWorkflowEntity.created_at.desc(),),
            load_columns=self._list_load_columns(),
        )
