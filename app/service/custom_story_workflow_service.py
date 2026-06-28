from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from types import MethodType, SimpleNamespace
from typing import Any
from uuid import UUID

from fastapi import status
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.custom_story_input_safety_audit import CustomStoryInputSafetyAuditStatus
from app.entity.custom_story_workflow import (
    CustomStoryBatchJobEntity,
    CustomStoryWorkflowEventEntity,
    CustomStoryWorkflowEventStatus,
    CustomStoryWorkflowEntity,
    CustomStoryWorkflowStatus,
    CustomStoryWorkflowStep,
    CustomStoryWorkflowType,
)
from app.entity.generic_story import GenericStory
from app.entity.story import StoryStatus
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StoryStepName
from app.entity.story_step import StepStatus
from app.model.request.story import (
    StoryGenerationRequest,
    age_group_for_reader_category,
    normalize_reader_category,
    reader_category_for_age_group,
)
from app.model.response.common import PaginatedResponse
from app.model.response.custom_story_workflow import (
    CustomStoryWorkflowBatchJobResponse,
    CustomStoryWorkflowEventResponse,
    CustomStoryWorkflowResponse,
    CustomStoryWorkflowStepResponse,
)
from app.repository.child_repository import ChildRepository
from app.repository.custom_story_workflow_repository import (
    CustomStoryInputSafetyAuditRepository,
    CustomStoryBatchJobRepository,
    CustomStoryWorkflowEventRepository,
    CustomStoryWorkflowRepository,
    CustomStoryWorkflowStepRepository,
)
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.story_page_repository import StoryPageRepository
from app.repository.story_repository import StoryRepository
from app.service.image_storage_provider import get_image_storage_service
from app.service.image_webp_converter import ImageWebPConverter
from app.service.story_input_safety_service import StoryInputSafetyService
from app.service.story_service import (
    DEFAULT_STORY_LANGUAGE,
    StoryGenerationFlags,
    StoryService,
    _compact_json,
    _normalize_story_output,
    _normalize_story_languages,
    _repair_json,
    _set_story_json_language_variant,
    _story_json_language_variant,
    _sync_story_media_to_language_variants,
    _story_source_inputs,
)
from app.service.story_completion_email_service import StoryCompletionEmailService
from app.service.story_service_batch_service import StoryServiceBatchService
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class _WorkflowBatchJobs:
    """Adapter so StoryServiceBatchService can write workflow-owned batch jobs."""

    def __init__(self, batch_jobs: CustomStoryBatchJobRepository, workflow: CustomStoryWorkflowEntity):
        self.batch_jobs = batch_jobs
        self.workflow = workflow

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
    ) -> CustomStoryBatchJobEntity:
        return await self.batch_jobs.create(
            workflow_id=story_id,
            story_id=None,
            generic_story_id=getattr(self.workflow, "generic_story_id", None),
            job_type=job_type,
            attempt=attempt,
            expected_item_count=expected_item_count,
            request_keys=request_keys,
            provider_model=provider_model,
            request_payload=request_payload,
        )

    async def latest_for_story_type(self, story_id: UUID, job_type: StoryBatchJobType) -> CustomStoryBatchJobEntity | None:
        return await self.batch_jobs.latest_for_workflow_type(story_id, job_type)

    async def update(self, job: CustomStoryBatchJobEntity) -> CustomStoryBatchJobEntity:
        return await self.batch_jobs.update(job)


class _WorkflowStoryStore:
    def __init__(self, workflows: CustomStoryWorkflowRepository):
        self.workflows = workflows

    async def update(self, workflow: CustomStoryWorkflowEntity) -> CustomStoryWorkflowEntity:
        return await self.workflows.update(workflow)

    async def upsert_content(self, workflow: CustomStoryWorkflowEntity, *, language: str, story_json: dict):
        _ = language
        workflow.story_json = story_json
        await self.workflows.update(workflow)
        return SimpleNamespace(story_json=story_json)

    async def get_content_by_story_and_language(self, *, story_id: UUID, language: str):
        _ = story_id, language
        return None


class _WorkflowPageBuffer:
    def __init__(self, workflow: CustomStoryWorkflowEntity):
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
        self.input_safety_audits = CustomStoryInputSafetyAuditRepository(session)
        self.events = CustomStoryWorkflowEventRepository(session)
        self.batch_jobs = CustomStoryBatchJobRepository(session)
        self.children = ChildRepository(session)
        self.stories = StoryRepository(session)
        self.generic_stories = GenericStoryRepository(session)
        self.story_pages = StoryPageRepository(session)

    @staticmethod
    def _print_reconcile_event(event: str, **fields: Any) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"[CUSTOM_RECONCILE] {event}" + (f" {details}" if details else ""), flush=True)

    async def create(self, user_id: UUID, payload: StoryGenerationRequest) -> CustomStoryWorkflowResponse:
        child = await self.children.get_for_user(user_id, payload.child_id)
        if child is None:
            raise NotFoundException("Child profile not found")
        if payload.use_child_character and not child.character_image_url:
            raise AppException(
                "Child must have a generated character image when used as the story hero",
                code="NO_CHARACTER_IMAGE",
            )

        story_service = StoryService(self.session)
        safety_service = StoryInputSafetyService()
        age_group = age_group_for_reader_category(payload.reader_category)
        execute_workflow = self._effective_execute_workflow(payload)
        inspection = await safety_service.inspect(payload)
        audit = await self.input_safety_audits.create(
            user_id=user_id,
            child_id=payload.child_id,
            provider=inspection.provider,
            model=inspection.model,
            request_json=inspection.request_json,
            request_idea_json=self._input_safety_idea_json(payload),
            prompt=inspection.prompt,
            status=CustomStoryInputSafetyAuditStatus.IN_PROGRESS,
            response_text=inspection.response_text,
            response_json=inspection.response_json,
            safe=inspection.result.safe if inspection.result is not None else None,
            risk_level=inspection.result.risk_level if inspection.result is not None else None,
            blocked_categories=inspection.result.blocked_categories if inspection.result is not None else None,
            reason=inspection.result.reason if inspection.result is not None else None,
            safe_rewrite=inspection.result.safe_rewrite if inspection.result is not None else None,
            error_code=inspection.error_code,
            error_message=inspection.error_message,
        )
        if inspection.error_message:
            audit.status = CustomStoryInputSafetyAuditStatus.ERROR
            await self.input_safety_audits.update(audit)
            await self.session.commit()
            raise AppException(
                inspection.error_message,
                status.HTTP_503_SERVICE_UNAVAILABLE,
                inspection.error_code or "STORY_INPUT_SAFETY_UNAVAILABLE",
            )
        if inspection.result is None:
            audit.status = CustomStoryInputSafetyAuditStatus.ERROR
            audit.error_code = "STORY_INPUT_SAFETY_UNAVAILABLE"
            audit.error_message = "Story safety validation returned an unexpected response. Please try again."
            await self.input_safety_audits.update(audit)
            await self.session.commit()
            raise AppException(
                "Story safety validation returned an unexpected response. Please try again.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "STORY_INPUT_SAFETY_UNAVAILABLE",
            )

        audit.status = (
            CustomStoryInputSafetyAuditStatus.SAFE
            if inspection.result.safe
            else CustomStoryInputSafetyAuditStatus.UNSAFE
        )
        await self.input_safety_audits.update(audit)
        await self.session.commit()
        if not inspection.result.safe:
            self._raise_input_safety_failure(inspection.result, audit)

        workflow = await self.workflows.create(
            user_id=user_id,
            child_id=payload.child_id,
            story_type=CustomStoryWorkflowType.CUSTOM,
            age_group=age_group,
            category=payload.category,
            learning_goal=payload.learning_goal,
            context=payload.context,
            languages=payload.languages,
            reader_category=payload.reader_category.value,
            use_child_character=payload.use_child_character,
            execute_image=bool(payload.execute_image),
            execute_narration=payload.execute_narration,
            skip_validation=payload.skip_validation,
            execute_workflow=execute_workflow,
            status=CustomStoryWorkflowStatus.PENDING,
            **story_service._current_ai_config(),
        )
        audit.workflow_id = workflow.id
        await self.input_safety_audits.update(audit)
        if execute_workflow:
            await self.events.create_if_absent(
                workflow_id=workflow.id,
                step_name=CustomStoryWorkflowStep.STORY_PLAN_GENERATION,
            )
        await self.session.commit()
        return self._response(workflow)

    @staticmethod
    def _effective_execute_workflow(payload: StoryGenerationRequest) -> bool:
        if "execute_workflow" in payload.model_fields_set:
            return bool(payload.execute_workflow)
        return bool(settings.CUSTOM_STORY_EXECUTE_WORKFLOW_DEFAULT)

    @staticmethod
    def _input_safety_idea_json(payload: StoryGenerationRequest) -> dict[str, str]:
        return {
            "category": payload.category or "",
            "learning_goal": payload.learning_goal or "",
            "context": payload.context or "",
        }

    @staticmethod
    def _use_child_character_enabled(workflow: CustomStoryWorkflowEntity) -> bool:
        return bool(getattr(workflow, "use_child_character", False))

    @staticmethod
    def _raise_input_safety_failure(
        result: Any,
        audit: Any | None = None,
    ) -> None:
        _ = audit
        raise AppException(
            "Story idea is not safe for children. Please revise the prompt and try again.",
            status.HTTP_400_BAD_REQUEST,
            "STORY_INPUT_UNSAFE",
            details={
                "risk_level": getattr(result, "risk_level", None),
                "blocked_categories": getattr(result, "blocked_categories", None),
                "reason": getattr(result, "reason", None),
                "safe_rewrite": getattr(result, "safe_rewrite", None),
            },
        )

    async def list(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
        child_id: UUID | None = None,
        status_filter: str | None = None,
    ) -> PaginatedResponse[CustomStoryWorkflowResponse]:
        try:
            workflows, total = await self.workflows.list_for_user(
                user_id,
                page=page,
                page_size=page_size,
                child_id=child_id,
                status_filter=status_filter,
                story_type=CustomStoryWorkflowType.CUSTOM,
            )
        except TypeError:
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
            raise NotFoundException("Story workflow not found")
        story_type = self._status_value(getattr(workflow, "story_type", CustomStoryWorkflowType.CUSTOM))
        retryable_statuses = {
            CustomStoryWorkflowStatus.FAILED.value,
            CustomStoryWorkflowStatus.IN_PROGRESS.value,
        }
        if story_type == CustomStoryWorkflowType.GENERIC.value:
            retryable_statuses.add(CustomStoryWorkflowStatus.PENDING.value)
        if self._status_value(workflow.status) not in retryable_statuses:
            raise AppException(
                "Only pending, failed, or in-progress generic workflows and failed or in-progress custom workflows can be retried",
                status.HTTP_400_BAD_REQUEST,
                "STORY_WORKFLOW_RETRY_STATUS_INVALID",
            )
        workflow.status = CustomStoryWorkflowStatus.PENDING
        workflow.error_message = None
        retry_step = await self._first_incomplete_step(workflow)
        workflow.current_step = retry_step.value
        await self.workflows.update(workflow)
        if retry_step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            await self._enqueue_narration_language_events(workflow, retry_count=1, source="manual_retry")
        else:
            await self.events.create_if_absent(
                workflow_id=workflow.id,
                step_name=retry_step,
                retry_count=1,
                metadata_json={"source": "manual_retry"},
            )
        await self.session.commit()
        return self._response(workflow)

    async def process_events(self, *, limit: int = 10) -> dict[str, Any]:
        events = await self.events.claim_pending(limit)
        await self.session.commit()
        results: list[dict[str, Any]] = []
        processed_count = 0
        for event in events:
            event_id = event.id
            workflow_id = event.workflow_id
            step_name = self._status_value(event.step_name)
            result = {
                "event_id": event_id,
                "workflow_id": workflow_id,
                "step_name": step_name,
            }
            try:
                await self._process_event(event)
                processed_count += 1
                results.append({**result, "status": self._status_value(event.status)})
            except Exception as exc:
                await self.session.rollback()
                failed_event = await self.session.get(CustomStoryWorkflowEventEntity, event_id)
                workflow = await self.workflows.get_by_id(workflow_id)
                if failed_event is not None:
                    failed_event.status = CustomStoryWorkflowEventStatus.FAILED
                    failed_event.error_message = str(exc)
                    failed_event.completed_at = datetime.now(UTC)
                    await self.events.update(failed_event)
                if workflow is not None:
                    await self._mark_workflow_failed(workflow, str(exc))
                await self.session.commit()
                logger.exception(
                    "[CUSTOM_WORKFLOW_EVENT] workflow=%s event=%s step=%s action=failed error=%s",
                    workflow_id,
                    event_id,
                    step_name,
                    exc,
                )
                results.append({**result, "status": CustomStoryWorkflowEventStatus.FAILED.value, "error": str(exc)})
        return {"checked_count": len(events), "processed_count": processed_count, "results": results}

    async def _process_event(self, event: CustomStoryWorkflowEventEntity) -> None:
        workflow = await self.workflows.get_by_id_for_update(event.workflow_id)
        if workflow is None:
            raise NotFoundException("Custom story workflow not found")
        step = CustomStoryWorkflowStep(self._status_value(event.step_name))
        if self._status_value(workflow.status) == CustomStoryWorkflowStatus.COMPLETED.value:
            await self._complete_event(event, {"skipped": True, "reason": "workflow_completed"})
            return
        if self._step_disabled_by_request(workflow, step):
            runner = self._story_runner(workflow)
            await self._execute_step(runner, workflow, step)
            await self._complete_event(event, {"skipped": True})
            await self._enqueue_next_step(workflow, step)
            await self.session.commit()
            return

        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
        workflow.current_step = step.value
        workflow.error_message = None
        await self.workflows.update(workflow)
        await self.session.commit()

        runner = self._story_runner(workflow)
        await runner._ensure_story_ai_config(workflow)

        if step in {
            CustomStoryWorkflowStep.STORY_PLAN_GENERATION,
            CustomStoryWorkflowStep.STORY_GENERATION,
            CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        }:
            job = await self._submit_text_batch_step(runner, workflow, step, event=event)
            await self._mark_event_batch_submitted(event, job)
            await self.session.commit()
            return

        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            language = self._event_language(event, workflow)
            if language is None:
                await self._enqueue_narration_language_events(workflow)
                await self._complete_event(event, {"skipped": True, "reason": "split_into_language_events"})
                await self.session.commit()
                return
            await self._execute_narration_language_event(workflow, event, language)
            await self.session.commit()
            return

        await self._execute_step(runner, workflow, step)
        if step in {CustomStoryWorkflowStep.IMAGE_GENERATION, CustomStoryWorkflowStep.NARRATION_GENERATION}:
            job = await self._active_delayed_batch_job(workflow, step)
            if job is not None:
                await self._mark_event_batch_submitted(event, job)
            else:
                await self._complete_event(event, {"mode": "local_step"})
            await self.session.commit()
            return
        await self._complete_event(event, {"mode": "local_step"})
        await self._enqueue_next_step(workflow, step)
        await self.session.commit()

    async def _complete_event(self, event: CustomStoryWorkflowEventEntity, metadata: dict[str, Any] | None = None) -> None:
        event.status = CustomStoryWorkflowEventStatus.COMPLETED
        event.completed_at = datetime.now(UTC)
        if metadata:
            current = dict(event.metadata_json or {})
            current.update(metadata)
            event.metadata_json = current
        await self.events.update(event)

    async def _mark_event_batch_submitted(
        self,
        event: CustomStoryWorkflowEventEntity,
        job: CustomStoryBatchJobEntity,
    ) -> None:
        event.status = CustomStoryWorkflowEventStatus.BATCH_SUBMITTED
        event.completed_at = None
        current = dict(event.metadata_json or {})
        current.update(
            {
                "batch_job_id": str(job.id),
                "provider_job_name": getattr(job, "provider_job_name", None),
                "job_type": self._status_value(job.job_type),
            }
        )
        event.metadata_json = current
        await self.events.update(event)

    async def _mark_event_failed(
        self,
        event: CustomStoryWorkflowEventEntity | None,
        error_message: str | None,
    ) -> None:
        if event is None or not hasattr(self, "events"):
            return
        event.status = CustomStoryWorkflowEventStatus.FAILED
        event.error_message = error_message
        event.completed_at = datetime.now(UTC)
        metadata = dict(event.metadata_json or {})
        if error_message:
            metadata["failure_error"] = error_message
        event.metadata_json = metadata
        await self.events.update(event)

    async def _create_batch_retry_event(
        self,
        *,
        workflow: CustomStoryWorkflowEntity,
        step_name: CustomStoryWorkflowStep,
        source_event: CustomStoryWorkflowEventEntity | None,
        retry_job: CustomStoryBatchJobEntity,
        retry_comment: str,
        retry_reason: str | None,
    ) -> CustomStoryWorkflowEventEntity | None:
        if not hasattr(self, "events"):
            return None
        retry_payload = getattr(retry_job, "request_payload", None)
        retry_payload = retry_payload if isinstance(retry_payload, dict) else {}
        retry_event = await self.events.create(
            workflow_id=workflow.id,
            step_name=step_name,
            retry_count=(int(getattr(source_event, "retry_count", 0) or 0) + 1) if source_event is not None else 1,
            retry_flag=True,
            retry_comment=retry_comment,
            retry_source_event_id=getattr(source_event, "id", None),
            metadata_json={
                "retry_reason": retry_reason,
                "language": retry_payload.get("language"),
                "source_batch_job_id": (
                    str((source_event.metadata_json or {}).get("batch_job_id") or "")
                    if source_event is not None and isinstance(source_event.metadata_json, dict)
                    else None
                ),
            },
        )
        await self._mark_event_batch_submitted(retry_event, retry_job)
        return retry_event

    async def _enqueue_next_step(
        self,
        workflow: CustomStoryWorkflowEntity,
        current_step: CustomStoryWorkflowStep,
    ) -> CustomStoryWorkflowStep | None:
        next_step = self._next_enabled_step(workflow, current_step)
        if next_step is None:
            workflow.status = CustomStoryWorkflowStatus.COMPLETED
            workflow.current_step = None
            await self.workflows.update(workflow)
            await self.session.commit()
            await self._send_completion_notifications(workflow)
            return None
        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
        workflow.current_step = next_step.value
        await self.workflows.update(workflow)
        if next_step == CustomStoryWorkflowStep.NARRATION_GENERATION and self._execute_narration_enabled(workflow):
            await self._enqueue_narration_language_events(workflow)
        else:
            await self.events.create_if_absent(workflow_id=workflow.id, step_name=next_step)
        return next_step

    async def _enqueue_narration_language_events(
        self,
        workflow: CustomStoryWorkflowEntity,
        *,
        retry_count: int = 0,
        source: str | None = None,
    ) -> None:
        if not hasattr(self, "events"):
            return
        for language in self._workflow_languages(workflow):
            metadata = {"language": language}
            if source:
                metadata["source"] = source
            await self.events.create_if_absent(
                workflow_id=workflow.id,
                step_name=CustomStoryWorkflowStep.NARRATION_GENERATION,
                retry_count=retry_count,
                metadata_json=metadata,
            )

    async def _execute_narration_language_event(
        self,
        workflow: CustomStoryWorkflowEntity,
        event: CustomStoryWorkflowEventEntity,
        language: str,
    ) -> None:
        step_input = self._step_input(workflow, CustomStoryWorkflowStep.NARRATION_GENERATION) or {}
        step_input["language"] = language
        batch_runner = self._batch_runner(workflow)
        language_story_json = self._workflow_story_json_for_language(workflow, language)
        if not language_story_json.get("pages"):
            raise AppException(
                f"Story JSON is missing for narration language '{language}'",
                code="STORY_LANGUAGE_JSON_MISSING",
            )

        items = batch_runner._build_audio_items(
            language_story_json,
            age_group=workflow.age_group.value,
            language=language,
        )
        missing = batch_runner._missing_audio_items(language_story_json, items)
        if settings.GOOGLE_TTS_SKIP_CALL and missing:
            batch_runner._apply_skipped_tts(language_story_json, missing)
            self._set_workflow_story_json_for_language(workflow, language, language_story_json)
            await self.workflows.update(workflow)
            missing = []

        if not missing:
            self._set_workflow_story_json_for_language(workflow, language, language_story_json)
            await self._complete_event(event, {"language": language, "audio_complete": True})
            if self._workflow_has_audio_for_all_languages(workflow):
                await self._record_completed_step(
                    workflow,
                    CustomStoryWorkflowStep.NARRATION_GENERATION,
                    self._step_input(workflow, CustomStoryWorkflowStep.NARRATION_GENERATION),
                    {
                        "narration_generated": True,
                        "languages": self._workflow_languages(workflow),
                    },
                )
                await self._enqueue_next_step(workflow, CustomStoryWorkflowStep.NARRATION_GENERATION)
            else:
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = CustomStoryWorkflowStep.NARRATION_GENERATION.value
                await self.workflows.update(workflow)
            return

        existing_job = await self._active_delayed_batch_job(
            workflow,
            CustomStoryWorkflowStep.NARRATION_GENERATION,
            language=language,
        )
        if existing_job is not None:
            await self._record_submitted_batch_step(
                workflow,
                CustomStoryWorkflowStep.NARRATION_GENERATION,
                step_input,
                existing_job,
                f"Existing {language} narration batch job is still active; event reused it.",
            )
            await self._mark_event_batch_submitted(event, existing_job)
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = CustomStoryWorkflowStep.NARRATION_GENERATION.value
            await self.workflows.update(workflow)
            return

        job = await batch_runner._submit_audio_batch_job_only(
            workflow,
            missing,
            attempt=1,
            language=language,
            event_id=event.id,
        )
        await self._record_submitted_batch_step(
            workflow,
            CustomStoryWorkflowStep.NARRATION_GENERATION,
            step_input,
            job,
            f"{language} narration batch submitted; reconcile scheduler will process results.",
        )
        await self._mark_event_batch_submitted(event, job)
        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
        workflow.current_step = CustomStoryWorkflowStep.NARRATION_GENERATION.value
        workflow.error_message = None
        await self.workflows.update(workflow)

    def _next_enabled_step(
        self,
        workflow: CustomStoryWorkflowEntity,
        current_step: CustomStoryWorkflowStep,
    ) -> CustomStoryWorkflowStep | None:
        start = self.ORDERED_STEPS.index(current_step) + 1
        for step in self.ORDERED_STEPS[start:]:
            return step
        return None

    async def _submit_text_batch_step(
        self,
        runner: StoryService,
        workflow: CustomStoryWorkflowEntity,
        step: CustomStoryWorkflowStep,
        *,
        event: CustomStoryWorkflowEventEntity | None = None,
    ) -> CustomStoryBatchJobEntity:
        prompt, max_tokens, temperature = await self._build_text_batch_prompt(runner, workflow, step)
        step_input = self._step_input(workflow, step)
        existing_job = await self._active_delayed_batch_job(workflow, step)
        if existing_job is not None:
            await self._record_submitted_batch_step(
                workflow,
                step,
                step_input,
                existing_job,
                "Existing text batch job is still active; event reused it instead of submitting a duplicate.",
            )
            return existing_job
        step_record = await self.steps.create(workflow.id, step.value)
        step_record.status = StepStatus.IN_PROGRESS
        step_record.started_at = datetime.now(UTC)
        step_record.input_json = step_input
        step_record.prompt = prompt
        await self.steps.update(step_record)
        await self.session.commit()

        job_type = self._job_type_for_step(step)
        batch_runner = self._batch_runner(workflow)
        model = workflow.text_model or settings.GOOGLE_TEXT_MODEL
        request_key = step.value
        request = types.InlinedRequest(
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=prompt)],
                )
            ],
            metadata={"key": request_key},
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
                response_mime_type="application/json",
            ),
        )
        job = await self.batch_jobs.create(
            workflow_id=workflow.id,
            story_id=None,
            generic_story_id=getattr(workflow, "generic_story_id", None),
            job_type=job_type,
            attempt=1,
            expected_item_count=1,
            request_keys=[request_key],
            provider_model=model,
            request_payload={
                "mode": "text",
                "workflow_step": step.value,
                "attempt": 1,
                "generation_config": {
                    "model": model,
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                },
                "event_id": str(event.id) if event is not None else None,
                "retry_flag": bool(getattr(event, "retry_flag", False)) if event is not None else False,
                "retry_comment": getattr(event, "retry_comment", None) if event is not None else None,
                "retry_source_event_id": (
                    str(event.retry_source_event_id)
                    if event is not None and getattr(event, "retry_source_event_id", None)
                    else None
                ),
                "items": [{"key": request_key, "prompt": prompt}],
            },
        )
        await self.session.commit()
        try:
            provider_job = await batch_runner.google_client.aio.batches.create(
                model=model,
                src=[request],
                config={"display_name": f"custom-{workflow.id}-{step.value.lower()}-attempt-1"},
            )
            job.provider_job_name = provider_job.name
            job.provider_state = batch_runner._job_state_name(provider_job)
            await self.batch_jobs.update(job)
            await self._record_submitted_batch_step(
                workflow,
                step,
                step_input,
                job,
                "Text batch submitted; reconcile scheduler will process results.",
            )
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = step.value
            await self.workflows.update(workflow)
            await self.session.commit()
            return job
        except Exception as exc:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            await self.batch_jobs.update(job)
            step_record.status = StepStatus.FAILED
            step_record.error_message = str(exc)
            step_record.completed_at = datetime.now(UTC)
            await self.steps.update(step_record)
            await self.session.commit()
            raise

    async def _build_text_batch_prompt(
        self,
        runner: StoryService,
        workflow: CustomStoryWorkflowEntity,
        step: CustomStoryWorkflowStep,
    ) -> tuple[str, int, float]:
        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            child = await self._prompt_child_context(workflow)
            if child is None and self._use_child_character_enabled(workflow):
                raise NotFoundException("Child profile not found during plan generation")
            template = load_prompt(runner._story_plan_prompt_path(workflow))
            source_inputs = _story_source_inputs(workflow)
            character_context = runner._build_story_cast_context(workflow, child)
            prompt = runner._render_story_plan_prompt(
                template,
                story=workflow,
                child=child,
                source_inputs=source_inputs,
                theme=source_inputs["category"],
                hobby=runner._get_hobby_for_age_group(workflow.age_group),
                pages=runner._get_page_count_for_age_group(workflow.age_group),
                character_context=character_context,
            )
            return prompt, runner.PLAN_MAX_TOKENS, 0.4

        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            template = load_prompt(runner._story_generation_prompt_path(workflow))
            prompt_plan = runner._build_story_generation_context(
                workflow.story_plan_json or {},
                languages=self._workflow_languages(workflow),
            )
            prompt = template.replace("{story_plan_json}", _compact_json(prompt_plan))
            return prompt, runner._story_max_tokens_for_languages(workflow.age_group, self._workflow_languages(workflow)), 0.7

        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            child = await self._prompt_child_context(workflow)
            if child is None and self._use_child_character_enabled(workflow):
                raise NotFoundException("Child profile not found during image plan generation")
            character_context = runner._build_story_cast_context(workflow, child, story_plan=workflow.story_plan_json or {})
            compact_story_plan, compact_story_json = runner._build_image_plan_context(
                workflow.story_plan_json or {},
                workflow.story_json or {},
            )
            prompt = runner._custom_safe_image_plan_prompt(compact_story_plan, compact_story_json, character_context)
            return prompt, runner._image_plan_max_tokens(workflow.age_group), 0.2

        raise AppException(f"Unsupported text batch step: {step}", code="CUSTOM_STORY_TEXT_BATCH_STEP_INVALID")

    async def _prompt_child_context(self, workflow: CustomStoryWorkflowEntity) -> Any:
        if workflow.child_id is not None:
            child = await self.children.get_for_user(workflow.user_id, workflow.child_id)
            if child is not None:
                return child
        if self._use_child_character_enabled(workflow):
            return None
        return SimpleNamespace(first_name="Story Hero", gender="neutral")

    async def run(self, workflow_id: UUID) -> CustomStoryWorkflowEntity:
        workflow = await self.workflows.get_by_id_for_update(workflow_id)
        if workflow is None:
            raise NotFoundException("Custom story workflow not found")
        if self._status_value(workflow.status) == CustomStoryWorkflowStatus.COMPLETED.value:
            return workflow
        if (
            self._status_value(workflow.status) == CustomStoryWorkflowStatus.FAILED.value
            and await self._failed_delayed_batch_job(workflow) is not None
        ):
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
                if self._step_disabled_by_request(workflow, step):
                    if not await self._step_has_completed_record(workflow, step):
                        await self._execute_step(runner, workflow, step)
                        await self.workflows.update(workflow)
                        await self.session.commit()
                    continue
                if step == CustomStoryWorkflowStep.PUBLISH_STORY and not await self._delayed_outputs_completed(workflow):
                    failed_job = await self._failed_delayed_batch_job(workflow)
                    if failed_job is not None:
                        await self._mark_workflow_failed(
                            workflow,
                            failed_job.error_message
                            or f"{self._status_value(failed_job.job_type)} batch job failed",
                        )
                    else:
                        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                        workflow.current_step = await self._delayed_waiting_step(workflow)
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    return workflow
                if await self._step_is_complete(workflow, step):
                    continue
                await self._execute_step(runner, workflow, step)
                await self.workflows.update(workflow)
                await self.session.commit()

            workflow.status = CustomStoryWorkflowStatus.COMPLETED
            workflow.current_step = None
            await self.workflows.update(workflow)
            await self.session.commit()
            await self._send_completion_notifications(workflow)
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
        workflow: CustomStoryWorkflowEntity,
        step: CustomStoryWorkflowStep,
    ) -> None:
        workflow.current_step = step.value
        workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
        await self.workflows.update(workflow)
        await self.session.commit()
        flags = self._flags(workflow)
        step_input = self._step_input(workflow, step)
        logger.info(
            "[CUSTOM_WORKFLOW_STEP] workflow=%s step=%s action=started execute_image=%s "
            "execute_narration=%s skip_validation=%s",
            workflow.id,
            step.value,
            self._execute_image_enabled(workflow),
            self._execute_narration_enabled(workflow),
            flags.skip_validation,
        )

        if self._step_disabled_by_request(workflow, step):
            output = {"skipped": True, "message": f"{step.value} skipped by request"}
            if step == CustomStoryWorkflowStep.IMAGE_GENERATION:
                await runner._create_pages_without_images(workflow, workflow.story_json or {})
                output = {"images_skipped": True, "skipped": True, "message": "Image generation skipped by request"}
            elif step == CustomStoryWorkflowStep.NARRATION_GENERATION:
                output = {
                    "narration_skipped": True,
                    "skipped": True,
                    "message": "Narration generation skipped by request",
                }
            await self._record_completed_step(workflow, step, step_input, output)
            logger.info(
                "[CUSTOM_WORKFLOW_STEP] workflow=%s step=%s action=skipped",
                workflow.id,
                step.value,
            )
            return

        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            try:
                story_plan = await runner._step_generate_plan(workflow, flags)
                workflow.story_plan_json = story_plan
                workflow.story_plan_validated = False
                await self._annotate_latest_step(workflow, step, step_input, story_plan)
                return
            except Exception as exc:
                self._log_step_failed(workflow, step, exc)
                raise

        if step == CustomStoryWorkflowStep.STORY_PLAN_VALIDATION:
            if flags.skip_validation:
                workflow.story_plan_validated = True
                await self._record_completed_step(workflow, step, step_input, workflow.story_plan_json or {})
                return
            try:
                story_plan = await runner._step_validate_plan(workflow, workflow.story_plan_json or {}, flags)
                workflow.story_plan_json = story_plan
                workflow.story_plan_validated = True
                await self._annotate_latest_step(workflow, step, step_input, story_plan)
                return
            except Exception as exc:
                self._log_step_failed(workflow, step, exc)
                raise

        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            try:
                story_json = await runner._step_generate_story(workflow, workflow.story_plan_json or {}, flags)
                runner._apply_story_metadata(workflow, workflow.story_plan_json or {}, story_json)
                story_json["use_child_character"] = workflow.use_child_character
                workflow.story_json = story_json
                await self._annotate_latest_step(workflow, step, step_input, story_json)
                return
            except Exception as exc:
                self._log_step_failed(workflow, step, exc)
                raise

        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            try:
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
            except Exception as exc:
                self._log_step_failed(workflow, step, exc)
                raise

        if step == CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION:
            if flags.skip_validation:
                if not flags.skip_image_generation:
                    workflow.image_plan_json = await runner._ensure_image_plan_character_references(
                        workflow,
                        workflow.image_plan_json or {},
                    )
                workflow.image_plan_validated = True
                await self._record_completed_step(workflow, step, step_input, workflow.image_plan_json or {})
                return
            try:
                image_plan = await runner._step_validate_image_plan(
                    workflow,
                    workflow.image_plan_json or {},
                    workflow.story_json or {},
                    flags,
                )
                if not flags.skip_image_generation:
                    image_plan = await runner._ensure_image_plan_character_references(workflow, image_plan)
                workflow.image_plan_json = image_plan
                workflow.image_plan_validated = True
                await self._annotate_latest_step(workflow, step, step_input, image_plan)
                return
            except Exception as exc:
                self._log_step_failed(workflow, step, exc)
                raise

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
            existing_job = await self._active_delayed_batch_job(workflow, step)
            if existing_job is not None:
                await self._record_submitted_batch_step(
                    workflow,
                    step,
                    step_input,
                    existing_job,
                    "Existing image batch job is still active; retry reused it instead of submitting a duplicate.",
                )
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = step.value
                return
            batch_runner = self._batch_runner(workflow)
            job = await batch_runner._step_submit_images_batch(
                workflow,
                workflow.story_json or {},
                workflow.image_plan_json or {},
            )
            if job is not None:
                await self._record_submitted_batch_step(
                    workflow,
                    step,
                    step_input,
                    job,
                    "Image batch submitted; reconcile scheduler will process results.",
                )
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = step.value
            return

        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            if not hasattr(self, "events"):
                batch_runner = self._batch_runner(workflow)
                existing_job = await self._active_delayed_batch_job(workflow, step)
                if existing_job is not None:
                    await self._record_submitted_batch_step(
                        workflow,
                        step,
                        step_input,
                        existing_job,
                        "Existing narration batch job is still active; retry reused it instead of submitting a duplicate.",
                    )
                    workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                    workflow.current_step = step.value
                    return
                message = await batch_runner._ensure_audio_batch_submitted(workflow)
                latest_job = await self.batch_jobs.latest_for_workflow_type(
                    workflow.id,
                    self._job_type_for_step(step),
                )
                if latest_job is not None and self._status_value(latest_job.status) in {
                    StoryBatchJobStatus.SUBMITTED.value,
                    StoryBatchJobStatus.RUNNING.value,
                }:
                    await self._record_submitted_batch_step(workflow, step, step_input, latest_job, message)
                elif self._story_has_audio(workflow.story_json or {}):
                    await self._record_completed_step(
                        workflow,
                        step,
                        step_input,
                        {"narration_generated": True, "message": message},
                    )
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = step.value
                return
            if self._workflow_has_audio_for_all_languages(workflow):
                await self._record_completed_step(
                    workflow,
                    step,
                    step_input,
                    {"narration_generated": True, "languages": self._workflow_languages(workflow)},
                )
            else:
                await self._enqueue_narration_language_events(workflow)
                step_record = await self.steps.latest_for_workflow_step(workflow.id, step)
                if step_record is None:
                    step_record = await self.steps.create(workflow.id, step.value)
                step_record.status = StepStatus.SUBMITTED_BATCH_JOB
                step_record.started_at = step_record.started_at or datetime.now(UTC)
                step_record.completed_at = None
                step_record.input_json = step_input
                step_record.output_json = {
                    "deferred": True,
                    "mode": "language_scoped_audio_events",
                    "languages": self._workflow_languages(workflow),
                }
                step_record.error_message = None
                await self.steps.update(step_record)
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = step.value
            return

        if step == CustomStoryWorkflowStep.PUBLISH_STORY:
            await self._publish_story(workflow)
            await self._record_completed_step(
                workflow,
                step,
                step_input,
                {
                    "story_id": str(workflow.story_id) if workflow.story_id else None,
                    "generic_story_id": (
                        str(workflow.generic_story_id) if getattr(workflow, "generic_story_id", None) else None
                    ),
                },
            )
            return

        raise AppException(f"Unsupported custom story workflow step: {step}", code="CUSTOM_STORY_STEP_INVALID")

    def _story_runner(self, workflow: CustomStoryWorkflowEntity) -> StoryService:
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

    def _batch_runner(self, workflow: CustomStoryWorkflowEntity) -> StoryServiceBatchService:
        batch_runner = StoryServiceBatchService(self.session)
        batch_runner.batch_jobs = _WorkflowBatchJobs(self.batch_jobs, workflow)
        batch_runner.story_steps = self.steps
        batch_runner.story_pages = _WorkflowPageBuffer(workflow)
        batch_runner.stories = _WorkflowStoryStore(self.workflows)
        batch_runner.workflow = self._story_runner(workflow)
        return batch_runner

    async def _publish_story(self, workflow: CustomStoryWorkflowEntity) -> None:
        if self._is_generic_workflow(workflow):
            await self._publish_generic_story(workflow)
            return
        if workflow.story_id is not None:
            return
        if workflow.child_id is None:
            raise AppException("Custom story workflow is missing child_id", code="CUSTOM_STORY_CHILD_REQUIRED")
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        if self._execute_narration_enabled(workflow) and not self._workflow_has_audio_for_all_languages(workflow):
            raise AppException(
                "Cannot publish custom story before narration audio is complete for every page.",
                code="CUSTOM_STORY_AUDIO_INCOMPLETE",
            )
        story = await self.stories.create(
            user_id=workflow.user_id,
            child_id=workflow.child_id,
            generation_mode="INPUT_DRIVEN",
            age_group=workflow.age_group.value,
            category=workflow.category,
            learning_goal=workflow.learning_goal,
            context=workflow.context,
            event_description=None,
            input_request=self._request_snapshot_from_columns(workflow),
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
        story_json = _sync_story_media_to_language_variants(story_json, include_audio=True)
        workflow.story_json = story_json
        for language, variant_json in self._story_json_variants_for_publish(workflow).items():
            await self.stories.upsert_content(story, language=language, story_json=variant_json)
        await self._publish_pages(story.id, story_json)
        await self.stories.update(story)
        workflow.story_id = story.id
        workflow.story_json = story_json
        for job in await self.batch_jobs.list_by_workflow(workflow.id):
            job.story_id = story.id
            await self.batch_jobs.update(job)
        await self.workflows.update(workflow)

    async def _send_completion_notifications(self, workflow: CustomStoryWorkflowEntity) -> None:
        if self._is_generic_workflow(workflow):
            return
        if workflow.story_id is None:
            logger.warning(
                "[CUSTOM_WORKFLOW_NOTIFY] workflow=%s action=skipped reason=no_story_id",
                workflow.id,
            )
            return
        story = await self.stories.get_by_id(workflow.story_id)
        if story is None:
            logger.warning(
                "[CUSTOM_WORKFLOW_NOTIFY] workflow=%s story_id=%s action=skipped reason=story_not_found",
                workflow.id,
                workflow.story_id,
            )
            return
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else None
        try:
            await StoryCompletionEmailService(self.session).send_story_completed(story, story_json)
            logger.info(
                "[CUSTOM_WORKFLOW_NOTIFY] workflow=%s story_id=%s action=sent",
                workflow.id,
                workflow.story_id,
            )
        except Exception:
            logger.exception(
                "[CUSTOM_WORKFLOW_NOTIFY] workflow=%s story_id=%s action=failed",
                workflow.id,
                workflow.story_id,
            )

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
            webp_bytes = ImageWebPConverter.convert_to_webp(image_bytes, quality=85)
            webp_filename = filename.replace(".png", ".webp")
            return await image_storage.save_story_image(story_id, webp_bytes, webp_filename, "")
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
        workflow: CustomStoryWorkflowEntity,
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
        logger.info(
            "[CUSTOM_WORKFLOW_STEP] workflow=%s step=%s action=completed output=%s",
            workflow.id,
            step_name.value,
            self._step_output_summary(output),
        )

    @staticmethod
    def _log_step_failed(workflow: CustomStoryWorkflowEntity, step: CustomStoryWorkflowStep, exc: Exception) -> None:
        logger.error(
            "[CUSTOM_WORKFLOW_STEP] workflow=%s step=%s action=failed error=%s",
            workflow.id,
            step.value,
            str(exc),
        )

    async def _annotate_latest_step(
        self,
        workflow: CustomStoryWorkflowEntity,
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
        logger.info(
            "[CUSTOM_WORKFLOW_STEP] workflow=%s step=%s action=completed output=%s",
            workflow.id,
            step_name.value,
            self._step_output_summary(output),
        )


    async def cancel_batch_job(
        self,
        *,
        user_id: UUID,
        workflow_id: UUID,
        batch_job_id: UUID,
    ) -> dict[str, Any]:
        """Cancel a submitted Google Batch job for custom workflow and update local tracking."""
        workflow = await self._get_owned(user_id, workflow_id)
        job = await self.batch_jobs.get_by_id(batch_job_id)
        if job is None:
            raise NotFoundException("Batch job not found", "BATCH_JOB_NOT_FOUND")
        if job.workflow_id != workflow_id:
            raise NotFoundException("Batch job does not belong to this workflow", "BATCH_JOB_NOT_FOUND")

        if job.status == StoryBatchJobStatus.SUCCEEDED:
            raise AppException(
                "Completed batch jobs cannot be cancelled",
                status.HTTP_409_CONFLICT,
                "BATCH_JOB_ALREADY_COMPLETED",
            )

        if job.status == StoryBatchJobStatus.CANCELLED:
            return self._batch_job_cancel_response(workflow, job, "Batch job was already cancelled")

        if not job.provider_job_name:
            raise AppException(
                "Batch job has not been submitted to Google yet",
                status.HTTP_409_CONFLICT,
                "BATCH_JOB_NOT_SUBMITTED",
            )

        try:
            batch_runner = self._batch_runner(workflow)
            await batch_runner.google_client.aio.batches.cancel(name=job.provider_job_name)
            provider_job = await batch_runner.google_client.aio.batches.get(name=job.provider_job_name)
            provider_state = batch_runner._job_state_name(provider_job)
        except Exception as exc:
            raise AppException(
                f"Failed to cancel Google batch job: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "GOOGLE_BATCH_CANCEL_FAILED",
            ) from exc

        job.status = StoryBatchJobStatus.CANCELLED
        job.provider_state = provider_state or "CANCEL_REQUESTED"
        job.error_message = "Cancelled by user request"
        if job.request_keys:
            job.missing_keys = job.request_keys
        await self.batch_jobs.update(job)

        if workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS:
            workflow.status = CustomStoryWorkflowStatus.FAILED
            workflow.current_step = None
            workflow.error_message = f"Batch {self._status_value(job.job_type)} job cancelled by user request"
            await self.workflows.update(workflow)

        await self.session.commit()
        return self._batch_job_cancel_response(workflow, job, "Batch job cancelled successfully")

    async def reconcile_batch_jobs(self, *, limit: int = 50) -> dict[str, Any]:
        jobs = await self.batch_jobs.list_reconcilable(limit=limit)
        results: list[dict[str, Any]] = []
        processed_count = 0
        self._print_reconcile_event("started", job_count=len(jobs), limit=limit)
        for job in jobs:
            job_result = {
                "workflow_id": job.workflow_id,
                "story_id": job.story_id,
                "batch_job_id": job.id,
                "job_type": self._status_value(job.job_type),
                "status": self._status_value(job.status),
                "provider_state": job.provider_state,
            }
            try:
                self._print_reconcile_event(
                    "job_started",
                    workflow_id=job.workflow_id,
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=self._status_value(job.job_type),
                    status=self._status_value(job.status),
                    provider_state=job.provider_state,
                )
                result = await self._reconcile_batch_job(job)
                if result["action"] not in {"still_running", "skipped"}:
                    processed_count += 1
                results.append(result)
                self._print_reconcile_event(
                    "job_completed",
                    workflow_id=job.workflow_id,
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=self._status_value(job.job_type),
                    action=result.get("action"),
                    status=result.get("status"),
                    provider_state=result.get("provider_state"),
                    message=result.get("message"),
                )
            except Exception as exc:
                await self.session.rollback()
                self._print_reconcile_event(
                    "job_error",
                    workflow_id=job.workflow_id,
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=self._status_value(job.job_type),
                    status=self._status_value(job.status),
                    provider_state=job.provider_state,
                    error=str(exc),
                )
                results.append(
                    {
                        **job_result,
                        "action": "error",
                        "message": str(exc),
                    }
                )
        self._print_reconcile_event("completed", checked_count=len(jobs), processed_count=processed_count)
        return {"checked_count": len(jobs), "processed_count": processed_count, "results": results}

    async def _reconcile_batch_job(self, job: CustomStoryBatchJobEntity) -> dict[str, Any]:
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
        self._print_reconcile_event(
            "google_batch_status",
            workflow_id=job.workflow_id,
            story_id=job.story_id,
            batch_job_id=job.id,
            job_type=self._status_value(job.job_type),
            provider_job_name=job.provider_job_name,
            provider_state=state_name,
        )

        if state_name in batch_runner.SUCCEEDED_STATES:
            if job.job_type in {
                StoryBatchJobType.STORY_PLAN,
                StoryBatchJobType.STORY,
                StoryBatchJobType.IMAGE_PLAN,
            }:
                self._print_reconcile_event(
                    "processing_text_batch_started",
                    workflow_id=workflow.id,
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=self._status_value(job.job_type),
                )
                await self._process_reconciled_text_job(workflow, job, provider_job, batch_runner)
            elif job.job_type == StoryBatchJobType.IMAGE:
                self._print_reconcile_event(
                    "processing_image_batch_started",
                    workflow_id=workflow.id,
                    story_id=job.story_id,
                    batch_job_id=job.id,
                )
                image_retry_submitted = await self._process_reconciled_image_job(workflow, job, provider_job, batch_runner)
                if image_retry_submitted:
                    return self._batch_reconcile_result(
                        job,
                        "retry_submitted",
                        "Image batch had missing or invalid pages; submitted a retry batch",
                    )
            elif job.job_type == StoryBatchJobType.AUDIO:
                self._print_reconcile_event(
                    "processing_narration_batch_started",
                    workflow_id=workflow.id,
                    story_id=job.story_id,
                    batch_job_id=job.id,
                )
                audio_retry_submitted = await self._process_reconciled_audio_job(
                    workflow,
                    job,
                    provider_job,
                    batch_runner,
                )
                if audio_retry_submitted:
                    return self._batch_reconcile_result(
                        job,
                        "retry_submitted",
                        "Audio batch had missing pages; submitted a retry for missing keys only",
                    )

            if self._status_value(job.status) == StoryBatchJobStatus.FAILED.value:
                return self._batch_reconcile_result(job, "failed", job.error_message)
            if job.job_type == StoryBatchJobType.AUDIO and not self._workflow_has_audio_for_all_languages(workflow):
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = CustomStoryWorkflowStep.NARRATION_GENERATION.value
                await self.workflows.update(workflow)
                await self.session.commit()
                return self._batch_reconcile_result(job, "processed", "Waiting for remaining audio retry batch")
            next_step = await self._enqueue_next_step(workflow, self._step_for_job_type(job.job_type))
            await self.session.commit()
            return self._batch_reconcile_result(job, "processed", "Custom story workflow batch job processed")

        if state_name in batch_runner.CANCELLED_STATES:
            job.status = StoryBatchJobStatus.CANCELLED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_batch_step_failed(workflow, job)
            await self._mark_batch_event_failed(workflow, job, job.error_message)
            await self._mark_workflow_failed(workflow, job.error_message)
            await self.session.commit()
            return self._batch_reconcile_result(job, "cancelled", job.error_message)

        if state_name in batch_runner.FAILED_STATES:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_batch_step_failed(workflow, job)
            await self._mark_batch_event_failed(workflow, job, job.error_message)
            await self._mark_workflow_failed(workflow, job.error_message)
            await self.session.commit()
            return self._batch_reconcile_result(job, "failed", job.error_message)

        job.status = StoryBatchJobStatus.RUNNING
        await self.batch_jobs.update(job)
        await self.session.commit()
        self._print_reconcile_event(
            "job_still_running",
            workflow_id=job.workflow_id,
            story_id=job.story_id,
            batch_job_id=job.id,
            job_type=self._status_value(job.job_type),
            provider_state=state_name,
        )
        return self._batch_reconcile_result(job, "still_running", f"Provider state is {state_name}")

    async def _process_reconciled_text_job(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        provider_job: Any,
        batch_runner: StoryServiceBatchService,
    ) -> None:
        step_name = self._step_for_job_type(job.job_type)
        event = await self._batch_submitted_event_for_job(
            workflow_id=workflow.id,
            step_name=step_name,
            batch_job_id=job.id,
        )
        responses = list(getattr(getattr(provider_job, "dest", None), "inlined_responses", None) or [])
        responses_by_key = batch_runner._responses_by_key(responses)
        request_keys = list(job.request_keys or [step_name.value])
        response = responses_by_key.get(request_keys[0]) if request_keys else None
        if response is None:
            job.status = StoryBatchJobStatus.FAILED
            job.completed_item_count = 0
            job.failed_item_count = 1
            job.missing_keys = request_keys
            job.error_message = f"Missing text response for {step_name.value}"
            await self.batch_jobs.update(job)
            await self._handle_text_batch_failure(
                workflow,
                job,
                event,
                step_name,
                error_message=job.error_message,
                raw_text=None,
                provider_response=batch_runner._model_dump_safe(provider_job),
            )
            await self.session.commit()
            return

        text = self._extract_text_from_inlined_response(response)
        if not text:
            job.status = StoryBatchJobStatus.FAILED
            job.completed_item_count = 0
            job.failed_item_count = 1
            job.missing_keys = request_keys
            job.response_payload = {"response": batch_runner._model_dump_safe(response)}
            job.error_message = f"Empty text response for {step_name.value}"
            await self.batch_jobs.update(job)
            await self._handle_text_batch_failure(
                workflow,
                job,
                event,
                step_name,
                error_message=job.error_message,
                raw_text="",
                provider_response=batch_runner._model_dump_safe(response),
            )
            await self.session.commit()
            return

        runner = self._story_runner(workflow)
        try:
            if step_name == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
                payload = json.loads(_repair_json(text))
                payload["source_inputs"] = _story_source_inputs(workflow)
                workflow.story_plan_json = payload
                workflow.story_plan_validated = False
                output = payload
            elif step_name == CustomStoryWorkflowStep.STORY_GENERATION:
                raw_story_json = runner._parse_story_generation_text(text)
                payload = _normalize_story_output(raw_story_json, workflow.story_plan_json or {}, workflow)
                runner._apply_story_metadata(workflow, workflow.story_plan_json or {}, payload)
                payload["use_child_character"] = workflow.use_child_character
                workflow.story_json = payload
                output = payload
            elif step_name == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
                payload = json.loads(_repair_json(text))
                runner._validate_image_plan_page_contract(payload, workflow.story_json or {})
                workflow.image_plan_json = payload
                workflow.image_plan_validated = False
                output = payload
            else:
                raise AppException(
                    f"Unsupported text batch job type: {self._status_value(job.job_type)}",
                    code="CUSTOM_STORY_TEXT_BATCH_JOB_INVALID",
                )
        except Exception as exc:
            job.status = StoryBatchJobStatus.FAILED
            job.completed_item_count = 0
            job.failed_item_count = 1
            job.missing_keys = request_keys
            job.response_payload = {"text": text, "response": batch_runner._model_dump_safe(response)}
            job.error_message = str(exc)
            await self.batch_jobs.update(job)
            await self._handle_text_batch_failure(
                workflow,
                job,
                event,
                step_name,
                error_message=job.error_message,
                raw_text=text,
                provider_response=batch_runner._model_dump_safe(response),
            )
            await self.session.commit()
            return

        job.status = StoryBatchJobStatus.SUCCEEDED
        job.completed_item_count = 1
        job.failed_item_count = 0
        job.missing_keys = []
        job.response_payload = {"text": text, "response": batch_runner._model_dump_safe(response)}
        job.error_message = None
        await self.batch_jobs.update(job)

        step = await self.steps.latest_for_workflow_step(workflow.id, step_name)
        if step is None:
            step = await self.steps.create(workflow.id, step_name.value)
        step.status = StepStatus.COMPLETED
        step.started_at = step.started_at or datetime.now(UTC)
        step.completed_at = datetime.now(UTC)
        step.error_message = None
        step.output_json = output
        await self.steps.update(step)
        if event is not None:
            await self._complete_event(
                event,
                {
                    "batch_job_id": str(job.id),
                    "provider_job_name": job.provider_job_name,
                    "job_type": self._status_value(job.job_type),
                },
            )
        await self.workflows.update(workflow)

        await self.session.commit()
        self._print_reconcile_event(
            "text_batch_processed",
            workflow_id=workflow.id,
            story_id=job.story_id,
            batch_job_id=job.id,
            job_type=self._status_value(job.job_type),
            status=self._status_value(job.status),
        )

    async def _publish_generic_story(self, workflow: CustomStoryWorkflowEntity) -> None:
        if workflow.generic_story_id is not None:
            return
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        if self._execute_narration_enabled(workflow) and not self._workflow_has_audio_for_all_languages(workflow):
            raise AppException(
                "Cannot publish generic story before narration audio is complete for every page.",
                code="GENERIC_STORY_AUDIO_INCOMPLETE",
            )

        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        title = await self._generic_publish_title(workflow, story_json)
        generic_story = await self.generic_stories.create(
            title=title,
            summary=workflow.summary or story_json.get("summary"),
            age_group=self._status_value(workflow.age_group),
            theme=workflow.category,
            genre=None,
            moral=workflow.moral,
            learning_goal=workflow.learning_goal,
            reading_time_minutes=self._reading_time_minutes(pages),
            character_type="AI-created",
            total_pages=len(pages),
            cover_image=story_json.get("cover_image_url"),
            status=workflow.publish_status or "inactive",
        )
        story_json = await self._copy_story_images_to_final_story_storage(story_json, generic_story.id)
        workflow.image_plan_json = await self._copy_character_references_to_final_story_storage(
            workflow.image_plan_json if isinstance(workflow.image_plan_json, dict) else {},
            generic_story.id,
        )
        story_json = _sync_story_media_to_language_variants(story_json, include_audio=True)
        workflow.story_json = story_json
        generic_story.cover_image = story_json.get("cover_image_url")
        await self.generic_stories.upsert_contents(
            generic_story,
            [
                {
                    "language": language,
                    "story_json": variant_json,
                }
                for language, variant_json in self._story_json_variants_for_publish(workflow).items()
            ],
        )
        workflow.generic_story_id = generic_story.id
        workflow.story_json = story_json
        for job in await self.batch_jobs.list_by_workflow(workflow.id):
            job.generic_story_id = generic_story.id
            await self.batch_jobs.update(job)
        await self.generic_stories.flush()
        await self.workflows.update(workflow)

    async def _copy_character_references_to_final_story_storage(
        self,
        image_plan: dict[str, Any],
        story_id: UUID,
    ) -> dict[str, Any]:
        if not isinstance(image_plan, dict):
            return image_plan
        image_storage = get_image_storage_service()
        updated = dict(image_plan)
        manifest = StoryService._character_reference_manifest(updated)
        visual_bible = updated.get("visual_bible") if isinstance(updated.get("visual_bible"), dict) else {}
        characters = self._visual_bible_reference_characters(visual_bible)

        for item in manifest:
            if not isinstance(item, dict):
                continue
            character_id = str(item.get("character_id") or "").strip()
            reference_url = str(item.get("reference_image_url") or item.get("image_url") or "").strip()
            if not character_id or not reference_url:
                continue
            copied_url = await self._copy_story_image_url(
                image_storage,
                image_url=reference_url,
                story_id=story_id,
                filename=f"character_ref_{character_id}.png",
            )
            if not copied_url:
                continue
            item["reference_image_url"] = copied_url
            item["persistent_reference_image_url"] = copied_url
            for character in characters:
                if self._same_character_reference(character, item):
                    character["reference_image_url"] = copied_url
                    character["persistent_reference_image_url"] = copied_url
        return updated

    @staticmethod
    def _visual_bible_reference_characters(visual_bible: dict[str, Any]) -> list[dict[str, Any]]:
        characters: list[dict[str, Any]] = []
        hero = visual_bible.get("hero") if isinstance(visual_bible.get("hero"), dict) else None
        if hero is not None:
            characters.append(hero)
        recurring = visual_bible.get("recurring_characters")
        if isinstance(recurring, list):
            characters.extend(character for character in recurring if isinstance(character, dict))
        return characters

    @staticmethod
    def _same_character_reference(character: dict[str, Any], reference: dict[str, Any]) -> bool:
        character_id = str(character.get("character_id") or "").strip()
        reference_id = str(reference.get("character_id") or "").strip()
        if character_id and reference_id and character_id == reference_id:
            return True
        character_name = StoryService._character_reference_name_key(str(character.get("name") or ""))
        reference_name = StoryService._character_reference_name_key(str(reference.get("name") or ""))
        return bool(character_name and reference_name and character_name == reference_name)

    async def _generic_publish_title(self, workflow: CustomStoryWorkflowEntity, story_json: dict[str, Any]) -> str:
        base_title = (
            workflow.title
            or story_json.get("title")
            or f"Generic Story {workflow.request_number}"
        )
        title = str(base_title).strip()[:255] or f"Generic Story {workflow.request_number}"
        existing = await self.generic_stories.get_by_title(title)
        if existing is None:
            return title
        suffix = f" ({workflow.request_number})"
        return f"{title[: 255 - len(suffix)]}{suffix}"

    @staticmethod
    def _reading_time_minutes(pages: list[Any]) -> int:
        word_count = 0
        for page in pages:
            if isinstance(page, dict):
                word_count += len(str(page.get("text") or "").split())
        return max(1, round(word_count / 120)) if word_count else max(1, len(pages) // 2)

    async def _handle_text_batch_failure(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        event: CustomStoryWorkflowEventEntity | None,
        step_name: CustomStoryWorkflowStep,
        *,
        error_message: str,
        raw_text: str | None,
        provider_response: Any | None,
    ) -> None:
        diagnostics = self._text_batch_failure_diagnostics(provider_response)
        diagnostics["request_keys"] = list(getattr(job, "request_keys", None) or [])
        diagnostics["missing_keys"] = list(getattr(job, "missing_keys", None) or [])
        job.response_payload = {
            "text": raw_text,
            "response": provider_response,
            "error": error_message,
            "diagnostics": diagnostics,
        }
        await self.batch_jobs.update(job)
        await self._mark_batch_step_failed(workflow, job)
        if event is not None:
            event.status = CustomStoryWorkflowEventStatus.FAILED
            event.error_message = error_message
            event.completed_at = datetime.now(UTC)
            metadata = dict(event.metadata_json or {})
            metadata.update(
                {
                    "batch_job_id": str(job.id),
                    "provider_job_name": job.provider_job_name,
                    "job_type": self._status_value(job.job_type),
                    "failure_error": error_message,
                }
            )
            event.metadata_json = metadata
            await self.events.update(event)

        self._log_text_batch_failure(
            workflow,
            job,
            event,
            step_name,
            error_message=error_message,
            raw_text=raw_text,
            diagnostics=diagnostics,
        )

        if step_name in {
            CustomStoryWorkflowStep.STORY_PLAN_GENERATION,
            CustomStoryWorkflowStep.STORY_GENERATION,
            CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        } and not self._text_batch_retry_used(event):
            retry_event = await self.events.create(
                workflow_id=workflow.id,
                step_name=step_name,
                retry_count=(int(getattr(event, "retry_count", 0) or 0) + 1) if event is not None else 1,
                retry_flag=True,
                retry_comment="FULL_BATCH_RETRY",
                retry_source_event_id=getattr(event, "id", None),
                metadata_json={
                    "retry_reason": error_message,
                    "source_batch_job_id": str(job.id),
                },
            )
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = step_name.value
            workflow.error_message = None
            await self.workflows.update(workflow)
            logger.warning(
                "[CUSTOM_TEXT_BATCH_RETRY] workflow=%s failed_event=%s retry_event=%s batch_job_id=%s reason=%s",
                workflow.id,
                getattr(event, "id", None),
                retry_event.id,
                job.id,
                error_message,
            )
            return

        await self._mark_workflow_failed(workflow, error_message)

    @staticmethod
    def _text_batch_retry_used(event: CustomStoryWorkflowEventEntity | None) -> bool:
        if event is None:
            return False
        if bool(getattr(event, "retry_flag", False)):
            return True
        return int(getattr(event, "retry_count", 0) or 0) >= 1

    def _log_text_batch_failure(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        event: CustomStoryWorkflowEventEntity | None,
        step_name: CustomStoryWorkflowStep,
        *,
        error_message: str,
        raw_text: str | None,
        diagnostics: dict[str, Any],
    ) -> None:
        logger.error(
            "[CUSTOM_TEXT_BATCH_RECONCILE_FAILED] workflow_id=%s batch_job_id=%s job_type=%s step=%s "
            "provider_job_name=%s provider_state=%s finish_reason=%s provider_error=%s "
            "event_id=%s retry_flag=%s retry_comment=%s request_keys=%s missing_keys=%s "
            "response_length=%s error=%s raw_llm_response=\n%s",
            workflow.id,
            job.id,
            self._status_value(job.job_type),
            step_name.value,
            job.provider_job_name,
            job.provider_state,
            diagnostics.get("finish_reason"),
            diagnostics.get("provider_error"),
            getattr(event, "id", None),
            bool(getattr(event, "retry_flag", False)) if event is not None else False,
            getattr(event, "retry_comment", None) if event is not None else None,
            diagnostics.get("request_keys"),
            diagnostics.get("missing_keys"),
            len(raw_text or ""),
            error_message,
            raw_text if raw_text is not None else "",
        )

    def _text_batch_failure_diagnostics(self, provider_response: Any | None) -> dict[str, Any]:
        response = provider_response if isinstance(provider_response, dict) else {}
        candidate = self._first_dict(
            response.get("response"),
            response.get("candidate"),
            response,
        )
        candidates = candidate.get("candidates") if isinstance(candidate.get("candidates"), list) else []
        first_candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        finish_reason = (
            first_candidate.get("finish_reason")
            or candidate.get("finish_reason")
            or response.get("finish_reason")
        )
        return {
            "finish_reason": str(finish_reason) if finish_reason is not None else None,
            "provider_error": self._compact_provider_error(response.get("error")),
            "request_keys": [],
            "missing_keys": [],
        }

    @staticmethod
    def _first_dict(*values: Any) -> dict[str, Any]:
        for value in values:
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _compact_provider_error(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value[:1000]
        try:
            return json.dumps(value, default=str)[:1000]
        except (TypeError, ValueError):
            return str(value)[:1000]

    @staticmethod
    def _extract_text_from_inlined_response(response: Any) -> str:
        candidate = getattr(response, "response", None)
        text = getattr(candidate, "text", None)
        if text:
            return str(text)
        parts_text: list[str] = []
        candidates = []
        if candidate is not None:
            candidates.append(candidate)
            candidates.extend(list(getattr(candidate, "candidates", None) or []))
        for item in candidates:
            content = getattr(item, "content", None)
            parts = getattr(content, "parts", None) if content is not None else getattr(item, "parts", None)
            for part in parts or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts_text.append(str(part_text))
        return "\n".join(parts_text).strip()

    async def _process_reconciled_image_job(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        provider_job: Any,
        batch_runner: StoryServiceBatchService,
    ) -> bool:
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

        source_event = await self._batch_submitted_event_for_job(
            workflow_id=workflow.id,
            step_name=CustomStoryWorkflowStep.IMAGE_GENERATION,
            batch_job_id=job.id,
        )
        retry_job = None
        retry_items = []
        retry_comment = None
        if failed_keys:
            next_attempt = int(getattr(job, "attempt", 1) or 1) + 1
            retry_keys = set(job.missing_keys or sorted(failed_keys))
            retry_comment = "PARTIAL_RETRY" if completed_keys else "FULL_BATCH_RETRY"
            if not completed_keys:
                retry_keys = {item.key for item in items}
            can_retry = bool(retry_keys) and next_attempt <= int(settings.STORY_BATCH_MAX_IMAGE_RETRIES)
            if can_retry:
                retry_items = [item for item in items if item.key in retry_keys]
                if retry_items:
                    retry_job = await batch_runner._submit_image_batch_job_only(
                        workflow,
                        retry_items,
                        attempt=next_attempt,
                    )

        step = await self.steps.latest_for_workflow_step(workflow.id, CustomStoryWorkflowStep.IMAGE_GENERATION)
        if step is None:
            step = await self.steps.create(workflow.id, CustomStoryWorkflowStep.IMAGE_GENERATION.value)
        step.status = (
            StepStatus.COMPLETED
            if not failed_keys
            else StepStatus.SUBMITTED_BATCH_JOB
            if retry_job is not None
            else StepStatus.FAILED
        )
        step.started_at = step.started_at or datetime.now(UTC)
        step.error_message = None if retry_job is not None else job.error_message
        step.output_json = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "completed_keys": sorted(completed_keys),
            "failed_keys": sorted(failed_keys),
            "response_summary": response_summary,
        }
        if retry_job is not None:
            step.output_json.update(
                {
                    "retry_submitted": True,
                    "retry_batch_job_id": str(retry_job.id),
                    "retry_provider_job_name": getattr(retry_job, "provider_job_name", None),
                    "retry_attempt": retry_job.attempt,
                    "retry_comment": retry_comment,
                    "retry_keys": [item.key for item in retry_items],
                }
            )
            step.retry_count = max(int(getattr(step, "retry_count", 0) or 0), int(retry_job.attempt or 1) - 1)
            step.completed_at = None
        else:
            step.completed_at = datetime.now(UTC)
        await self.steps.update(step)

        workflow.story_json = _sync_story_media_to_language_variants(story_json, include_audio=False)
        if retry_job is not None:
            await self._mark_event_failed(source_event, job.error_message)
            await self._create_batch_retry_event(
                workflow=workflow,
                step_name=CustomStoryWorkflowStep.IMAGE_GENERATION,
                source_event=source_event,
                retry_job=retry_job,
                retry_comment=retry_comment or "PARTIAL_RETRY",
                retry_reason=job.error_message,
            )
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = CustomStoryWorkflowStep.IMAGE_GENERATION.value
            workflow.error_message = None
        elif failed_keys:
            await self._mark_workflow_failed(workflow, job.error_message or "Image batch reconciliation failed")
            await self._mark_event_failed(source_event, job.error_message)
        await self.workflows.update(workflow)
        await self.session.commit()
        if not failed_keys:
            if source_event is not None:
                await self._complete_event(
                    source_event,
                    {
                        "batch_job_id": str(job.id),
                        "provider_job_name": job.provider_job_name,
                        "job_type": self._status_value(job.job_type),
                    },
                )
                await self.session.commit()
        self._print_reconcile_event(
            "image_batch_processed",
            workflow_id=workflow.id,
            story_id=job.story_id,
            batch_job_id=job.id,
            status=self._status_value(job.status),
            completed=len(completed_keys),
            failed=len(failed_keys),
        )
        return retry_job is not None

    async def _process_reconciled_audio_job(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        provider_job: Any,
        batch_runner: StoryServiceBatchService,
    ) -> bool:
        root_story_json = workflow.story_json if isinstance(workflow.story_json, dict) else None
        if root_story_json is None:
            raise AppException("Story JSON is missing during audio batch reconciliation", code="STORY_JSON_MISSING")
        payload = getattr(job, "request_payload", None)
        payload = payload if isinstance(payload, dict) else {}
        language = str(payload.get("language") or self._workflow_languages(workflow)[0])
        story_json = self._workflow_story_json_for_language(workflow, language)

        try:
            items = batch_runner._build_audio_items(story_json, age_group=workflow.age_group.value, language=language)
        except TypeError:
            items = batch_runner._build_audio_items(story_json, age_group=workflow.age_group.value)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]
        try:
            completed_keys, failed_keys, response_summary = await batch_runner._process_audio_batch_responses(
                workflow,
                story_json,
                items,
                provider_job,
                language=language,
            )
        except TypeError:
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
        source_event = await self._batch_submitted_event_for_job(
            workflow_id=workflow.id,
            step_name=CustomStoryWorkflowStep.NARRATION_GENERATION,
            batch_job_id=job.id,
        )
        retry_job = None
        retry_keys = set(job.missing_keys or sorted(failed_keys))
        retry_comment = "PARTIAL_RETRY" if completed_keys else "FULL_BATCH_RETRY"
        if failed_keys and not completed_keys:
            retry_keys = {item.key for item in items}
        next_attempt = int(getattr(job, "attempt", 1) or 1) + 1
        can_retry_missing = bool(retry_keys) and next_attempt <= int(settings.STORY_BATCH_MAX_AUDIO_RETRIES)
        if can_retry_missing:
            retry_items = [item for item in items if item.key in retry_keys]
            if retry_items:
                try:
                    retry_job = await batch_runner._submit_audio_batch_job_only(
                        workflow,
                        retry_items,
                        attempt=next_attempt,
                        language=language,
                    )
                except TypeError:
                    retry_job = await batch_runner._submit_audio_batch_job_only(
                        workflow,
                        retry_items,
                        attempt=next_attempt,
                    )

        step.status = (
            StepStatus.COMPLETED
            if not failed_keys
            else StepStatus.SUBMITTED_BATCH_JOB
            if retry_job is not None
            else StepStatus.FAILED
        )
        step.started_at = step.started_at or datetime.now(UTC)
        step.error_message = None if retry_job is not None else job.error_message
        step.output_json = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "language": language,
            "completed_keys": sorted(completed_keys),
            "failed_keys": sorted(failed_keys),
            "response_summary": response_summary,
        }
        if retry_job is not None:
            step.output_json.update(
                {
                    "retry_submitted": True,
                    "retry_batch_job_id": str(retry_job.id),
                    "retry_provider_job_name": getattr(retry_job, "provider_job_name", None),
                    "retry_attempt": retry_job.attempt,
                    "retry_comment": retry_comment,
                    "retry_keys": [item.key for item in retry_items],
                    "language": language,
                }
            )
            step.retry_count = max(int(getattr(step, "retry_count", 0) or 0), next_attempt - 1)
            step.completed_at = None
        else:
            step.completed_at = datetime.now(UTC)
        await self.steps.update(step)

        self._set_workflow_story_json_for_language(workflow, language, story_json)
        if retry_job is not None:
            workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
            workflow.current_step = CustomStoryWorkflowStep.NARRATION_GENERATION.value
            workflow.error_message = None
            await self._mark_event_failed(source_event, job.error_message)
            await self._create_batch_retry_event(
                workflow=workflow,
                step_name=CustomStoryWorkflowStep.NARRATION_GENERATION,
                source_event=source_event,
                retry_job=retry_job,
                retry_comment=retry_comment,
                retry_reason=job.error_message,
            )
        elif failed_keys:
            await self._mark_workflow_failed(workflow, job.error_message or "Audio batch reconciliation failed")
            await self._mark_batch_event_failed(workflow, job, job.error_message)
        else:
            if source_event is not None:
                await self._complete_event(
                    source_event,
                    {
                        "batch_job_id": str(job.id),
                        "provider_job_name": job.provider_job_name,
                        "job_type": self._status_value(job.job_type),
                        "language": language,
                    },
                )
            if self._workflow_has_audio_for_all_languages(workflow):
                step.status = StepStatus.COMPLETED
                step.completed_at = datetime.now(UTC)
                step.error_message = None
                await self.steps.update(step)
            else:
                step.status = StepStatus.SUBMITTED_BATCH_JOB
                step.completed_at = None
                await self.steps.update(step)
                workflow.status = CustomStoryWorkflowStatus.IN_PROGRESS
                workflow.current_step = CustomStoryWorkflowStep.NARRATION_GENERATION.value
        await self.workflows.update(workflow)
        await self.session.commit()
        self._print_reconcile_event(
            "narration_batch_processed",
            workflow_id=workflow.id,
            story_id=job.story_id,
            batch_job_id=job.id,
            status=self._status_value(job.status),
            completed=len(completed_keys),
            failed=len(failed_keys),
            language=language,
            retry_batch_job_id=getattr(retry_job, "id", None),
        )
        return retry_job is not None

    async def _mark_workflow_failed(self, workflow: CustomStoryWorkflowEntity, error_message: str | None) -> None:
        workflow.status = CustomStoryWorkflowStatus.FAILED
        workflow.error_message = error_message
        await self.workflows.update(workflow)

    async def _mark_batch_event_failed(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        error_message: str | None,
    ) -> None:
        if not hasattr(self, "events") or not hasattr(job, "job_type"):
            return
        step_name = self._step_for_job_type(job.job_type)
        event = await self._batch_submitted_event_for_job(
            workflow_id=workflow.id,
            step_name=step_name,
            batch_job_id=job.id,
        )
        if event is None:
            return
        event.status = CustomStoryWorkflowEventStatus.FAILED
        event.error_message = error_message
        event.completed_at = datetime.now(UTC)
        metadata = dict(event.metadata_json or {})
        metadata.update(
            {
                "batch_job_id": str(job.id),
                "provider_job_name": job.provider_job_name,
                "job_type": self._status_value(job.job_type),
                "failure_error": error_message,
            }
        )
        event.metadata_json = metadata
        await self.events.update(event)

    async def _batch_submitted_event_for_job(
        self,
        *,
        workflow_id: UUID,
        step_name: CustomStoryWorkflowStep,
        batch_job_id: UUID,
    ) -> CustomStoryWorkflowEventEntity | None:
        if not hasattr(self, "events"):
            return None
        return await self.events.batch_submitted_for_job(
            workflow_id=workflow_id,
            step_name=step_name,
            batch_job_id=batch_job_id,
        )

    async def _mark_batch_step_failed(self, workflow: CustomStoryWorkflowEntity, job: CustomStoryBatchJobEntity) -> None:
        step_name = self._step_for_job_type(job.job_type)
        step = await self.steps.latest_for_workflow_step(workflow.id, step_name)
        if step is None:
            step = await self.steps.create(workflow.id, step_name.value)
        step.status = StepStatus.FAILED
        step.started_at = step.started_at or datetime.now(UTC)
        step.completed_at = datetime.now(UTC)
        step.error_message = job.error_message
        step.output_json = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "provider_state": job.provider_state,
            "status": self._status_value(job.status),
            "message": job.error_message,
        }
        await self.steps.update(step)
        workflow.current_step = step_name.value

    async def _active_delayed_batch_job(
        self,
        workflow: CustomStoryWorkflowEntity,
        step_name: CustomStoryWorkflowStep,
        *,
        language: str | None = None,
    ) -> CustomStoryBatchJobEntity | None:
        if language and step_name == CustomStoryWorkflowStep.NARRATION_GENERATION:
            job_type = self._job_type_for_step(step_name)
            for job in reversed(await self.batch_jobs.list_active_for_workflow(workflow.id)):
                payload = job.request_payload if isinstance(job.request_payload, dict) else {}
                if job.job_type == job_type and payload.get("language") == language:
                    return job
            return None
        latest = await self.batch_jobs.latest_for_workflow_type(workflow.id, self._job_type_for_step(step_name))
        if latest is None:
            return None
        if self._status_value(latest.status) in {
            StoryBatchJobStatus.SUBMITTED.value,
            StoryBatchJobStatus.RUNNING.value,
        }:
            return latest
        return None

    async def _record_submitted_batch_step(
        self,
        workflow: CustomStoryWorkflowEntity,
        step_name: CustomStoryWorkflowStep,
        step_input: dict[str, Any] | None,
        job: CustomStoryBatchJobEntity,
        message: str,
    ) -> None:
        step = await self.steps.latest_for_workflow_step(workflow.id, step_name)
        if step is None:
            step = await self.steps.create(workflow.id, step_name.value)
        step.status = StepStatus.SUBMITTED_BATCH_JOB
        step.started_at = step.started_at or datetime.now(UTC)
        step.completed_at = None
        step.input_json = step_input
        step.output_json = {
            "deferred": True,
            "mode": "google_batch",
            "batch_job_id": str(job.id),
            "provider_job_name": job.provider_job_name,
            "provider_state": job.provider_state,
            "status": self._status_value(job.status),
            "message": message,
        }
        step.error_message = None
        await self.steps.update(step)
        logger.info(
            "[CUSTOM_WORKFLOW_STEP] workflow=%s step=%s action=submitted_batch job_id=%s provider_job=%s status=%s",
            workflow.id,
            step_name.value,
            job.id,
            job.provider_job_name,
            self._status_value(job.status),
        )

    def _batch_reconcile_result(
        self,
        job: CustomStoryBatchJobEntity,
        action: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "workflow_id": job.workflow_id,
            "story_id": job.story_id,
            "generic_story_id": getattr(job, "generic_story_id", None),
            "batch_job_id": job.id,
            "job_type": self._status_value(job.job_type),
            "status": self._status_value(job.status),
            "provider_state": job.provider_state,
            "action": action,
            "message": message,
        }

    @staticmethod
    def _batch_job_response(job: CustomStoryBatchJobEntity) -> CustomStoryWorkflowBatchJobResponse:
        return CustomStoryWorkflowBatchJobResponse(
            id=job.id,
            workflow_id=job.workflow_id,
            story_id=job.story_id,
            generic_story_id=getattr(job, "generic_story_id", None),
            job_type=CustomStoryWorkflowService._status_value(job.job_type),
            status=CustomStoryWorkflowService._status_value(job.status),
            provider=job.provider,
            provider_job_name=job.provider_job_name,
            provider_model=job.provider_model,
            provider_state=job.provider_state,
            attempt=job.attempt,
            expected_item_count=job.expected_item_count,
            completed_item_count=job.completed_item_count,
            failed_item_count=job.failed_item_count,
            request_keys=job.request_keys,
            missing_keys=job.missing_keys,
            error_message=job.error_message,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    def _batch_job_cancel_response(
        self,
        workflow: CustomStoryWorkflowEntity,
        job: CustomStoryBatchJobEntity,
        message: str,
    ) -> dict[str, Any]:
        return {
            "workflow_id": workflow.id,
            "generic_story_id": getattr(workflow, "generic_story_id", None),
            "batch_job_id": job.id,
            "job_type": self._status_value(job.job_type),
            "status": self._status_value(job.status),
            "provider_job_name": job.provider_job_name,
            "provider_state": job.provider_state,
            "workflow_status": self._status_value(workflow.status),
            "message": message,
        }

    @staticmethod
    def _job_type_for_step(step_name: CustomStoryWorkflowStep):
        if step_name == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            return StoryBatchJobType.STORY_PLAN
        if step_name == CustomStoryWorkflowStep.STORY_GENERATION:
            return StoryBatchJobType.STORY
        if step_name == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return StoryBatchJobType.IMAGE_PLAN
        if step_name == CustomStoryWorkflowStep.IMAGE_GENERATION:
            return StoryBatchJobType.IMAGE
        return StoryBatchJobType.AUDIO

    @staticmethod
    def _step_for_job_type(job_type: StoryBatchJobType) -> CustomStoryWorkflowStep:
        value = job_type.value if hasattr(job_type, "value") else str(job_type)
        if value == StoryBatchJobType.STORY_PLAN.value:
            return CustomStoryWorkflowStep.STORY_PLAN_GENERATION
        if value == StoryBatchJobType.STORY.value:
            return CustomStoryWorkflowStep.STORY_GENERATION
        if value == StoryBatchJobType.IMAGE_PLAN.value:
            return CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION
        if value == StoryBatchJobType.IMAGE.value:
            return CustomStoryWorkflowStep.IMAGE_GENERATION
        return CustomStoryWorkflowStep.NARRATION_GENERATION

    async def _step_has_completed_record(
        self,
        workflow: CustomStoryWorkflowEntity,
        step_name: CustomStoryWorkflowStep,
    ) -> bool:
        step = await self.steps.latest_for_workflow_step(workflow.id, step_name)
        return step is not None and self._status_value(step.status) == StepStatus.COMPLETED.value

    async def _delayed_outputs_completed(self, workflow: CustomStoryWorkflowEntity) -> bool:
        for step in (CustomStoryWorkflowStep.IMAGE_GENERATION, CustomStoryWorkflowStep.NARRATION_GENERATION):
            if self._step_disabled_by_request(workflow, step):
                continue
            if not await self._step_is_complete(workflow, step):
                return False
        return True

    async def _failed_delayed_batch_job(self, workflow: CustomStoryWorkflowEntity) -> CustomStoryBatchJobEntity | None:
        for step in (CustomStoryWorkflowStep.IMAGE_GENERATION, CustomStoryWorkflowStep.NARRATION_GENERATION):
            if self._step_disabled_by_request(workflow, step):
                continue
            latest = await self.batch_jobs.latest_for_workflow_type(workflow.id, self._job_type_for_step(step))
            if latest is None:
                continue
            if self._status_value(latest.status) in {
                StoryBatchJobStatus.FAILED.value,
                StoryBatchJobStatus.CANCELLED.value,
            }:
                return latest
        return None

    async def _delayed_waiting_step(self, workflow: CustomStoryWorkflowEntity) -> str | None:
        for step in (CustomStoryWorkflowStep.IMAGE_GENERATION, CustomStoryWorkflowStep.NARRATION_GENERATION):
            if self._step_disabled_by_request(workflow, step):
                continue
            if not await self._step_is_complete(workflow, step):
                return step.value
        return CustomStoryWorkflowStep.PUBLISH_STORY.value

    @staticmethod
    def _workflow_languages(workflow: CustomStoryWorkflowEntity) -> list[str]:
        return _normalize_story_languages(workflow)

    @staticmethod
    def _workflow_primary_language(workflow: CustomStoryWorkflowEntity) -> str | None:
        languages = CustomStoryWorkflowService._workflow_languages(workflow)
        return languages[0] if languages else None

    @staticmethod
    def _event_language(event: CustomStoryWorkflowEventEntity | None, workflow: CustomStoryWorkflowEntity) -> str | None:
        metadata = event.metadata_json if event is not None and isinstance(event.metadata_json, dict) else {}
        language = metadata.get("language")
        if language:
            return str(language)
        languages = CustomStoryWorkflowService._workflow_languages(workflow)
        return languages[0] if len(languages) == 1 else None

    @staticmethod
    def _workflow_story_json_for_language(workflow: CustomStoryWorkflowEntity, language: str) -> dict[str, Any]:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        return _story_json_language_variant(story_json, language)

    @staticmethod
    def _set_workflow_story_json_for_language(
        workflow: CustomStoryWorkflowEntity,
        language: str,
        language_story_json: dict[str, Any],
    ) -> None:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        _set_story_json_language_variant(story_json, language, language_story_json)
        workflow.story_json = story_json

    @staticmethod
    def _workflow_has_audio_for_all_languages(workflow: CustomStoryWorkflowEntity) -> bool:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        languages = CustomStoryWorkflowService._workflow_languages(workflow)
        variants = story_json.get("language_variants") if isinstance(story_json.get("language_variants"), dict) else {}
        for language in languages:
            if len(languages) > 1 and not isinstance(variants.get(language), dict):
                return False
            if not CustomStoryWorkflowService._story_has_audio(_story_json_language_variant(story_json, language)):
                return False
        return True

    @staticmethod
    def _story_json_variants_for_publish(workflow: CustomStoryWorkflowEntity) -> dict[str, dict[str, Any]]:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        variants: dict[str, dict[str, Any]] = {}
        for language in CustomStoryWorkflowService._workflow_languages(workflow):
            variant = _story_json_language_variant(story_json, language)
            _sync_story_media_to_language_variants(variant, include_audio=True)
            variants[language] = variant
        return variants

    def _step_disabled_by_request(
        self,
        workflow: CustomStoryWorkflowEntity,
        step: CustomStoryWorkflowStep,
    ) -> bool:
        if step in {
            CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION,
            CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION,
            CustomStoryWorkflowStep.IMAGE_GENERATION,
        }:
            return not self._execute_image_enabled(workflow)
        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            return not self._execute_narration_enabled(workflow)
        return False

    @staticmethod
    def _execute_image_enabled(workflow: CustomStoryWorkflowEntity) -> bool:
        return bool(getattr(workflow, "execute_image", True))

    @staticmethod
    def _execute_narration_enabled(workflow: CustomStoryWorkflowEntity) -> bool:
        return bool(getattr(workflow, "execute_narration", True))

    async def _first_incomplete_step(self, workflow: CustomStoryWorkflowEntity) -> CustomStoryWorkflowStep:
        for step in self.ORDERED_STEPS:
            if not await self._step_is_complete(workflow, step):
                return step
        return CustomStoryWorkflowStep.PUBLISH_STORY

    async def _step_is_complete(self, workflow: CustomStoryWorkflowEntity, step: CustomStoryWorkflowStep) -> bool:
        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            return isinstance(workflow.story_plan_json, dict) and bool(workflow.story_plan_json)
        if step == CustomStoryWorkflowStep.STORY_PLAN_VALIDATION:
            return bool(workflow.story_plan_validated)
        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            return isinstance(workflow.story_json, dict) and bool(workflow.story_json.get("pages"))
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            if not self._execute_image_enabled(workflow):
                return True
            if not isinstance(workflow.image_plan_json, dict) or not workflow.image_plan_json:
                return False
            try:
                StoryService._validate_image_plan_page_contract(workflow.image_plan_json, workflow.story_json or {})
            except AppException:
                return False
            return True
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION:
            if not self._execute_image_enabled(workflow):
                return True
            return bool(workflow.image_plan_validated)
        if step == CustomStoryWorkflowStep.IMAGE_GENERATION:
            if not self._execute_image_enabled(workflow):
                return True
            if self._story_has_images(workflow.story_json or {}):
                return True
            latest = await self.batch_jobs.latest_for_workflow_type(workflow.id, self._job_type_for_step(step))
            return latest is not None and self._status_value(latest.status) == "SUCCEEDED"
        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            if not self._execute_narration_enabled(workflow):
                return True
            return self._workflow_has_audio_for_all_languages(workflow)
        if step == CustomStoryWorkflowStep.PUBLISH_STORY:
            if self._is_generic_workflow(workflow):
                return workflow.generic_story_id is not None
            return workflow.story_id is not None
        return False

    @staticmethod
    def _story_has_images(story_json: dict[str, Any]) -> bool:
        if story_json.get("cover_image_url") or story_json.get("back_cover_image_url"):
            return True
        return any(isinstance(page, dict) and page.get("image_url") for page in story_json.get("pages") or [])

    @staticmethod
    def _story_has_audio(story_json: dict[str, Any]) -> bool:
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        if not pages:
            return False
        return all(
            isinstance(page, dict) and CustomStoryWorkflowService._page_has_audio(page)
            for page in pages
        )

    @staticmethod
    def _page_has_audio(page: dict[str, Any]) -> bool:
        if page.get("tts_skipped"):
            return True
        return bool(page.get("audio_url") and page.get("duration") and page.get("word_timestamps"))

    @staticmethod
    def _flags(workflow: CustomStoryWorkflowEntity) -> StoryGenerationFlags:
        return StoryGenerationFlags(
            skip_image_generation=not CustomStoryWorkflowService._execute_image_enabled(workflow),
            skip_validation=bool(getattr(workflow, "skip_validation", False)),
        )

    @staticmethod
    def _step_input(workflow: CustomStoryWorkflowEntity, step: CustomStoryWorkflowStep) -> dict[str, Any] | None:
        if step == CustomStoryWorkflowStep.STORY_PLAN_GENERATION:
            return CustomStoryWorkflowService._request_snapshot_from_columns(workflow)
        if step == CustomStoryWorkflowStep.STORY_PLAN_VALIDATION:
            return {"story_plan": workflow.story_plan_json}
        if step == CustomStoryWorkflowStep.STORY_GENERATION:
            return {"story_plan": workflow.story_plan_json}
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return {"story_plan": workflow.story_plan_json, "story_json": workflow.story_json}
        if step == CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION:
            return {"image_plan": workflow.image_plan_json, "story_json": workflow.story_json}
        if step == CustomStoryWorkflowStep.IMAGE_GENERATION:
            return {
                "image_plan_summary": CustomStoryWorkflowService._image_plan_summary(workflow.image_plan_json),
                "story_json_summary": CustomStoryWorkflowService._story_json_summary(workflow.story_json),
            }
        if step == CustomStoryWorkflowStep.NARRATION_GENERATION:
            return {"story_json_summary": CustomStoryWorkflowService._story_json_summary(workflow.story_json)}
        if step == CustomStoryWorkflowStep.PUBLISH_STORY:
            return {"story_json_summary": CustomStoryWorkflowService._story_json_summary(workflow.story_json)}
        return None

    @staticmethod
    def _image_plan_summary(image_plan: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(image_plan, dict):
            return {}
        pages = image_plan.get("pages") if isinstance(image_plan.get("pages"), list) else []
        return {
            "has_cover": isinstance(image_plan.get("cover"), dict),
            "page_count": len(pages),
            "has_back_cover": isinstance(image_plan.get("back_cover"), dict),
            "has_visual_bible": isinstance(image_plan.get("visual_bible"), dict),
        }

    @staticmethod
    def _story_json_summary(story_json: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(story_json, dict):
            return {}
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        return {
            "title": story_json.get("title"),
            "page_count": len(pages),
            "has_cover_image": bool(story_json.get("cover_image_url")),
            "has_back_cover_image": bool(story_json.get("back_cover_image_url")),
            "image_page_count": sum(1 for page in pages if isinstance(page, dict) and page.get("image_url")),
            "audio_page_count": sum(1 for page in pages if isinstance(page, dict) and page.get("audio_url")),
        }

    @staticmethod
    def _step_output_summary(output: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(output, dict):
            return {}
        if output.get("skipped"):
            return {"skipped": True, "message": output.get("message")}
        if output.get("deferred"):
            return {
                "deferred": True,
                "mode": output.get("mode"),
                "batch_job_id": output.get("batch_job_id"),
                "status": output.get("status"),
            }
        pages = output.get("pages")
        visual_bible = output.get("visual_bible")
        summary: dict[str, Any] = {
            "keys": sorted(str(key) for key in output.keys() if not str(key).startswith("_")),
        }
        if isinstance(pages, list):
            summary["page_count"] = len(pages)
        if isinstance(visual_bible, dict):
            summary["has_visual_bible"] = True
        if output.get("title"):
            summary["title"] = output.get("title")
        if output.get("story_id"):
            summary["story_id"] = output.get("story_id")
        return summary

    @staticmethod
    def _request_snapshot_from_columns(workflow: CustomStoryWorkflowEntity) -> dict[str, Any]:
        reader_category = CustomStoryWorkflowService._workflow_reader_category(workflow)
        age_group = CustomStoryWorkflowService._status_value(workflow.age_group)
        use_child_character = bool(getattr(workflow, "use_child_character", False))
        execute_image = bool(getattr(workflow, "execute_image", True))
        return {
            "story_type": CustomStoryWorkflowService._status_value(
                getattr(workflow, "story_type", CustomStoryWorkflowType.CUSTOM)
            ),
            "child_id": str(workflow.child_id) if workflow.child_id else None,
            "generic_story_id": (
                str(workflow.generic_story_id) if getattr(workflow, "generic_story_id", None) else None
            ),
            "reader_category": reader_category,
            "age_group": age_group,
            "category": workflow.category,
            "learning_goal": workflow.learning_goal,
            "context": workflow.context,
            "language": CustomStoryWorkflowService._workflow_primary_language(workflow),
            "languages": CustomStoryWorkflowService._workflow_languages(workflow),
            "publish_status": getattr(workflow, "publish_status", None),
            "use_child_character": use_child_character,
            "cast_mode": StoryService.CAST_MODE_CHILD_HERO if use_child_character else StoryService.CAST_MODE_IMAGINED,
            "execute_image": execute_image,
            "skip_image_generation": not execute_image,
            "execute_narration": bool(getattr(workflow, "execute_narration", True)),
            "skip_validation": bool(getattr(workflow, "skip_validation", False)),
            "execute_workflow": bool(getattr(workflow, "execute_workflow", False)),
        }

    @staticmethod
    def _workflow_reader_category(workflow: CustomStoryWorkflowEntity) -> str | None:
        raw_reader_category = getattr(workflow, "reader_category", None)
        if raw_reader_category:
            try:
                return normalize_reader_category(raw_reader_category).value
            except AttributeError:
                return str(raw_reader_category)
        try:
            return reader_category_for_age_group(workflow.age_group).value
        except AppException:
            return None

    @staticmethod
    def _status_value(status: Any) -> str:
        return status.value if hasattr(status, "value") else str(status)

    @staticmethod
    def _is_generic_workflow(workflow: CustomStoryWorkflowEntity) -> bool:
        return (
            CustomStoryWorkflowService._status_value(getattr(workflow, "story_type", CustomStoryWorkflowType.CUSTOM))
            == CustomStoryWorkflowType.GENERIC.value
        )

    async def _get_owned(
        self,
        user_id: UUID,
        workflow_id: UUID,
        *,
        story_type: CustomStoryWorkflowType | None = CustomStoryWorkflowType.CUSTOM,
    ) -> CustomStoryWorkflowEntity:
        workflow = await self.workflows.get_for_user(user_id, workflow_id)
        if workflow is None or (
            story_type is not None
            and self._status_value(getattr(workflow, "story_type", None)) != story_type.value
        ):
            raise NotFoundException("Custom story workflow not found")
        return workflow

    @staticmethod
    def _response(workflow: CustomStoryWorkflowEntity) -> CustomStoryWorkflowResponse:
        return CustomStoryWorkflowResponse(
            workflow_id=workflow.id,
            request_number=int(getattr(workflow, "request_number", 0) or 0),
            story_type=CustomStoryWorkflowService._status_value(
                getattr(workflow, "story_type", CustomStoryWorkflowType.CUSTOM)
            ),
            story_id=getattr(workflow, "story_id", None),
            generic_story_id=getattr(workflow, "generic_story_id", None),
            child_id=workflow.child_id,
            status=workflow.status.value if hasattr(workflow.status, "value") else str(workflow.status),
            current_step=workflow.current_step,
            error_message=workflow.error_message,
            reader_category=CustomStoryWorkflowService._workflow_reader_category(workflow),
            age_group=CustomStoryWorkflowService._status_value(workflow.age_group),
            category=workflow.category,
            learning_goal=workflow.learning_goal,
            context=workflow.context,
            languages=CustomStoryWorkflowService._workflow_languages(workflow),
            publish_status=getattr(workflow, "publish_status", None),
            use_child_character=bool(getattr(workflow, "use_child_character", False)),
            execute_image=bool(getattr(workflow, "execute_image", True)),
            execute_narration=bool(getattr(workflow, "execute_narration", True)),
            skip_validation=bool(getattr(workflow, "skip_validation", False)),
            execute_workflow=bool(getattr(workflow, "execute_workflow", False)),
            title=workflow.title,
            summary=workflow.summary,
            moral=workflow.moral,
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

    @staticmethod
    def _event_response(event: CustomStoryWorkflowEventEntity) -> CustomStoryWorkflowEventResponse:
        return CustomStoryWorkflowEventResponse(
            id=event.id,
            workflow_id=event.workflow_id,
            story_type=CustomStoryWorkflowService._status_value(
                getattr(event, "story_type", CustomStoryWorkflowType.CUSTOM)
            ),
            step_name=event.step_name.value if hasattr(event.step_name, "value") else str(event.step_name),
            status=event.status.value if hasattr(event.status, "value") else str(event.status),
            retry_count=event.retry_count,
            retry_flag=event.retry_flag,
            retry_comment=event.retry_comment,
            retry_source_event_id=event.retry_source_event_id,
            metadata=event.metadata_json,
            error_message=event.error_message,
            locked_at=event.locked_at,
            completed_at=event.completed_at,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )
