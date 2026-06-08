from __future__ import annotations

from datetime import UTC, datetime
from types import MethodType, SimpleNamespace
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException
from app.entity.custom_story_workflow import (
    CustomStoryBatchJob,
    CustomStoryWorkflow,
    CustomStoryWorkflowStatus,
    CustomStoryWorkflowStep,
)
from app.entity.story import StoryStatus
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StoryStepName
from app.entity.story_step import StepStatus
from app.model.request.story import StoryGenerationRequest
from app.model.response.common import PaginatedResponse
from app.model.response.custom_story_workflow import (
    CustomStoryWorkflowResponse,
    CustomStoryWorkflowStepResponse,
)
from app.repository.child_repository import ChildRepository
from app.repository.custom_story_workflow_repository import (
    CustomStoryBatchJobRepository,
    CustomStoryWorkflowRepository,
    CustomStoryWorkflowStepRepository,
)
from app.repository.story_page_repository import StoryPageRepository
from app.repository.story_repository import StoryRepository
from app.service.image_storage_provider import get_image_storage_service
from app.service.story_input_safety_service import StoryInputSafetyService
from app.service.story_service import DEFAULT_STORY_LANGUAGE, StoryGenerationFlags, StoryService
from app.service.story_service_batch_service import StoryServiceBatchService


class _WorkflowBatchJobs:
    """Adapter so StoryServiceBatchService can write workflow-owned batch jobs."""

    def __init__(self, batch_jobs: CustomStoryBatchJobRepository):
        self.batch_jobs = batch_jobs

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
    ) -> CustomStoryBatchJob:
        return await self.batch_jobs.create(
            workflow_id=story_id,
            story_id=None,
            job_type=job_type,
            attempt=attempt,
            expected_item_count=expected_item_count,
            request_keys=request_keys,
            provider_model=provider_model,
            request_payload=request_payload,
        )

    async def latest_for_story_type(self, story_id: UUID, job_type: StoryBatchJobType) -> CustomStoryBatchJob | None:
        return await self.batch_jobs.latest_for_workflow_type(story_id, job_type)

    async def update(self, job: CustomStoryBatchJob) -> CustomStoryBatchJob:
        return await self.batch_jobs.update(job)


class _WorkflowStoryStore:
    def __init__(self, workflows: CustomStoryWorkflowRepository):
        self.workflows = workflows

    async def update(self, workflow: CustomStoryWorkflow) -> CustomStoryWorkflow:
        return await self.workflows.update(workflow)

    async def upsert_content(self, workflow: CustomStoryWorkflow, *, language: str, story_json: dict):
        _ = language
        workflow.story_json = story_json
        await self.workflows.update(workflow)
        return SimpleNamespace(story_json=story_json)

    async def get_content_by_story_and_language(self, *, story_id: UUID, language: str):
        _ = story_id, language
        return None


class _WorkflowPageBuffer:
    def __init__(self, workflow: CustomStoryWorkflow):
        self.workflow = workflow
        self.pages: dict[int, SimpleNamespace] = {}

    async def get_by_story_page(self, story_id: UUID, page_number: int):
        _ = story_id
        return self.pages.get(page_number)

    async def upsert_page(
        self,
        story_id: UUID,
        page_number: int,
        page_type: str,
        text: str,
        image_prompt: str | None = None,
        image_url: str | None = None,
    ):
        _ = story_id
        page = self.pages.get(page_number)
        if page is None:
            page = SimpleNamespace(page_number=page_number)
            self.pages[page_number] = page
        page.page_type = page_type
        page.text = text
        page.image_prompt = image_prompt
        page.image_url = image_url
        self._apply_to_story_json(page_number, image_url)
        return page

    def _apply_to_story_json(self, page_number: int, image_url: str | None) -> None:
        if not image_url:
            return
        story_json = self.workflow.story_json if isinstance(self.workflow.story_json, dict) else {}
        if page_number == 0:
            story_json["cover_image_url"] = image_url
            self.workflow.story_json = story_json
            return
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        if page_number == len(pages) + 1:
            story_json["back_cover_image_url"] = image_url
            self.workflow.story_json = story_json
            return
        for page in pages:
            if isinstance(page, dict) and page.get("page_number") == page_number:
                page["image_url"] = image_url
                break
        self.workflow.story_json = story_json


class CustomStoryWorkflowService:
    ORDERED_STEPS = [
        CustomStoryWorkflowStep.STORY_PLAN_GENERATION,
        CustomStoryWorkflowStep.STORY_PLAN_VALIDATION,
        CustomStoryWorkflowStep.STORY_GENERATION,
        CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION,
        CustomStoryWorkflowStep.IMAGE_GENERATION,
        CustomStoryWorkflowStep.NARRATION_GENERATION,
        CustomStoryWorkflowStep.PUBLISH_STORY,
    ]

    def __init__(self, session: AsyncSession):
        self.session = session
        self.workflows = CustomStoryWorkflowRepository(session)
        self.steps = CustomStoryWorkflowStepRepository(session)
        self.batch_jobs = CustomStoryBatchJobRepository(session)
        self.children = ChildRepository(session)
        self.stories = StoryRepository(session)
        self.story_pages = StoryPageRepository(session)

    async def create(self, user_id: UUID, payload: StoryGenerationRequest) -> CustomStoryWorkflowResponse:
        await StoryInputSafetyService().validate(payload)
        child = await self.children.get_for_user(user_id, payload.child_id)
        if child is None:
            raise NotFoundException("Child profile not found")
        if not child.character_image_url:
            raise AppException(
                "Child must have a generated character image before story generation",
                code="NO_CHARACTER_IMAGE",
            )

        story_service = StoryService(self.session)
        age_group = story_service._get_age_group_from_dob(child.dob)
        workflow = await self.workflows.create(
            user_id=user_id,
            child_id=payload.child_id,
            generation_mode=payload.mode,
            processing_mode=payload.processing_mode,
            age_group=age_group,
            category=payload.category,
            learning_goal=payload.learning_goal,
            context=payload.context,
            event_description=payload.event_description,
            status=CustomStoryWorkflowStatus.PENDING,
            input_request=payload.model_dump(mode="json"),
            **story_service._current_ai_config(),
        )
        await self.session.commit()
        return self._response(workflow)

    async def list(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        child_id: UUID | None = None,
        status_filter: str | None = None,
    ) -> PaginatedResponse[CustomStoryWorkflowResponse]:
        workflows, total = await self.workflows.list_for_user(
            user_id,
            page=page,
            page_size=page_size,
            child_id=child_id,
            status_filter=status_filter,
        )
        return PaginatedResponse[CustomStoryWorkflowResponse].create(
            items=[self._response(workflow) for workflow in workflows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflowResponse:
        return self._response(await self._get_owned(user_id, workflow_id))

    async def delete(self, user_id: UUID, workflow_id: UUID) -> None:
        workflow = await self._get_owned(user_id, workflow_id)
        await self.workflows.delete(workflow)
        await self.session.commit()

    async def get_steps(self, user_id: UUID, workflow_id: UUID) -> list[CustomStoryWorkflowStepResponse]:
        workflow = await self._get_owned(user_id, workflow_id)
        steps = await self.steps.list_by_workflow(workflow.id)
        return [self._step_response(step) for step in steps]

    async def retry(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflowResponse:
        workflow = await self.workflows.get_for_user_for_update(user_id, workflow_id)
        if workflow is None:
            raise NotFoundException("Custom story workflow not found")
        if self._status_value(workflow.status) not in {
            CustomStoryWorkflowStatus.FAILED.value,
            CustomStoryWorkflowStatus.IN_PROGRESS.value,
        }:
            raise AppException(
                "Only failed or in-progress custom story workflows can be retried",
                status.HTTP_400_BAD_REQUEST,
                "CUSTOM_STORY_WORKFLOW_RETRY_STATUS_INVALID",
            )
        workflow.status = CustomStoryWorkflowStatus.PENDING
        workflow.error_message = None
        await self.workflows.update(workflow)
        await self.session.commit()
        return self._response(workflow)

    async def run(self, workflow_id: UUID) -> CustomStoryWorkflow:
        workflow = await self.workflows.get_by_id_for_update(workflow_id)
        if workflow is None:
            raise NotFoundException("Custom story workflow not found")
        if self._status_value(workflow.status) == CustomStoryWorkflowStatus.COMPLETED.value:
            return workflow

        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
        workflow.error_message = None
        await self.workflows.update(workflow)
        await self.session.commit()

        try:
            runner = self._story_runner(workflow)
            await runner._ensure_story_ai_config(workflow)
            start_step = await self._first_incomplete_step(workflow)
            for step in self.ORDERED_STEPS[self.ORDERED_STEPS.index(start_step) :]:
                if await self._step_is_complete(workflow, step):
                    continue
                await self._execute_step(runner, workflow, step)
                await self.workflows.update(workflow)
                await self.session.commit()
                if workflow.processing_mode == "delayed" and step in {
                    CustomStoryWorkflowStep.IMAGE_GENERATION,
                    CustomStoryWorkflowStep.NARRATION_GENERATION,
                } and self._status_value(workflow.status) == CustomStoryWorkflowStatus.IN_PROGRESS.value and not await self._step_is_complete(workflow, step):
                    return workflow

            workflow.status = CustomStoryWorkflowStatus.COMPLETED
            workflow.current_step = None
            await self.workflows.update(workflow)
            await self.session.commit()
            return workflow
        except Exception as exc:
            await self.session.rollback()
            workflow.status = CustomStoryWorkflowStatus.FAILED
            workflow.error_message = str(exc)
            await self.workflows.update(workflow)
            await self.session.commit()
            raise

    async def _execute_step(
        self,
        runner: StoryService,
        workflow: CustomStoryWorkflow,
        step: CustomStoryWorkflowStep,
    ) -> None:
        workflow.current_step = step.value
        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
        await self.workflows.update(workflow)
        await self.session.commit()
        flags = self._flags(workflow)
        step_input = self._step_input(workflow, step)

        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            story_plan = await runner._step_generate_plan(workflow, flags)
            workflow.story_plan_json = story_plan
            workflow.story_plan_validated = False
            await self._annotate_latest_step(workflow, step, step_input, story_plan)
            return

        if step == CustomStoryWorkflowStep.STORY_PLAN_VALIDATION:
            if flags.skip_validation:
                workflow.story_plan_validated = True
                await self._record_completed_step(workflow, step, step_input, workflow.story_plan_json or {})
                return
            story_plan = await runner._step_validate_plan(workflow, workflow.story_plan_json or {}, flags)
            workflow.story_plan_json = story_plan
            workflow.story_plan_validated = True
            await self._annotate_latest_step(workflow, step, step_input, story_plan)
            return

        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            story_json = await runner._step_generate_story(workflow, workflow.story_plan_json or {}, flags)
            runner._apply_story_metadata(workflow, workflow.story_plan_json or {}, story_json)
            workflow.story_json = story_json
            await self._annotate_latest_step(workflow, step, step_input, story_json)
            return

        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            image_plan = await runner._step_generate_image_plan(
                workflow,
                workflow.story_plan_json or {},
                workflow.story_json or {},
                flags,
            )
            workflow.image_plan_json = image_plan
            workflow.image_plan_validated = False
            await self._annotate_latest_step(workflow, step, step_input, image_plan)
            return

        if step == CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION:
            if flags.skip_validation:
                workflow.image_plan_validated = True
                await self._record_completed_step(workflow, step, step_input, workflow.image_plan_json or {})
                return
            image_plan = await runner._step_validate_image_plan(
                workflow,
                workflow.image_plan_json or {},
                workflow.story_json or {},
                flags,
            )
            workflow.image_plan_json = image_plan
            workflow.image_plan_validated = True
            await self._annotate_latest_step(workflow, step, step_input, image_plan)
            return

        if step == CustomStoryWorkflowStep.IMAGE_GENERATION:
            if flags.skip_image_generation:
                await runner._create_pages_without_images(workflow, workflow.story_json or {})
                await self._record_completed_step(
                    workflow,
                    step,
                    step_input,
                    {"images_skipped": True, "message": "Image generation skipped by request"},
                )
                return
            if workflow.processing_mode == "delayed":
                batch_runner = self._batch_runner(workflow)
                await batch_runner._step_submit_images_batch(
                    workflow,
                    workflow.story_json or {},
                    workflow.image_plan_json or {},
                )
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = step.value
                return
            await runner._step_generate_images(
                workflow,
                workflow.story_json or {},
                workflow.image_plan_json or {},
                flags,
            )
            await self._annotate_latest_step(workflow, step, step_input, workflow.story_json or {})
            return

        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            if workflow.processing_mode == "delayed":
                batch_runner = self._batch_runner(workflow)
                message = await batch_runner._ensure_audio_batch_submitted(workflow)
                latest = await self.steps.latest_for_workflow_step(workflow.id, step)
                if latest is not None:
                    latest.input_json = step_input
                    latest.output_json = {"deferred": True, "message": message}
                    await self.steps.update(latest)
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = step.value
                return
            workflow.story_json = await runner._step_generate_narration(workflow, workflow.story_json or {})
            await self._annotate_latest_step(workflow, step, step_input, workflow.story_json or {})
            return

        if step == CustomStoryWorkflowStep.PUBLISH_STORY:
            await self._publish_story(workflow)
            await self._record_completed_step(
                workflow,
                step,
                step_input,
                {"story_id": str(workflow.story_id) if workflow.story_id else None},
            )
            return

        raise AppException(f"Unsupported custom story workflow step: {step}", code="CUSTOM_STORY_STEP_INVALID")

    def _story_runner(self, workflow: CustomStoryWorkflow) -> StoryService:
        runner = StoryService(self.session)
        runner.story_steps = self.steps
        runner.stories = _WorkflowStoryStore(self.workflows)
        runner.story_pages = _WorkflowPageBuffer(workflow)

        async def _set_current_step(_runner, story, step_name):
            story.status = CustomStoryWorkflowStatus.IN_PROGRESS
            story.current_step = step_name.value
            await self.workflows.update(story)
            await self.session.commit()

        async def _persist_story_content(_runner, story, story_json):
            story.story_json = story_json
            await self.workflows.update(story)
            await self.session.commit()

        async def _load_existing_story_json(_runner, story):
            if isinstance(story.story_json, dict) and story.story_json.get("pages"):
                return story.story_json
            return None

        runner._set_current_step = MethodType(_set_current_step, runner)
        runner._persist_story_content = MethodType(_persist_story_content, runner)
        runner._load_existing_story_json = MethodType(_load_existing_story_json, runner)
        return runner

    def _batch_runner(self, workflow: CustomStoryWorkflow) -> StoryServiceBatchService:
        batch_runner = StoryServiceBatchService(self.session)
        batch_runner.batch_jobs = _WorkflowBatchJobs(self.batch_jobs)
        batch_runner.story_steps = self.steps
        batch_runner.story_pages = _WorkflowPageBuffer(workflow)
        batch_runner.stories = _WorkflowStoryStore(self.workflows)
        batch_runner.workflow = self._story_runner(workflow)
        return batch_runner

    async def _publish_story(self, workflow: CustomStoryWorkflow) -> None:
        if workflow.story_id is not None:
            return
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        story = await self.stories.create(
            user_id=workflow.user_id,
            child_id=workflow.child_id,
            generation_mode=workflow.generation_mode,
            age_group=workflow.age_group.value,
            category=workflow.category,
            learning_goal=workflow.learning_goal,
            context=workflow.context,
            event_description=workflow.event_description,
            input_request=workflow.input_request,
            ai_provider=workflow.ai_provider,
            text_model=workflow.text_model,
            image_model=workflow.image_model,
            reference_image_model=workflow.reference_image_model,
        )
        story.status = StoryStatus.COMPLETED
        story.current_step = None
        story.title = workflow.title
        story.summary = workflow.summary
        story.moral = workflow.moral
        story_json = await self._copy_story_images_to_final_story_storage(story_json, story.id)
        await self.stories.upsert_content(story, language=DEFAULT_STORY_LANGUAGE, story_json=story_json)
        await self._publish_pages(story.id, story_json)
        await self.stories.update(story)
        workflow.story_id = story.id
        workflow.story_json = story_json
        for job in await self.batch_jobs.list_by_workflow(workflow.id):
            job.story_id = story.id
            await self.batch_jobs.update(job)
        await self.workflows.update(workflow)

    async def _copy_story_images_to_final_story_storage(
        self,
        story_json: dict[str, Any],
        story_id: UUID,
    ) -> dict[str, Any]:
        image_storage = get_image_storage_service()
        updated = dict(story_json)
        cover_image_url = await self._copy_story_image_url(
            image_storage,
            image_url=updated.get("cover_image_url"),
            story_id=story_id,
            filename="cover.png",
        )
        if cover_image_url:
            updated["cover_image_url"] = cover_image_url

        pages = list(updated.get("pages") or [])
        updated["pages"] = pages
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            page_number = int(page.get("page_number") or index)
            image_url = await self._copy_story_image_url(
                image_storage,
                image_url=page.get("image_url"),
                story_id=story_id,
                filename=f"page_{page_number}.png",
            )
            if image_url:
                page["image_url"] = image_url

        back_cover_image_url = await self._copy_story_image_url(
            image_storage,
            image_url=updated.get("back_cover_image_url"),
            story_id=story_id,
            filename="back_cover.png",
        )
        if back_cover_image_url:
            updated["back_cover_image_url"] = back_cover_image_url
        return updated

    @staticmethod
    async def _copy_story_image_url(
        image_storage: Any,
        *,
        image_url: str | None,
        story_id: UUID,
        filename: str,
    ) -> str | None:
        if not image_url or str(image_url).startswith("data:"):
            return image_url
        normalized_url = str(image_url).replace("\\", "/")
        if f"/stories/{story_id}/" in normalized_url:
            return image_url
        try:
            image_bytes = await image_storage.get_image_bytes(image_url)
            return await image_storage.save_story_image(story_id, image_bytes, filename, "")
        except Exception:
            return image_url

    async def _publish_pages(self, story_id: UUID, story_json: dict[str, Any]) -> None:
        if story_json.get("cover_image_url"):
            await self.story_pages.upsert_page(
                story_id,
                page_number=0,
                page_type="cover",
                text="",
                image_url=story_json.get("cover_image_url"),
            )
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for idx, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            await self.story_pages.upsert_page(
                story_id,
                page_number=int(page.get("page_number") or idx),
                page_type="page",
                text=str(page.get("text") or ""),
                image_prompt=page.get("image_prompt"),
                image_url=page.get("image_url"),
            )
        if story_json.get("back_cover_image_url"):
            await self.story_pages.upsert_page(
                story_id,
                page_number=len(pages) + 1,
                page_type="back_cover",
                text="",
                image_url=story_json.get("back_cover_image_url"),
            )

    async def _record_completed_step(
        self,
        workflow: CustomStoryWorkflow,
        step_name: CustomStoryWorkflowStep,
        step_input: dict[str, Any] | None,
        output: dict[str, Any] | None,
    ) -> None:
        step = await self.steps.latest_for_workflow_step(workflow.id, step_name)
        if step is None or step.status == StepStatus.COMPLETED:
            step = await self.steps.create(workflow.id, step_name.value)
        step.status = StepStatus.COMPLETED
        step.started_at = step.started_at or datetime.now(UTC)
        step.completed_at = datetime.now(UTC)
        step.input_json = step_input
        step.output_json = output
        step.error_message = None
        await self.steps.update(step)

    async def _annotate_latest_step(
        self,
        workflow: CustomStoryWorkflow,
        step_name: CustomStoryWorkflowStep,
        step_input: dict[str, Any] | None,
        output: dict[str, Any] | None,
    ) -> None:
        step = await self.steps.latest_for_workflow_step(workflow.id, step_name)
        if step is None:
            await self._record_completed_step(workflow, step_name, step_input, output)
            return
        step.input_json = step_input
        if output is not None and step.output_json is None:
            step.output_json = output
        await self.steps.update(step)

    async def reconcile_batch_jobs(self, *, limit: int = 50) -> dict[str, Any]:
        jobs = await self.batch_jobs.list_reconcilable(limit=limit)
        results: list[dict[str, Any]] = []
        processed_count = 0
        for job in jobs:
            try:
                result = await self._reconcile_batch_job(job)
                if result["action"] not in {"still_running", "skipped"}:
                    processed_count += 1
                results.append(result)
            except Exception as exc:
                results.append(
                    {
                        "workflow_id": job.workflow_id,
                        "story_id": job.story_id,
                        "batch_job_id": job.id,
                        "job_type": self._status_value(job.job_type),
                        "status": self._status_value(job.status),
                        "provider_state": job.provider_state,
                        "action": "error",
                        "message": str(exc),
                    }
                )
        return {"checked_count": len(jobs), "processed_count": processed_count, "results": results}

    async def _reconcile_batch_job(self, job: CustomStoryBatchJob) -> dict[str, Any]:
        if not job.provider_job_name:
            return self._batch_reconcile_result(job, "skipped", "Batch job has no provider job name")

        workflow = await self.workflows.get_by_id(job.workflow_id)
        if workflow is None:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = "Custom story workflow not found during batch reconciliation"
            await self.batch_jobs.update(job)
            await self.session.commit()
            return self._batch_reconcile_result(job, "failed", job.error_message)

        batch_runner = self._batch_runner(workflow)
        provider_job = await batch_runner.google_client.aio.batches.get(name=job.provider_job_name)
        state_name = batch_runner._job_state_name(provider_job)
        job.provider_state = state_name

        if state_name in batch_runner.SUCCEEDED_STATES:
            if job.job_type == StoryBatchJobType.IMAGE:
                await self._process_reconciled_image_job(workflow, job, provider_job, batch_runner)
            elif job.job_type == StoryBatchJobType.AUDIO:
                await self._process_reconciled_audio_job(workflow, job, provider_job, batch_runner)

            if self._status_value(job.status) == StoryBatchJobStatus.FAILED.value:
                return self._batch_reconcile_result(job, "failed", job.error_message)
            await self.run(workflow.id)
            return self._batch_reconcile_result(job, "processed", "Custom story workflow batch job processed")

        if state_name in batch_runner.CANCELLED_STATES:
            job.status = StoryBatchJobStatus.CANCELLED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(workflow, job.error_message)
            await self.session.commit()
            return self._batch_reconcile_result(job, "cancelled", job.error_message)

        if state_name in batch_runner.FAILED_STATES:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(workflow, job.error_message)
            await self.session.commit()
            return self._batch_reconcile_result(job, "failed", job.error_message)

        job.status = StoryBatchJobStatus.RUNNING
        await self.batch_jobs.update(job)
        await self.session.commit()
        return self._batch_reconcile_result(job, "still_running", f"Provider state is {state_name}")

    async def _process_reconciled_image_job(
        self,
        workflow: CustomStoryWorkflow,
        job: CustomStoryBatchJob,
        provider_job: Any,
        batch_runner: StoryServiceBatchService,
    ) -> None:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else None
        if story_json is None:
            raise AppException("Story JSON is missing during image batch reconciliation", code="STORY_JSON_MISSING")
        if not isinstance(workflow.image_plan_json, dict):
            raise AppException("Image plan is missing during image batch reconciliation", code="IMAGE_PLAN_MISSING")

        items = await batch_runner._build_image_items(workflow, story_json, workflow.image_plan_json)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]
        completed_keys, failed_keys, response_summary = await batch_runner._process_image_batch_responses(
            workflow,
            story_json,
            items,
            provider_job,
        )
        job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
        job.completed_item_count = len(completed_keys)
        job.failed_item_count = len(failed_keys)
        job.missing_keys = sorted({item.key for item in items} - completed_keys)
        job.response_payload = response_summary
        job.error_message = f"Missing image keys: {', '.join(sorted(failed_keys))}" if failed_keys else None
        await self.batch_jobs.update(job)

        step = await self.steps.latest_for_workflow_step(workflow.id, CustomStoryWorkflowStep.IMAGE_GENERATION)
        if step is None:
            step = await self.steps.create(workflow.id, CustomStoryWorkflowStep.IMAGE_GENERATION.value)
        step.status = StepStatus.COMPLETED if not failed_keys else StepStatus.FAILED
        step.error_message = job.error_message
        step.output_json = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "completed_keys": sorted(completed_keys),
            "failed_keys": sorted(failed_keys),
        }
        step.completed_at = datetime.now(UTC)
        await self.steps.update(step)

        workflow.story_json = story_json
        if failed_keys:
            await self._mark_workflow_failed(workflow, job.error_message or "Image batch reconciliation failed")
        await self.workflows.update(workflow)
        await self.session.commit()

    async def _process_reconciled_audio_job(
        self,
        workflow: CustomStoryWorkflow,
        job: CustomStoryBatchJob,
        provider_job: Any,
        batch_runner: StoryServiceBatchService,
    ) -> None:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else None
        if story_json is None:
            raise AppException("Story JSON is missing during audio batch reconciliation", code="STORY_JSON_MISSING")

        items = batch_runner._build_audio_items(story_json, age_group=workflow.age_group.value)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]
        completed_keys, failed_keys, response_summary = await batch_runner._process_audio_batch_responses(
            workflow,
            story_json,
            items,
            provider_job,
        )
        job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
        job.completed_item_count = len(completed_keys)
        job.failed_item_count = len(failed_keys)
        job.missing_keys = sorted({item.key for item in items} - completed_keys)
        job.response_payload = response_summary
        job.error_message = f"Missing audio keys: {', '.join(sorted(failed_keys))}" if failed_keys else None
        await self.batch_jobs.update(job)

        step = await self.steps.latest_for_workflow_step(workflow.id, CustomStoryWorkflowStep.NARRATION_GENERATION)
        if step is None:
            step = await self.steps.create(workflow.id, CustomStoryWorkflowStep.NARRATION_GENERATION.value)
        step.status = StepStatus.COMPLETED if not failed_keys else StepStatus.FAILED
        step.error_message = job.error_message
        step.output_json = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "completed_keys": sorted(completed_keys),
            "failed_keys": sorted(failed_keys),
        }
        step.completed_at = datetime.now(UTC)
        await self.steps.update(step)

        workflow.story_json = story_json
        if failed_keys:
            await self._mark_workflow_failed(workflow, job.error_message or "Audio batch reconciliation failed")
        await self.workflows.update(workflow)
        await self.session.commit()

    async def _mark_workflow_failed(self, workflow: CustomStoryWorkflow, error_message: str | None) -> None:
        workflow.status = CustomStoryWorkflowStatus.FAILED
        workflow.error_message = error_message
        await self.workflows.update(workflow)

    def _batch_reconcile_result(
        self,
        job: CustomStoryBatchJob,
        action: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "workflow_id": job.workflow_id,
            "story_id": job.story_id,
            "batch_job_id": job.id,
            "job_type": self._status_value(job.job_type),
            "status": self._status_value(job.status),
            "provider_state": job.provider_state,
            "action": action,
            "message": message,
        }

    @staticmethod
    def _job_type_for_step(step_name: CustomStoryWorkflowStep):
        return StoryBatchJobType.IMAGE if step_name == CustomStoryWorkflowStep.IMAGE_GENERATION else StoryBatchJobType.AUDIO

    async def _first_incomplete_step(self, workflow: CustomStoryWorkflow) -> CustomStoryWorkflowStep:
        for step in self.ORDERED_STEPS:
            if not await self._step_is_complete(workflow, step):
                return step
        return CustomStoryWorkflowStep.PUBLISH_STORY

    async def _step_is_complete(self, workflow: CustomStoryWorkflow, step: CustomStoryWorkflowStep) -> bool:
        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            return isinstance(workflow.story_plan_json, dict) and bool(workflow.story_plan_json)
        if step == CustomStoryWorkflowStep.STORY_PLAN_VALIDATION:
            return bool(workflow.story_plan_validated)
        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            return isinstance(workflow.story_json, dict) and bool(workflow.story_json.get("pages"))
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return isinstance(workflow.image_plan_json, dict) and bool(workflow.image_plan_json)
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION:
            return bool(workflow.image_plan_validated)
        if step == CustomStoryWorkflowStep.IMAGE_GENERATION:
            flags = self._flags(workflow)
            if flags.skip_image_generation:
                return True
            if workflow.processing_mode == "delayed":
                latest = await self.batch_jobs.latest_for_workflow_type(workflow.id, self._job_type_for_step(step))
                return latest is not None and self._status_value(latest.status) == "SUCCEEDED"
            return self._story_has_images(workflow.story_json or {})
        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            if workflow.processing_mode == "delayed":
                latest = await self.batch_jobs.latest_for_workflow_type(workflow.id, self._job_type_for_step(step))
                return latest is not None and self._status_value(latest.status) == "SUCCEEDED"
            return self._story_has_audio(workflow.story_json or {})
        if step == CustomStoryWorkflowStep.PUBLISH_STORY:
            return workflow.story_id is not None
        return False

    @staticmethod
    def _story_has_images(story_json: dict[str, Any]) -> bool:
        if story_json.get("cover_image_url") or story_json.get("back_cover_image_url"):
            return True
        return any(isinstance(page, dict) and page.get("image_url") for page in story_json.get("pages") or [])

    @staticmethod
    def _story_has_audio(story_json: dict[str, Any]) -> bool:
        return any(
            isinstance(page, dict) and (page.get("audio_url") or page.get("tts_skipped"))
            for page in story_json.get("pages") or []
        )

    @staticmethod
    def _flags(workflow: CustomStoryWorkflow) -> StoryGenerationFlags:
        request = workflow.input_request if isinstance(workflow.input_request, dict) else {}
        return StoryGenerationFlags(
            skip_image_generation=bool(request.get("skip_image_generation", False)),
            skip_validation=bool(request.get("skip_validation", False)),
        )

    @staticmethod
    def _step_input(workflow: CustomStoryWorkflow, step: CustomStoryWorkflowStep) -> dict[str, Any] | None:
        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            return workflow.input_request
        if step == CustomStoryWorkflowStep.STORY_PLAN_VALIDATION:
            return {"story_plan": workflow.story_plan_json}
        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            return {"story_plan": workflow.story_plan_json}
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return {"story_plan": workflow.story_plan_json, "story_json": workflow.story_json}
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION:
            return {"image_plan": workflow.image_plan_json, "story_json": workflow.story_json}
        if step == CustomStoryWorkflowStep.IMAGE_GENERATION:
            return {"image_plan": workflow.image_plan_json, "story_json": workflow.story_json}
        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            return {"story_json": workflow.story_json}
        if step == CustomStoryWorkflowStep.PUBLISH_STORY:
            return {"story_json": workflow.story_json}
        return None

    @staticmethod
    def _status_value(status: Any) -> str:
        return status.value if hasattr(status, "value") else str(status)

    async def _get_owned(self, user_id: UUID, workflow_id: UUID) -> CustomStoryWorkflow:
        workflow = await self.workflows.get_for_user(user_id, workflow_id)
        if workflow is None:
            raise NotFoundException("Custom story workflow not found")
        return workflow

    @staticmethod
    def _response(workflow: CustomStoryWorkflow) -> CustomStoryWorkflowResponse:
        return CustomStoryWorkflowResponse(
            workflow_id=workflow.id,
            story_id=workflow.story_id,
            child_id=workflow.child_id,
            status=workflow.status.value if hasattr(workflow.status, "value") else str(workflow.status),
            current_step=workflow.current_step,
            error_message=workflow.error_message,
            generation_mode=workflow.generation_mode,
            processing_mode=workflow.processing_mode,
            category=workflow.category,
            learning_goal=workflow.learning_goal,
            context=workflow.context,
            event_description=workflow.event_description,
            title=workflow.title,
            summary=workflow.summary,
            moral=workflow.moral,
            input_request=workflow.input_request,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
        )

    @staticmethod
    def _step_response(step) -> CustomStoryWorkflowStepResponse:
        return CustomStoryWorkflowStepResponse(
            id=step.id,
            workflow_id=step.workflow_id,
            step_name=step.step_name.value if hasattr(step.step_name, "value") else str(step.step_name),
            status=step.status.value if hasattr(step.status, "value") else str(step.status),
            input=step.input_json,
            prompt=step.prompt,
            output=step.output_json,
            error_message=step.error_message,
            retry_count=step.retry_count,
            started_at=step.started_at,
            completed_at=step.completed_at,
            created_at=step.created_at,
        )
