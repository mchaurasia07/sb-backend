"""Delayed story generation workflow backed by Google Gemini Batch API."""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID

from fastapi import status
from google import genai
from google.genai import types
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.story import Story, StoryStatus
from app.entity.story_batch_job import StoryBatchJob, StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StoryStepName, StepStatus
from app.repository.child_repository import ChildRepository
from app.repository.story_batch_job_repository import StoryBatchJobRepository
from app.repository.story_page_repository import StoryPageRepository
from app.repository.story_repository import StoryRepository
from app.repository.story_step_repository import StoryStepRepository
from app.service.ai.google_provider import GoogleProvider
from app.service.image_storage_provider import get_image_storage_service
from app.service.story_audio_storage_provider import get_story_audio_storage_service
from app.service.story_completion_email_service import StoryCompletionEmailService
from app.service.story_narration_profile import build_page_narration
from app.service.story_service import DEFAULT_STORY_LANGUAGE, StoryGenerationFlags, StoryService
from app.utils.google_tts_utils import GoogleTTSProvider
from app.utils.prompt_loader import load_prompt
from app.utils.word_timestamps import generate_word_timestamps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BatchImageItem:
    key: str
    page_type: str
    page_number: int
    page_data: dict[str, Any]
    source_image_prompt: str
    rendered_prompt: str
    aspect_ratio: str
    image_size: str
    file_name: str
    text: str


@dataclass(frozen=True)
class BatchImageReference:
    character_id: str
    name: str
    role: str
    image_url: str
    part: types.Part
    priority: int = 100


@dataclass(frozen=True)
class BatchAudioItem:
    key: str
    page_number: int
    text: str
    prompt: str
    pace: str
    voice_style: str
    tone: str
    emotion: str


class StoryServiceBatchService:
    """Runs the delayed story workflow with strict all-images/all-audio completion."""

    SUCCEEDED_STATES = {"JOB_STATE_SUCCEEDED", "SUCCEEDED"}
    CANCELLED_STATES = {"JOB_STATE_CANCELLED", "CANCELLED"}
    FAILED_STATES = {"JOB_STATE_FAILED", "JOB_STATE_EXPIRED", "FAILED"}

    def __init__(self, session: AsyncSession):
        self.session = session
        self.stories = StoryRepository(session)
        self.story_steps = StoryStepRepository(session)
        self.story_pages = StoryPageRepository(session)
        self.children = ChildRepository(session)
        self.batch_jobs = StoryBatchJobRepository(session)
        self.workflow = StoryService(session)
        self.image_storage = get_image_storage_service()
        self.audio_storage = get_story_audio_storage_service()
        self.tts_provider = GoogleTTSProvider()
        self.google_client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    @staticmethod
    def _log_event(event: str, **fields: Any) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        logger.info("[story_batch] event=%s %s", event, details)

    @staticmethod
    def _reference_image_model(story: Story) -> str:
        model = story.reference_image_model or settings.GOOGLE_REFERENCE_IMAGE_MODEL
        if model == "gemini-2.5-flash-image":
            model = settings.GOOGLE_REFERENCE_IMAGE_MODEL
        return model.removeprefix("models/")

    async def cancel_batch_job(
        self,
        *,
        user_id: UUID,
        story_id: UUID,
        batch_job_id: UUID,
    ) -> dict[str, Any]:
        """Cancel a submitted Google Batch job and update local tracking."""
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found", "STORY_NOT_FOUND")

        job = await self.batch_jobs.get_for_story(story_id, batch_job_id)
        if job is None:
            raise NotFoundException("Story batch job not found", "STORY_BATCH_JOB_NOT_FOUND")

        if job.status == StoryBatchJobStatus.SUCCEEDED:
            raise AppException(
                "Completed batch jobs cannot be cancelled",
                status.HTTP_409_CONFLICT,
                "BATCH_JOB_ALREADY_COMPLETED",
            )

        if job.status == StoryBatchJobStatus.CANCELLED:
            return self._batch_cancel_response(story, job, "Batch job was already cancelled")

        if not job.provider_job_name:
            raise AppException(
                "Batch job has not been submitted to Google yet",
                status.HTTP_409_CONFLICT,
                "BATCH_JOB_NOT_SUBMITTED",
            )

        try:
            await self.google_client.aio.batches.cancel(name=job.provider_job_name)
            provider_job = await self.google_client.aio.batches.get(name=job.provider_job_name)
            provider_state = self._job_state_name(provider_job)
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
            flag_modified(job, "missing_keys")
        await self.batch_jobs.update(job)

        if story.status in {
            StoryStatus.IN_PROGRESS,
            StoryStatus.IMAGE_RETRY_REQUIRED,
            StoryStatus.AUDIO_RETRY_REQUIRED,
        }:
            story.status = StoryStatus.FAILED
            story.current_step = None
            story.error_message = f"Batch {job.job_type.value} job cancelled by user request"
            await self.stories.update(story)

        await self.session.commit()
        return self._batch_cancel_response(story, job, "Batch job cancelled successfully")

    async def reconcile_batch_jobs(self, *, limit: int = 50) -> dict[str, Any]:
        """Check submitted/running provider jobs once and process completed results."""
        jobs = await self.batch_jobs.list_reconcilable(limit=limit)
        results: list[dict[str, Any]] = []
        processed_count = 0

        self._log_event("reconcile_started", job_count=len(jobs), limit=limit)
        for job in jobs:
            try:
                result = await self._reconcile_batch_job(job)
                if result["action"] not in {"still_running", "skipped"}:
                    processed_count += 1
                results.append(result)
            except Exception as exc:
                self._log_event("reconcile_job_failed", batch_job_id=job.id, story_id=job.story_id, error=str(exc))
                results.append(
                    {
                        "story_id": job.story_id,
                        "batch_job_id": job.id,
                        "job_type": job.job_type.value,
                        "status": job.status.value,
                        "provider_state": job.provider_state,
                        "action": "error",
                        "message": str(exc),
                    }
                )

        self._log_event("reconcile_completed", checked_count=len(jobs), processed_count=processed_count)
        return {
            "checked_count": len(jobs),
            "processed_count": processed_count,
            "results": results,
        }

    async def _reconcile_batch_job(self, job: StoryBatchJob) -> dict[str, Any]:
        if not job.provider_job_name:
            return self._reconcile_result(job, "skipped", "Batch job has no provider job name")

        provider_job = await self.google_client.aio.batches.get(name=job.provider_job_name)
        state_name = self._job_state_name(provider_job)
        job.provider_state = state_name

        if state_name in self.SUCCEEDED_STATES:
            story = await self.stories.get_by_id(job.story_id)
            if story is None:
                job.status = StoryBatchJobStatus.FAILED
                job.error_message = "Story not found during batch reconciliation"
                await self.batch_jobs.update(job)
                await self.session.commit()
                return self._reconcile_result(job, "failed", job.error_message)

            if job.job_type == StoryBatchJobType.IMAGE:
                await self._process_reconciled_image_job(story, job, provider_job)
                if job.status == StoryBatchJobStatus.FAILED:
                    return self._reconcile_result(job, "failed", job.error_message)
                audio_message = await self._ensure_audio_batch_submitted(story)
                return self._reconcile_result(job, "processed", f"Image job processed. {audio_message}")

            if job.job_type == StoryBatchJobType.AUDIO:
                await self._process_reconciled_audio_job(story, job, provider_job)
                return self._reconcile_result(job, "processed", "Audio job processed")

        if state_name in self.CANCELLED_STATES:
            job.status = StoryBatchJobStatus.CANCELLED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_story_failed(job.story_id, job.error_message)
            await self.session.commit()
            return self._reconcile_result(job, "cancelled", job.error_message)

        if state_name in self.FAILED_STATES:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_story_failed(job.story_id, job.error_message)
            await self.session.commit()
            return self._reconcile_result(job, "failed", job.error_message)

        job.status = StoryBatchJobStatus.RUNNING
        await self.batch_jobs.update(job)
        await self.session.commit()
        return self._reconcile_result(job, "still_running", f"Provider state is {state_name}")

    def _reconcile_result(self, job: StoryBatchJob, action: str, message: str | None = None) -> dict[str, Any]:
        return {
            "story_id": job.story_id,
            "batch_job_id": job.id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "provider_state": job.provider_state,
            "action": action,
            "message": message,
        }

    async def execute_workflow(
        self,
        story_id: UUID,
        flags: StoryGenerationFlags | None = None,
        resume: bool = False,
    ) -> Story:
        """Execute delayed story generation.

        Text planning and validation reuse the existing StoryService workflow.
        Image and narration steps are submitted through Google Batch, retried
        item-by-item, and only complete when every expected URL is present.
        """
        if flags is None:
            flags = StoryGenerationFlags()

        story = await self.stories.get_by_id_for_update(story_id)
        if story is None:
            raise NotFoundException(f"Story {story_id} not found")
        if story.status == StoryStatus.IN_PROGRESS:
            logger.warning("Story %s is already in progress; skipping duplicate delayed runner", story_id)
            return story

        await self.workflow._ensure_story_ai_config(story)
        if (story.ai_provider or settings.AI_PROVIDER).lower() != "google":
            raise AppException(
                "Delayed batch story generation currently requires AI_PROVIDER=google",
                code="BATCH_REQUIRES_GOOGLE",
            )

        story.status = StoryStatus.IN_PROGRESS
        story.error_message = None
        await self.stories.update(story)
        await self.session.commit()
        self._log_event(
            "workflow_started",
            story_id=story.id,
            resume=resume,
            skip_image_generation=flags.skip_image_generation,
            skip_validation=flags.skip_validation,
        )

        try:
            story_plan = await self._prepare_story_plan(story, flags, resume=resume)
            story_json = await self._prepare_story_json(story, story_plan, flags, resume=resume)
            image_plan = await self._prepare_image_plan(story, story_plan, story_json, flags, resume=resume)

            if flags.skip_image_generation:
                await self.workflow._create_pages_without_images(story, story_json)
                await self.workflow._persist_story_content(story, story_json)
                await self._ensure_audio_batch_submitted(story)
                self._log_event("workflow_deferred_after_audio_submit", story_id=story.id)
                await self.session.commit()
                return story
            else:
                image_job = await self._step_submit_images_batch(story, story_json, image_plan)
                if image_job is None:
                    await self._ensure_audio_batch_submitted(story)
                    self._log_event("workflow_deferred_after_audio_submit", story_id=story.id)
                    await self.session.commit()
                    return story

            story.story_plan_json = story_plan
            story.image_plan_json = image_plan
            await self.stories.update(story)
            await self.session.commit()
            self._log_event("workflow_deferred_after_image_submit", story_id=story.id)
            logger.info("Story %s: delayed batch workflow deferred to reconcile scheduler", story.id)
            return story
        except Exception as exc:
            self._log_event("workflow_failed", story_id=story.id, error=str(exc))
            logger.exception("Story %s: delayed batch workflow failed: %s", story.id, exc)
            story.status = StoryStatus.FAILED
            story.current_step = None
            story.error_message = str(exc)
            await self.stories.update(story)
            await self.session.commit()
            raise

    async def _prepare_story_plan(
        self,
        story: Story,
        flags: StoryGenerationFlags,
        *,
        resume: bool,
    ) -> dict[str, Any]:
        if resume and story.story_plan_validated and isinstance(story.story_plan_json, dict) and story.story_plan_json.get("pages"):
            self._log_event("checkpoint_reused", story_id=story.id, checkpoint="story_plan")
            return story.story_plan_json

        self._log_event("step_started", story_id=story.id, step=StoryStepName.STORY_PLAN_GENERATION.value)
        await self.workflow._set_current_step(story, StoryStepName.STORY_PLAN_GENERATION)
        story_plan = await self.workflow._step_generate_plan(story, flags)
        story.story_plan_json = story_plan
        story.story_plan_validated = False
        await self.stories.update(story)
        await self.session.commit()

        self._log_event("step_started", story_id=story.id, step=StoryStepName.STORY_PLAN_VALIDATION.value)
        await self.workflow._set_current_step(story, StoryStepName.STORY_PLAN_VALIDATION)
        story_plan = await self.workflow._step_validate_plan(story, story_plan, flags)
        story.story_plan_json = story_plan
        story.story_plan_validated = True
        await self.stories.update(story)
        await self.session.commit()
        return story_plan

    async def _prepare_story_json(
        self,
        story: Story,
        story_plan: dict[str, Any],
        flags: StoryGenerationFlags,
        *,
        resume: bool,
    ) -> dict[str, Any]:
        story_json = await self.workflow._load_existing_story_json(story) if resume else None
        if story_json is not None:
            self._log_event("checkpoint_reused", story_id=story.id, checkpoint="story_json")
            self.workflow._apply_story_metadata(story, story_plan, story_json)
            await self.stories.update(story)
            await self.session.commit()
            return story_json

        self._log_event("step_started", story_id=story.id, step=StoryStepName.STORY_GENERATION.value)
        await self.workflow._set_current_step(story, StoryStepName.STORY_GENERATION)
        story_json = await self.workflow._step_generate_story(story, story_plan, flags)
        self.workflow._apply_story_metadata(story, story_plan, story_json)
        await self.stories.update(story)
        await self.workflow._persist_story_content(story, story_json)
        return story_json

    async def _prepare_image_plan(
        self,
        story: Story,
        story_plan: dict[str, Any],
        story_json: dict[str, Any],
        flags: StoryGenerationFlags,
        *,
        resume: bool,
    ) -> dict[str, Any]:
        if resume and story.image_plan_validated and isinstance(story.image_plan_json, dict) and story.image_plan_json.get("pages"):
            self._log_event("checkpoint_reused", story_id=story.id, checkpoint="image_plan")
            return story.image_plan_json

        self._log_event("step_started", story_id=story.id, step=StoryStepName.IMAGE_PLAN_GENERATION.value)
        await self.workflow._set_current_step(story, StoryStepName.IMAGE_PLAN_GENERATION)
        image_plan = await self.workflow._step_generate_image_plan(story, story_plan, story_json, flags)
        story.image_plan_json = image_plan
        story.image_plan_validated = False
        await self.stories.update(story)
        await self.session.commit()

        if not flags.skip_validation:
            self._log_event("step_started", story_id=story.id, step=StoryStepName.IMAGE_PLAN_VALIDATION.value)
            await self.workflow._set_current_step(story, StoryStepName.IMAGE_PLAN_VALIDATION)
            image_plan = await self.workflow._step_validate_image_plan(story, image_plan, story_json, flags)
            story.image_plan_validated = True
        else:
            story.image_plan_validated = True
        if not flags.skip_image_generation:
            image_plan = await self.workflow._ensure_image_plan_character_references(story, image_plan)
        story.image_plan_json = image_plan
        await self.stories.update(story)
        await self.session.commit()
        return image_plan

    async def _step_submit_images_batch(
        self,
        story: Story,
        story_json: dict[str, Any],
        image_plan: dict[str, Any],
    ) -> StoryBatchJob | None:
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.prompt = self._json_safe({"mode": "google_batch", "image_plan": image_plan})
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            items = await self._build_image_items(story, story_json, image_plan)
            if not items:
                raise AppException("Image batch has no image prompts to generate", code="EMPTY_IMAGE_BATCH")

            missing = await self._missing_image_items(story, story_json, items)
            self._log_event(
                "image_batch_ready",
                story_id=story.id,
                expected_items=len(items),
                missing_items=len(missing),
                reused_items=len(items) - len(missing),
            )
            if not missing:
                step.status = StepStatus.COMPLETED
                step.response = {
                    "images_generated": True,
                    "expected_image_count": len(items),
                    "message": "All batch images already exist",
                }
                await self.story_steps.update(step)
                await self.workflow._persist_story_content(story, story_json)
                await self.session.commit()
                self._log_event("image_batch_step_completed", story_id=story.id, expected_items=len(items))
                return None

            story.status = StoryStatus.IN_PROGRESS
            story.current_step = StoryStepName.IMAGE_GENERATION.value
            await self.stories.update(story)
            await self.session.commit()
            self._log_event(
                "image_batch_attempt_started",
                story_id=story.id,
                attempt=1,
                item_count=len(missing),
            )

            job = await self._submit_image_batch_job_only(story, missing, attempt=1)
            step.response = {
                "images_submitted": True,
                "expected_image_count": len(items),
                "submitted_image_count": len(missing),
                "batch_job_id": str(job.id),
                "provider_job_name": job.provider_job_name,
                "provider_state": job.provider_state,
                "message": "Image batch submitted; reconcile scheduler will process results",
            }
            await self.story_steps.update(step)
            await self.workflow._persist_story_content(story, story_json)
            await self.session.commit()
            self._log_event(
                "image_batch_step_deferred",
                story_id=story.id,
                batch_job_id=job.id,
                expected_items=len(items),
                submitted_items=len(missing),
            )
            return job
        except Exception as exc:
            self._log_event("image_batch_step_failed", story_id=story.id, error=str(exc))
            step.status = StepStatus.FAILED
            step.error_message = str(exc)
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _submit_image_batch_job_only(
        self,
        story: Story,
        items: list[BatchImageItem],
        *,
        attempt: int,
    ) -> StoryBatchJob:
        model = self._reference_image_model(story)
        reference_images = await self._load_reference_image_blobs(story)
        strict_page_refs = StoryService._is_custom_story_workflow_record(story)
        request_specs = [
            (
                item,
                self._select_reference_images_for_item(
                    item,
                    reference_images,
                    model=model,
                    strict_page_refs=strict_page_refs,
                ),
            )
            for item in items
        ]
        requests = [
            self._build_image_inlined_request(item, reference_images=item_references)
            for item, item_references in request_specs
        ]
        uses_character_reference = any(item_references for _item, item_references in request_specs)
        job = await self.batch_jobs.create(
            story_id=story.id,
            job_type=StoryBatchJobType.IMAGE,
            attempt=attempt,
            expected_item_count=len(items),
            request_keys=[item.key for item in items],
            provider_model=model,
            request_payload={
                "mode": "image",
                "cast_mode": StoryService._cast_mode(story),
                "uses_character_reference": uses_character_reference,
                "reference_character_ids_by_item": {
                    item.key: [reference.character_id for reference in item_references]
                    for item, item_references in request_specs
                },
                "attempt": attempt,
                "items": [
                    self._image_item_payload(item, reference_images=item_references)
                    for item, item_references in request_specs
                ],
            },
        )
        await self.session.commit()
        self._log_event(
            "image_batch_created",
            story_id=story.id,
            batch_job_id=job.id,
            attempt=attempt,
            item_count=len(items),
            model=model,
            reference_items=sum(len(item_references) for _item, item_references in request_specs),
        )

        try:
            provider_job = await self.google_client.aio.batches.create(
                model=model,
                src=requests,
                config={"display_name": f"story-{story.id}-images-attempt-{attempt}"},
            )
            job.provider_job_name = provider_job.name
            job.provider_state = self._job_state_name(provider_job)
            await self.batch_jobs.update(job)
            await self.session.commit()
            self._log_event(
                "image_batch_submitted",
                story_id=story.id,
                batch_job_id=job.id,
                provider_job_name=job.provider_job_name,
                provider_state=job.provider_state,
            )
            return job
        except Exception as exc:
            self._log_event("image_batch_submit_failed", story_id=story.id, batch_job_id=job.id, error=str(exc))
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            job.missing_keys = [item.key for item in items]
            await self.batch_jobs.update(job)
            await self.session.commit()
            raise

    async def _submit_and_process_image_batch(
        self,
        story: Story,
        story_json: dict[str, Any],
        items: list[BatchImageItem],
        attempt: int,
    ) -> tuple[set[str], set[str], dict[str, Any]]:
        model = self._reference_image_model(story)
        reference_images = await self._load_reference_image_blobs(story)
        strict_page_refs = StoryService._is_custom_story_workflow_record(story)
        request_specs = [
            (
                item,
                self._select_reference_images_for_item(
                    item,
                    reference_images,
                    model=model,
                    strict_page_refs=strict_page_refs,
                ),
            )
            for item in items
        ]
        requests = [
            self._build_image_inlined_request(item, reference_images=item_references)
            for item, item_references in request_specs
        ]
        uses_character_reference = any(item_references for _item, item_references in request_specs)
        job = await self.batch_jobs.create(
            story_id=story.id,
            job_type=StoryBatchJobType.IMAGE,
            attempt=attempt,
            expected_item_count=len(items),
            request_keys=[item.key for item in items],
            provider_model=model,
            request_payload={
                "mode": "image",
                "cast_mode": StoryService._cast_mode(story),
                "uses_character_reference": uses_character_reference,
                "reference_character_ids_by_item": {
                    item.key: [reference.character_id for reference in item_references]
                    for item, item_references in request_specs
                },
                "attempt": attempt,
                "items": [
                    self._image_item_payload(item, reference_images=item_references)
                    for item, item_references in request_specs
                ],
            },
        )
        await self.session.commit()
        self._log_event(
            "image_batch_created",
            story_id=story.id,
            batch_job_id=job.id,
            attempt=attempt,
            item_count=len(items),
            model=model,
            reference_items=sum(len(item_references) for _item, item_references in request_specs),
        )

        try:
            provider_job = await self.google_client.aio.batches.create(
                model=model,
                src=requests,
                config={"display_name": f"story-{story.id}-images-attempt-{attempt}"},
            )
            job.provider_job_name = provider_job.name
            job.provider_state = self._job_state_name(provider_job)
            await self.batch_jobs.update(job)
            await self.session.commit()
            self._log_event(
                "image_batch_submitted",
                story_id=story.id,
                batch_job_id=job.id,
                provider_job_name=job.provider_job_name,
                provider_state=job.provider_state,
            )

            completed_job = await self._wait_for_provider_job(job)
            responses = list((completed_job.dest.inlined_responses if completed_job.dest else None) or [])
            by_key = self._responses_by_key(responses)
            if not by_key and responses:
                by_key = {item.key: response for item, response in zip(items, responses, strict=False)}
            completed_keys: set[str] = set()
            failed_keys: set[str] = set()
            response_summary: dict[str, Any] = {"items": []}

            items_by_key = {item.key: item for item in items}
            for key, item in items_by_key.items():
                inlined_response = by_key.get(key)
                if inlined_response is None:
                    failed_keys.add(key)
                    response_summary["items"].append({"key": key, "status": "missing_response"})
                    continue
                if inlined_response.error:
                    failed_keys.add(key)
                    response_summary["items"].append(
                        {"key": key, "status": "error", "error": self._model_dump_safe(inlined_response.error)}
                    )
                    continue
                if inlined_response.response is None:
                    failed_keys.add(key)
                    response_summary["items"].append({"key": key, "status": "empty_response"})
                    continue

                try:
                    image_bytes, response_text = GoogleProvider._extract_image_from_content_response(
                        inlined_response.response
                    )
                    cropped = StoryService._crop_image_bytes_to_aspect_ratio(image_bytes, item.aspect_ratio)
                    image_url = await self.image_storage.save_story_image(story.id, cropped, item.file_name, "")
                    await self._save_image_item_result(story, story_json, item, image_url)
                    completed_keys.add(key)
                    response_summary["items"].append(
                        {
                            "key": key,
                            "status": "completed",
                            "image_url": image_url,
                            "response_text": response_text,
                        }
                    )
                except Exception as exc:
                    failed_keys.add(key)
                    response_summary["items"].append({"key": key, "status": "save_failed", "error": str(exc)})

            await self.workflow._persist_story_content(story, story_json)
            job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
            job.completed_item_count = len(completed_keys)
            job.failed_item_count = len(failed_keys)
            job.missing_keys = sorted(set(items_by_key) - completed_keys)
            job.response_payload = response_summary
            if failed_keys:
                job.error_message = f"Missing image keys: {', '.join(sorted(failed_keys))}"
            await self.batch_jobs.update(job)
            await self.session.commit()
            self._log_event(
                "image_batch_processed",
                story_id=story.id,
                batch_job_id=job.id,
                status=job.status.value,
                completed=len(completed_keys),
                failed=len(failed_keys),
                missing=len(job.missing_keys or []),
            )
            return completed_keys, failed_keys, {
                "batch_job_id": str(job.id),
                "provider_job_name": job.provider_job_name,
                "attempt": attempt,
                "completed_keys": sorted(completed_keys),
                "failed_keys": sorted(failed_keys),
            }
        except Exception as exc:
            self._log_event("image_batch_failed", story_id=story.id, batch_job_id=job.id, error=str(exc))
            if job.status != StoryBatchJobStatus.CANCELLED:
                job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            job.missing_keys = [item.key for item in items]
            await self.batch_jobs.update(job)
            await self.session.commit()
            raise

    async def _step_generate_narration_batch(self, story: Story, story_json: dict[str, Any]) -> dict[str, Any]:
        step = await self.story_steps.create(story.id, StoryStepName.NARRATION_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.prompt = self._json_safe(
            {
                "mode": "google_batch",
                "language": DEFAULT_STORY_LANGUAGE,
                "page_count": len(story_json.get("pages", [])),
            }
        )
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            items = self._build_audio_items(story_json, age_group=story.age_group.value)
            self._log_event("audio_batch_ready", story_id=story.id, expected_items=len(items))
            if settings.GOOGLE_TTS_SKIP_CALL:
                self._apply_skipped_tts(story_json, items)
                await self.workflow._persist_story_content(story, story_json)
                step.status = StepStatus.COMPLETED
                step.response = {"tts_skipped": True, "page_count": len(items)}
                await self.story_steps.update(step)
                await self.session.commit()
                self._log_event("audio_batch_skipped", story_id=story.id, page_count=len(items))
                return story_json

            missing = self._missing_audio_items(story_json, items)
            self._log_event(
                "audio_batch_missing_checked",
                story_id=story.id,
                missing_items=len(missing),
                reused_items=len(items) - len(missing),
            )
            attempts: list[dict[str, Any]] = []
            for attempt in range(1, settings.STORY_BATCH_MAX_AUDIO_RETRIES + 1):
                if not missing:
                    break
                story.status = StoryStatus.AUDIO_RETRY_REQUIRED if attempt > 1 else StoryStatus.IN_PROGRESS
                story.current_step = StoryStepName.NARRATION_GENERATION.value
                await self.stories.update(story)
                await self.session.commit()
                self._log_event(
                    "audio_batch_attempt_started",
                    story_id=story.id,
                    attempt=attempt,
                    item_count=len(missing),
                )

                completed_keys, failed_keys, job_summary = await self._submit_and_process_audio_batch(
                    story,
                    story_json,
                    missing,
                    attempt,
                )
                attempts.append(job_summary)
                missing = [item for item in missing if item.key not in completed_keys or item.key in failed_keys]

            final_missing = [item.key for item in self._missing_audio_items(story_json, items)]
            if final_missing:
                error = f"Audio batch failed after retries. Missing audio keys: {', '.join(final_missing)}"
                step.status = StepStatus.FAILED
                step.error_message = error
                step.response = {"attempts": attempts, "missing_audio_keys": final_missing}
                await self.story_steps.update(step)
                await self.session.commit()
                raise AppException(error, code="AUDIO_BATCH_INCOMPLETE")

            total_duration = sum(
                page.get("duration") or 0
                for page in story_json.get("pages", [])
                if isinstance(page, dict) and isinstance(page.get("duration"), (int, float))
            )
            step.status = StepStatus.COMPLETED
            step.response = {
                "narration_generated": True,
                "mode": "google_batch",
                "language": DEFAULT_STORY_LANGUAGE,
                "page_count": len(story_json.get("pages", [])),
                "total_duration": round(total_duration, 2),
                "attempts": attempts,
            }
            await self.story_steps.update(step)
            await self.workflow._persist_story_content(story, story_json)
            await self.session.commit()
            self._log_event(
                "audio_batch_step_completed",
                story_id=story.id,
                page_count=len(story_json.get("pages", [])),
                total_duration=round(total_duration, 2),
            )
            return story_json
        except Exception as exc:
            self._log_event("audio_batch_step_failed", story_id=story.id, error=str(exc))
            step.status = StepStatus.FAILED
            step.error_message = str(exc)
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _submit_and_process_audio_batch(
        self,
        story: Story,
        story_json: dict[str, Any],
        items: list[BatchAudioItem],
        attempt: int,
    ) -> tuple[set[str], set[str], dict[str, Any]]:
        requests = [self._build_audio_inlined_request(item) for item in items]
        model = settings.GOOGLE_TTS_MODEL.removeprefix("models/")
        job = await self.batch_jobs.create(
            story_id=story.id,
            job_type=StoryBatchJobType.AUDIO,
            attempt=attempt,
            expected_item_count=len(items),
            request_keys=[item.key for item in items],
            provider_model=model,
            request_payload={
                "mode": "audio",
                "attempt": attempt,
                "language": DEFAULT_STORY_LANGUAGE,
                "voice": settings.GOOGLE_TTS_VOICE,
                "items": [
                    {
                        "key": item.key,
                        "page_number": item.page_number,
                        "text": item.text,
                        "text_chars": len(item.text),
                        "prompt": item.prompt,
                        "pace": item.pace,
                        "voice_style": item.voice_style,
                        "tone": item.tone,
                        "emotion": item.emotion,
                    }
                    for item in items
                ],
            },
        )
        await self.session.commit()
        self._log_event(
            "audio_batch_created",
            story_id=story.id,
            batch_job_id=job.id,
            attempt=attempt,
            item_count=len(items),
            model=model,
        )

        try:
            provider_job = await self.google_client.aio.batches.create(
                model=model,
                src=requests,
                config={"display_name": f"story-{story.id}-audio-attempt-{attempt}"},
            )
            job.provider_job_name = provider_job.name
            job.provider_state = self._job_state_name(provider_job)
            await self.batch_jobs.update(job)
            await self.session.commit()
            self._log_event(
                "audio_batch_submitted",
                story_id=story.id,
                batch_job_id=job.id,
                provider_job_name=job.provider_job_name,
                provider_state=job.provider_state,
            )

            completed_job = await self._wait_for_provider_job(job)
            responses = list((completed_job.dest.inlined_responses if completed_job.dest else None) or [])
            by_key = self._responses_by_key(responses)
            if not by_key and responses:
                by_key = {item.key: response for item, response in zip(items, responses, strict=False)}
            completed_keys: set[str] = set()
            failed_keys: set[str] = set()
            response_summary: dict[str, Any] = {"items": []}

            items_by_key = {item.key: item for item in items}
            for key, item in items_by_key.items():
                inlined_response = by_key.get(key)
                if inlined_response is None or inlined_response.response is None:
                    failed_keys.add(key)
                    response_summary["items"].append({"key": key, "status": "missing_response"})
                    continue
                if inlined_response.error:
                    failed_keys.add(key)
                    response_summary["items"].append(
                        {"key": key, "status": "error", "error": self._model_dump_safe(inlined_response.error)}
                    )
                    continue

                try:
                    pcm_bytes = self._extract_audio_from_response(inlined_response.response)
                    wav_bytes = self.tts_provider._pcm_to_wav(pcm_bytes)
                    duration = self.tts_provider._pcm_duration_seconds(pcm_bytes)
                    audio_url = await self.audio_storage.save_story_page_audio(
                        story_id=story.id,
                        language=DEFAULT_STORY_LANGUAGE,
                        page_number=item.page_number,
                        audio_bytes=wav_bytes,
                    )
                    self._set_story_json_page_audio(story_json, item, audio_url, duration)
                    completed_keys.add(key)
                    response_summary["items"].append(
                        {
                            "key": key,
                            "status": "completed",
                            "audio_url": audio_url,
                            "duration": round(duration, 2),
                        }
                    )
                except Exception as exc:
                    failed_keys.add(key)
                    response_summary["items"].append({"key": key, "status": "save_failed", "error": str(exc)})

            job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
            job.completed_item_count = len(completed_keys)
            job.failed_item_count = len(failed_keys)
            job.missing_keys = sorted(set(items_by_key) - completed_keys)
            job.response_payload = response_summary
            if failed_keys:
                job.error_message = f"Missing audio keys: {', '.join(sorted(failed_keys))}"
            await self.batch_jobs.update(job)
            await self.workflow._persist_story_content(story, story_json)
            await self.session.commit()
            self._log_event(
                "audio_batch_processed",
                story_id=story.id,
                batch_job_id=job.id,
                status=job.status.value,
                completed=len(completed_keys),
                failed=len(failed_keys),
                missing=len(job.missing_keys or []),
            )
            return completed_keys, failed_keys, {
                "batch_job_id": str(job.id),
                "provider_job_name": job.provider_job_name,
                "attempt": attempt,
                "completed_keys": sorted(completed_keys),
                "failed_keys": sorted(failed_keys),
            }
        except Exception as exc:
            self._log_event("audio_batch_failed", story_id=story.id, batch_job_id=job.id, error=str(exc))
            if job.status != StoryBatchJobStatus.CANCELLED:
                job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            job.missing_keys = [item.key for item in items]
            await self.batch_jobs.update(job)
            await self.session.commit()
            raise

    async def _process_reconciled_image_job(
        self,
        story: Story,
        job: StoryBatchJob,
        provider_job: types.BatchJob,
    ) -> None:
        story_json = await self.workflow._load_existing_story_json(story)
        if story_json is None:
            raise AppException("Story JSON is missing during image batch reconciliation", code="STORY_JSON_MISSING")
        if not isinstance(story.image_plan_json, dict):
            raise AppException("Image plan is missing during image batch reconciliation", code="IMAGE_PLAN_MISSING")

        items = await self._build_image_items(story, story_json, story.image_plan_json)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]
        completed_keys, failed_keys, response_summary = await self._process_image_batch_responses(
            story,
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
        await self.workflow._persist_story_content(story, story_json)

        step = await self._latest_or_create_step(story.id, StoryStepName.IMAGE_GENERATION)
        step.status = StepStatus.COMPLETED if not failed_keys else StepStatus.FAILED
        step.error_message = job.error_message
        step.response = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "completed_keys": sorted(completed_keys),
            "failed_keys": sorted(failed_keys),
        }
        step.completed_at = datetime.now(UTC)
        await self.story_steps.update(step)

        if failed_keys:
            story.status = StoryStatus.FAILED
            story.current_step = None
            story.error_message = job.error_message
            await self.stories.update(story)
        await self.session.commit()

    async def _process_reconciled_audio_job(
        self,
        story: Story,
        job: StoryBatchJob,
        provider_job: types.BatchJob,
    ) -> None:
        story_json = await self.workflow._load_existing_story_json(story)
        if story_json is None:
            raise AppException("Story JSON is missing during audio batch reconciliation", code="STORY_JSON_MISSING")

        items = self._build_audio_items(story_json, age_group=story.age_group.value)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]
        completed_keys, failed_keys, response_summary = await self._process_audio_batch_responses(
            story,
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
        await self.workflow._persist_story_content(story, story_json)

        step = await self._latest_or_create_step(story.id, StoryStepName.NARRATION_GENERATION)
        step.status = StepStatus.COMPLETED if not failed_keys else StepStatus.FAILED
        step.error_message = job.error_message
        step.response = {
            "mode": "google_batch_reconcile",
            "batch_job_id": str(job.id),
            "completed_keys": sorted(completed_keys),
            "failed_keys": sorted(failed_keys),
        }
        step.completed_at = datetime.now(UTC)
        await self.story_steps.update(step)

        if failed_keys:
            story.status = StoryStatus.FAILED
            story.current_step = None
            story.error_message = job.error_message
        else:
            story.status = StoryStatus.COMPLETED
            story.current_step = None
            story.error_message = None
            self.workflow._apply_story_metadata(story, story.story_plan_json or {}, story_json)
            await self.stories.upsert_content(story, language=DEFAULT_STORY_LANGUAGE, story_json=story_json)
        await self.stories.update(story)
        await self.session.commit()
        if not failed_keys:
            await StoryCompletionEmailService(self.session).send_story_completed(story, story_json)

    async def _process_image_batch_responses(
        self,
        story: Story,
        story_json: dict[str, Any],
        items: list[BatchImageItem],
        provider_job: types.BatchJob,
    ) -> tuple[set[str], set[str], dict[str, Any]]:
        responses = list((provider_job.dest.inlined_responses if provider_job.dest else None) or [])
        by_key = self._responses_by_key(responses)
        if not by_key and responses:
            by_key = {item.key: response for item, response in zip(items, responses, strict=False)}

        completed_keys: set[str] = set()
        failed_keys: set[str] = set()
        response_summary: dict[str, Any] = {"items": []}
        for item in items:
            inlined_response = by_key.get(item.key)
            if inlined_response is None:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "missing_response"})
                continue
            if inlined_response.error:
                failed_keys.add(item.key)
                response_summary["items"].append(
                    {"key": item.key, "status": "error", "error": self._model_dump_safe(inlined_response.error)}
                )
                continue
            if inlined_response.response is None:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "empty_response"})
                continue
            try:
                image_bytes, response_text = GoogleProvider._extract_image_from_content_response(
                    inlined_response.response
                )
                cropped = StoryService._crop_image_bytes_to_aspect_ratio(image_bytes, item.aspect_ratio)
                image_url = await self.image_storage.save_story_image(story.id, cropped, item.file_name, "")
                await self._save_image_item_result(story, story_json, item, image_url)
                completed_keys.add(item.key)
                response_summary["items"].append(
                    {"key": item.key, "status": "completed", "image_url": image_url, "response_text": response_text}
                )
            except Exception as exc:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "save_failed", "error": str(exc)})
        return completed_keys, failed_keys, response_summary

    async def _process_audio_batch_responses(
        self,
        story: Story,
        story_json: dict[str, Any],
        items: list[BatchAudioItem],
        provider_job: types.BatchJob,
    ) -> tuple[set[str], set[str], dict[str, Any]]:
        responses = list((provider_job.dest.inlined_responses if provider_job.dest else None) or [])
        by_key = self._responses_by_key(responses)
        if not by_key and responses:
            by_key = {item.key: response for item, response in zip(items, responses, strict=False)}

        completed_keys: set[str] = set()
        failed_keys: set[str] = set()
        response_summary: dict[str, Any] = {"items": []}
        for item in items:
            inlined_response = by_key.get(item.key)
            if inlined_response is None or inlined_response.response is None:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "missing_response"})
                continue
            if inlined_response.error:
                failed_keys.add(item.key)
                response_summary["items"].append(
                    {"key": item.key, "status": "error", "error": self._model_dump_safe(inlined_response.error)}
                )
                continue
            try:
                pcm_bytes = self._extract_audio_from_response(inlined_response.response)
                wav_bytes = self.tts_provider._pcm_to_wav(pcm_bytes)
                duration = self.tts_provider._pcm_duration_seconds(pcm_bytes)
                audio_url = await self.audio_storage.save_story_page_audio(
                    story_id=story.id,
                    language=DEFAULT_STORY_LANGUAGE,
                    page_number=item.page_number,
                    audio_bytes=wav_bytes,
                )
                self._set_story_json_page_audio(story_json, item, audio_url, duration)
                completed_keys.add(item.key)
                response_summary["items"].append(
                    {"key": item.key, "status": "completed", "audio_url": audio_url, "duration": round(duration, 2)}
                )
            except Exception as exc:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "save_failed", "error": str(exc)})
        return completed_keys, failed_keys, response_summary

    async def _ensure_audio_batch_submitted(self, story: Story) -> str:
        latest_audio_job = await self.batch_jobs.latest_for_story_type(story.id, StoryBatchJobType.AUDIO)
        if latest_audio_job and latest_audio_job.status in {
            StoryBatchJobStatus.SUBMITTED,
            StoryBatchJobStatus.RUNNING,
            StoryBatchJobStatus.SUCCEEDED,
        }:
            return f"Audio job already exists with status {latest_audio_job.status.value}."

        story_json = await self.workflow._load_existing_story_json(story)
        if story_json is None:
            raise AppException("Story JSON is missing before audio batch submission", code="STORY_JSON_MISSING")

        items = self._build_audio_items(story_json, age_group=story.age_group.value)
        missing = self._missing_audio_items(story_json, items)
        if not missing:
            return "No missing audio items."

        step = await self._latest_or_create_step(story.id, StoryStepName.NARRATION_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.error_message = None
        step.prompt = self._json_safe(
            {"mode": "google_batch_reconcile", "language": DEFAULT_STORY_LANGUAGE, "page_count": len(items)}
        )
        await self.story_steps.update(step)

        story.status = StoryStatus.IN_PROGRESS
        story.current_step = StoryStepName.NARRATION_GENERATION.value
        story.error_message = None
        await self.stories.update(story)

        audio_job = await self._submit_audio_batch_job_only(story, missing, attempt=1)
        await self.session.commit()
        return f"Audio job submitted: {audio_job.id}."

    async def _submit_audio_batch_job_only(
        self,
        story: Story,
        items: list[BatchAudioItem],
        *,
        attempt: int,
    ) -> StoryBatchJob:
        requests = [self._build_audio_inlined_request(item) for item in items]
        model = settings.GOOGLE_TTS_MODEL.removeprefix("models/")
        job = await self.batch_jobs.create(
            story_id=story.id,
            job_type=StoryBatchJobType.AUDIO,
            attempt=attempt,
            expected_item_count=len(items),
            request_keys=[item.key for item in items],
            provider_model=model,
            request_payload={
                "mode": "audio",
                "attempt": attempt,
                "language": DEFAULT_STORY_LANGUAGE,
                "voice": settings.GOOGLE_TTS_VOICE,
                "items": [
                    {
                        "key": item.key,
                        "page_number": item.page_number,
                        "text": item.text,
                        "text_chars": len(item.text),
                        "prompt": item.prompt,
                        "pace": item.pace,
                        "voice_style": item.voice_style,
                        "tone": item.tone,
                        "emotion": item.emotion,
                    }
                    for item in items
                ],
            },
        )
        await self.session.flush()
        provider_job = await self.google_client.aio.batches.create(
            model=model,
            src=requests,
            config={"display_name": f"story-{story.id}-audio-attempt-{attempt}"},
        )
        job.provider_job_name = provider_job.name
        job.provider_state = self._job_state_name(provider_job)
        await self.batch_jobs.update(job)
        self._log_event(
            "audio_batch_submitted_by_reconcile",
            story_id=story.id,
            batch_job_id=job.id,
            provider_job_name=job.provider_job_name,
            provider_state=job.provider_state,
            item_count=len(items),
        )
        return job

    async def _latest_or_create_step(self, story_id: UUID, step_name: StoryStepName):
        step = await self.story_steps.latest_for_story_step(story_id, step_name)
        if step is None:
            step = await self.story_steps.create(story_id, step_name)
        return step

    async def _mark_story_failed(self, story_id: UUID, error_message: str) -> None:
        story = await self.stories.get_by_id(story_id)
        if story is None:
            return
        story.status = StoryStatus.FAILED
        story.current_step = None
        story.error_message = error_message
        await self.stories.update(story)

    async def _wait_for_provider_job(self, job: StoryBatchJob) -> types.BatchJob:
        if not job.provider_job_name:
            raise AppException("Batch job was not submitted to Google", code="BATCH_JOB_NOT_SUBMITTED")

        waited = 0
        poll_interval = max(5, settings.STORY_BATCH_POLL_INTERVAL_SECONDS)
        max_wait = max(poll_interval, settings.STORY_BATCH_MAX_WAIT_SECONDS)
        last_logged_state: str | None = None
        poll_count = 0
        while waited <= max_wait:
            provider_job = await self.google_client.aio.batches.get(name=job.provider_job_name)
            state_name = self._job_state_name(provider_job)
            job.provider_state = state_name
            if state_name != last_logged_state or poll_count % 20 == 0:
                self._log_event(
                    "batch_poll",
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=job.job_type.value,
                    provider_state=state_name,
                    waited_seconds=waited,
                )
                last_logged_state = state_name
            poll_count += 1
            if state_name in self.SUCCEEDED_STATES:
                job.status = StoryBatchJobStatus.SUCCEEDED
                await self.batch_jobs.update(job)
                await self.session.commit()
                self._log_event(
                    "batch_provider_succeeded",
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=job.job_type.value,
                    waited_seconds=waited,
                )
                return provider_job
            if state_name in self.CANCELLED_STATES:
                job.status = StoryBatchJobStatus.CANCELLED
                job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
                await self.batch_jobs.update(job)
                await self.session.commit()
                self._log_event(
                    "batch_provider_cancelled",
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=job.job_type.value,
                    provider_state=state_name,
                )
                raise AppException(job.error_message, code="GOOGLE_BATCH_CANCELLED")
            if state_name in self.FAILED_STATES:
                job.status = StoryBatchJobStatus.FAILED
                job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
                await self.batch_jobs.update(job)
                await self.session.commit()
                self._log_event(
                    "batch_provider_failed",
                    story_id=job.story_id,
                    batch_job_id=job.id,
                    job_type=job.job_type.value,
                    provider_state=state_name,
                    error=job.error_message,
                )
                raise AppException(job.error_message, code="GOOGLE_BATCH_FAILED")

            job.status = StoryBatchJobStatus.RUNNING
            await self.batch_jobs.update(job)
            await self.session.commit()
            await asyncio.sleep(poll_interval)
            waited += poll_interval

        error = f"Google batch job {job.provider_job_name} did not complete within {max_wait} seconds"
        self._log_event("batch_provider_timeout", story_id=job.story_id, batch_job_id=job.id, error=error)
        raise AppException(error, code="GOOGLE_BATCH_TIMEOUT")

    async def _build_image_items(
        self,
        story: Story,
        story_json: dict[str, Any],
        image_plan: dict[str, Any],
    ) -> list[BatchImageItem]:
        image_generation_template = load_prompt("prompts/story/image_generation_prompt.txt")
        visual_bible = image_plan.get("visual_bible", {})
        is_custom_story = StoryService._is_custom_story_workflow_record(story)
        if StoryService._use_child_character(story):
            child = await self.children.get_for_user(story.user_id, story.child_id)
            if child is None:
                raise NotFoundException("Child profile not found during batch image generation")
            if not child.character_image_url:
                raise AppException("Generated character image is required for story image generation", code="NO_CHARACTER_IMAGE")
            character_context = StoryService._build_character_reference_context(child)
        else:
            character_context = StoryService._build_imagined_cast_context(story, story_plan={"visual_bible": visual_bible})
        pages = story_json.get("pages", [])

        items: list[BatchImageItem] = []
        cover = image_plan.get("cover") or {}
        if not cover.get("image_prompt"):
            raise AppException("Image plan is missing cover image prompt", code="IMAGE_PLAN_MISSING_COVER")
        story_title = story_json.get("title") or story.title or ""
        items.append(
            BatchImageItem(
                key="cover",
                page_type="cover",
                page_number=0,
                page_data=cover,
                source_image_prompt=cover["image_prompt"],
                rendered_prompt=self._render_custom_story_image_prompt(
                    image_generation_template,
                    visual_bible,
                    cover["image_prompt"],
                    character_context,
                    page_type="cover",
                    target_aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                    page_data=cover,
                    story_title=story_title,
                    is_custom_story=is_custom_story,
                ),
                aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                image_size=settings.STORY_COVER_IMAGE_SIZE,
                file_name="cover.png",
                text="",
            )
        )

        for img_page in image_plan.get("pages") or []:
            if not isinstance(img_page, dict):
                continue
            page_number = int(img_page.get("page_number") or 0)
            image_prompt = img_page.get("image_prompt")
            if page_number <= 0 or not image_prompt:
                continue
            text = ""
            if page_number <= len(pages) and isinstance(pages[page_number - 1], dict):
                text = pages[page_number - 1].get("text") or ""
            items.append(
                BatchImageItem(
                    key=f"page_{page_number}",
                    page_type="page",
                    page_number=page_number,
                    page_data=img_page,
                    source_image_prompt=image_prompt,
                    rendered_prompt=self._render_custom_story_image_prompt(
                        image_generation_template,
                        visual_bible,
                        image_prompt,
                        character_context,
                        page_type="story_page",
                        target_aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
                        page_data=img_page,
                        story_title=story_title,
                        is_custom_story=is_custom_story,
                    ),
                    aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
                    image_size=settings.STORY_PAGE_IMAGE_SIZE,
                    file_name=f"page_{page_number}.png",
                    text=text,
                )
            )

        back_cover = image_plan.get("back_cover") or {}
        if not back_cover.get("image_prompt"):
            raise AppException("Image plan is missing back cover image prompt", code="IMAGE_PLAN_MISSING_BACK_COVER")
        back_cover_page_number = len(pages) + 1
        items.append(
            BatchImageItem(
                key="back_cover",
                page_type="back_cover",
                page_number=back_cover_page_number,
                page_data=back_cover,
                source_image_prompt=back_cover["image_prompt"],
                rendered_prompt=self._render_custom_story_image_prompt(
                    image_generation_template,
                    visual_bible,
                    back_cover["image_prompt"],
                    character_context,
                    page_type="back_cover",
                    target_aspect_ratio=settings.STORY_BACK_COVER_ASPECT_RATIO,
                    page_data=back_cover,
                    story_title=story_title,
                    is_custom_story=is_custom_story,
                ),
                aspect_ratio=settings.STORY_BACK_COVER_ASPECT_RATIO,
                image_size=settings.STORY_BACK_COVER_IMAGE_SIZE,
                file_name="back_cover.png",
                text="",
            )
        )
        return items

    @classmethod
    def _render_custom_story_image_prompt(
        cls,
        template: str,
        visual_bible: dict[str, Any],
        image_prompt: str,
        character_context: dict[str, Any],
        *,
        page_type: str,
        target_aspect_ratio: str,
        page_data: dict[str, Any],
        story_title: str,
        is_custom_story: bool,
    ) -> str:
        rendered_prompt = StoryService._render_story_image_prompt(
            template,
            visual_bible,
            image_prompt,
            character_context,
            page_type=page_type,
            target_aspect_ratio=target_aspect_ratio,
            page_data=page_data,
            story_title=story_title,
        )
        if not is_custom_story:
            return rendered_prompt
        rendered_prompt = rendered_prompt.replace(
            "Expressions, pose, camera angle, and scene clothing may vary only when the\n"
            "Current Page Data requests it. The underlying face/head identity must stay the\n"
            "same. Do not make a new variant that merely resembles the reference.",
            "Expressions, pose, and camera angle may vary only when the Current Page Data requests it.\n"
            "The locked story outfit, hairstyle, body build, height, body proportions, and face/head identity must stay the same.\n"
            "Do not make a new variant that merely resembles the reference.",
        )
        lock_block = cls._custom_visible_character_lock_block(visual_bible, page_data)
        return f"{rendered_prompt}\n\n{lock_block}" if lock_block else rendered_prompt

    @staticmethod
    def _custom_visible_character_lock_block(
        visual_bible: dict[str, Any],
        page_data: dict[str, Any],
    ) -> str:
        if not isinstance(visual_bible, dict) or not isinstance(page_data, dict):
            return ""

        explicit_ids = {
            str(value).strip()
            for value in page_data.get("reference_character_ids") or []
            if isinstance(value, str) and value.strip()
        }
        character_names = {
            StoryService._character_reference_name_key(value)
            for value in page_data.get("characters_present") or []
            if isinstance(value, str) and value.strip()
        }

        hero = visual_bible.get("hero") if isinstance(visual_bible.get("hero"), dict) else {}
        recurring = visual_bible.get("recurring_characters")
        recurring_characters = [item for item in recurring if isinstance(item, dict)] if isinstance(recurring, list) else []

        locks: list[str] = []
        hero_name = str(hero.get("name") or "hero child").strip()
        hero_id = str(hero.get("character_id") or "hero_child").strip()
        if hero and (
            hero_id in explicit_ids
            or StoryService._character_reference_name_key(hero_name) in character_names
        ):
            appearance = str(hero.get("appearance") or "").strip()
            outfit = str(hero.get("outfit") or "").strip()
            footwear = str(hero.get("footwear") or "").strip()
            signature_item = str(hero.get("signature_item") or "").strip()
            locks.append(
                "\n".join(
                    [
                        f"- {hero_id} / {hero_name}: use the attached hero reference as the face, hairline, hair, eyes, and age source.",
                        f"  Locked appearance: {appearance}",
                        f"  Locked story outfit: {outfit}",
                        f"  Locked footwear: {footwear}" if footwear else "",
                        f"  Locked signature item: {signature_item}" if signature_item else "",
                        "  Hair lock: keep the exact hairstyle from the reference and Visual Bible on every page; no loose hair, no open hair, no shortened hair, no alternate pigtail placement, no changed hairline.",
                        "  Body-scale lock: same reusable child body model in every image; same child height, build, limb proportions, shoulder width, head-to-body ratio, natural child hands and feet, and age appearance.",
                        "  Relative-scale lock: preserve the hero's scale relative to visible companions and nearby objects; camera angle and pose may change, but body build, body proportions, height, hairstyle, and outfit must not change.",
                        "  Footwear etiquette: wear the locked footwear in footwear-appropriate scenes; in temples, prayer rooms, sacred spaces, no-shoe home areas, beds, mattresses, or bedding, use bare feet or socks and place the exact locked footwear neatly nearby if visible.",
                        "  Negative lock: no changed body build, no changed height, no thinner/fatter redesign, no younger/older redesign, no changed outfit, no changed footwear, no missing footwear in footwear-appropriate scenes, no outdoor shoes on beds or sacred/no-shoe spaces, no changed hairstyle.",
                    ]
                )
            )

        for character in recurring_characters:
            character_id = str(character.get("character_id") or "").strip()
            name = str(character.get("name") or "").strip()
            if not character_id and not name:
                continue
            if character_id not in explicit_ids and StoryService._character_reference_name_key(name) not in character_names:
                continue
            appearance = str(character.get("appearance") or "").strip()
            outfit = str(character.get("outfit") or "").strip()
            locks.append(
                "\n".join(
                    [
                        f"- {character_id or name} / {name or character_id}: use only this character's attached reference and Visual Bible lock.",
                        f"  Locked appearance: {appearance}",
                        f"  Locked outfit/accessories: {outfit}" if outfit else "",
                        "  Scale lock: keep the same body scale, build, face/head shape, hair, outfit, and accessories whenever this character appears.",
                    ]
                )
            )

        if not locks:
            return ""

        return (
            "---\n"
            "## Custom Visible Character Lock\n\n"
            "Apply this lock to this image item. It overrides any shorter or incomplete character wording in Current Page Data.\n"
            "Only draw the visible character identities listed here; do not borrow the face, hair, outfit, or body scale of a non-visible character.\n\n"
            + "\n".join(locks)
        )

    async def _missing_image_items(
        self,
        story: Story,
        story_json: dict[str, Any],
        items: list[BatchImageItem],
    ) -> list[BatchImageItem]:
        missing: list[BatchImageItem] = []
        for item in items:
            existing = await self.story_pages.get_by_story_page(story.id, item.page_number)
            if existing and existing.image_url and await self._image_url_exists(existing.image_url):
                await self._save_image_item_result(story, story_json, item, existing.image_url)
                continue
            story_json_image_url = self._story_json_image_url(story_json, item)
            if story_json_image_url and await self._image_url_exists(story_json_image_url):
                continue
            missing.append(item)
        return missing

    async def _save_image_item_result(
        self,
        story: Story,
        story_json: dict[str, Any],
        item: BatchImageItem,
        image_url: str,
    ) -> None:
        if item.page_type == "cover":
            await self.story_pages.upsert_page(
                story.id,
                page_number=0,
                page_type="cover",
                text="",
                image_prompt=item.source_image_prompt,
                image_url=image_url,
            )
            story_json["cover_image_url"] = image_url
            await self.workflow._persist_story_content(story, story_json)
            return

        if item.page_type == "back_cover":
            await self.story_pages.upsert_page(
                story.id,
                page_number=item.page_number,
                page_type="back_cover",
                text="",
                image_prompt=item.source_image_prompt,
                image_url=image_url,
            )
            story_json["back_cover_image_url"] = image_url
            await self.workflow._persist_story_content(story, story_json)
            return

        await self.story_pages.upsert_page(
            story.id,
            page_number=item.page_number,
            page_type="page",
            text=item.text,
            image_prompt=item.source_image_prompt,
            image_url=image_url,
        )
        StoryService._set_story_json_page_image_url(story_json, item.page_number, image_url)
        await self.workflow._persist_story_content(story, story_json)

    async def _image_url_exists(self, image_url: str) -> bool:
        try:
            return bool(await self.image_storage.get_image_bytes(image_url))
        except Exception:
            logger.warning("Story batch image URL is not readable and will be regenerated: %s", image_url)
            return False

    @staticmethod
    def _story_json_image_url(story_json: dict[str, Any], item: BatchImageItem) -> str | None:
        if item.page_type == "cover":
            return story_json.get("cover_image_url")
        if item.page_type == "back_cover":
            return story_json.get("back_cover_image_url")
        for page in story_json.get("pages") or []:
            if isinstance(page, dict) and page.get("page_number") == item.page_number:
                return page.get("image_url")
        return None

    async def _load_reference_image_blobs(self, story: Story) -> list[BatchImageReference]:
        image_plan = getattr(story, "image_plan_json", None)
        manifest = (
            StoryService._character_reference_manifest(image_plan)
            if isinstance(image_plan, dict)
            else []
        )
        references: list[BatchImageReference] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(manifest, start=1):
            image_url = str(item.get("reference_image_url") or item.get("image_url") or "").strip()
            character_id = str(item.get("character_id") or "").strip()
            if not image_url or not character_id or character_id in seen_ids:
                continue
            references.append(
                BatchImageReference(
                    character_id=character_id,
                    name=str(item.get("name") or character_id),
                    role=str(item.get("role") or "character_reference"),
                    image_url=image_url,
                    part=await self._image_url_to_part(image_url),
                    priority=int(item.get("priority") or index),
                )
            )
            seen_ids.add(character_id)

        if StoryService._use_child_character(story) and "hero_child" not in seen_ids:
            child = await self.children.get_for_user(story.user_id, story.child_id)
            if child is None:
                raise NotFoundException("Child profile not found during batch reference loading")
            if not child.character_image_url:
                raise AppException("Generated character image is required for story image generation", code="NO_CHARACTER_IMAGE")
            references.insert(
                0,
                BatchImageReference(
                    character_id="hero_child",
                    name=child.first_name or "Child",
                    role="hero_child",
                    image_url=child.character_image_url,
                    part=await self._image_url_to_part(child.character_image_url),
                    priority=0,
                ),
            )
        return references

    async def _image_url_to_part(self, url: str | None) -> types.Part:
        if not url:
            raise AppException("Reference image URL is missing", code="MISSING_REFERENCE_IMAGE")
        image_bytes = await self.image_storage.get_image_bytes(url)
        mime_type = self._detect_image_mime_type(image_bytes)
        return types.Part(inline_data=types.Blob(data=image_bytes, mime_type=mime_type))

    @staticmethod
    def _detect_image_mime_type(image_bytes: bytes) -> str:
        with Image.open(BytesIO(image_bytes)) as image:
            return Image.MIME.get(image.format or "", "image/png")

    @staticmethod
    def _max_character_references_for_model(model: str) -> int:
        normalized = (model or "").lower()
        return 5 if "pro" in normalized and "image" in normalized else 4

    @staticmethod
    def _reference_text_pool(item: BatchImageItem) -> str:
        page_data = item.page_data if isinstance(item.page_data, dict) else {}
        values: list[str] = [
            item.source_image_prompt,
            item.rendered_prompt,
            str(page_data.get("visual_focus") or ""),
            str(page_data.get("scene_action") or ""),
            str(page_data.get("environment") or ""),
        ]
        characters_present = page_data.get("characters_present")
        if isinstance(characters_present, list):
            values.extend(str(value) for value in characters_present if isinstance(value, str))
        return " ".join(values).lower()

    @classmethod
    def _select_reference_images_for_item(
        cls,
        item: BatchImageItem,
        reference_images: list[BatchImageReference],
        *,
        model: str,
        strict_page_refs: bool = False,
    ) -> list[BatchImageReference]:
        if not reference_images:
            return []

        page_data = item.page_data if isinstance(item.page_data, dict) else {}
        explicit_ids = {
            str(value).strip()
            for value in page_data.get("reference_character_ids") or []
            if isinstance(value, str) and value.strip()
        }
        characters_present = {
            StoryService._character_reference_name_key(value)
            for value in page_data.get("characters_present") or []
            if isinstance(value, str)
        }
        text_pool = "" if strict_page_refs else cls._reference_text_pool(item)
        selected: list[BatchImageReference] = []

        hero = next((reference for reference in reference_images if reference.character_id == "hero_child"), None)
        if hero is not None and (
            not strict_page_refs
            or hero.character_id in explicit_ids
            or StoryService._character_reference_name_key(hero.name) in characters_present
        ):
            selected.append(hero)

        for reference in sorted(reference_images, key=lambda value: value.priority):
            if reference.character_id == "hero_child":
                continue
            name_key = StoryService._character_reference_name_key(reference.name)
            should_include = (
                reference.character_id in explicit_ids
                or name_key in characters_present
                or (not strict_page_refs and reference.name and reference.name.lower() in text_pool)
            )
            if should_include:
                selected.append(reference)

        max_refs = cls._max_character_references_for_model(model)
        return selected[:max_refs]

    def _build_image_inlined_request(
        self,
        item: BatchImageItem,
        *,
        reference_images: list[BatchImageReference] | None,
    ) -> types.InlinedRequest:
        prompt = (
            self._story_reference_image_prompt(item.rendered_prompt, reference_images=reference_images or [])
            if reference_images
            else self._story_text_only_image_prompt(item.rendered_prompt)
        )
        parts = [types.Part(text=prompt)]
        for reference in reference_images or []:
            parts.append(reference.part)
        return types.InlinedRequest(
            contents=[
                types.Content(
                    role="user",
                    parts=parts,
                )
            ],
            metadata={"key": item.key},
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(aspect_ratio=item.aspect_ratio),
            ),
        )

    @staticmethod
    def _story_text_only_image_prompt(prompt: str) -> str:
        return (
            "Generate one polished children's storybook illustration from text only. No character reference image "
            "is attached. Use the Visual Bible inside the rendered prompt as the complete model sheet for every "
            "character. Character consistency is more important than decorative scene details. Preserve each "
            "character's locked face or head shape, hair or fur, eyes, skin or body color, outfit, shoes, "
            "accessories, size, distinctive features, color palette, and the single storybook style across the "
            "cover, every page, and the back cover. Respect basic scene etiquette: in temples, prayer rooms, "
            "sacred spaces, no-shoe home areas, beds, mattresses, or bedding, do not draw outdoor shoes on feet "
            "or on the bed; use bare feet or socks and place the exact locked footwear neatly nearby if visible. "
            "This does not change the locked footwear design. Do not redesign characters between pages. Use the same "
            "reusable character models implied by the Visual Bible in each scene:\n\n"
            f"{prompt}"
        )

    @staticmethod
    def _story_reference_image_prompt(
        prompt: str,
        reference_images: list[BatchImageReference] | None = None,
    ) -> str:
        reference_images = reference_images or []
        if len(reference_images) <= 1:
            reference_instruction = (
                "\nThe only attached image after this prompt is the generated Master Character Reference Portrait "
                "from character_image_url. It is the PRIMARY visual identity reference. Match the master character's "
                "face, facial proportions, eye shape, natural eye size, hairstyle, hairline, skin tone, and age appearance. "
            )
        else:
            reference_lines = [
                "\nAttached images after this prompt are named character identity references in this exact order.",
                "Use each reference only for the matching character ID/name:",
            ]
            for index, reference in enumerate(reference_images, start=1):
                reference_lines.append(
                    f"{index}. character_id={reference.character_id}; name={reference.name}; role={reference.role}"
                )
            reference_lines.append(
                "Preserve each attached character's face/head shape, facial proportions, eyes, hairstyle or "
                "fur/body pattern, colors, age/scale, outfit/accessories, and distinctive features. "
            )
            reference_instruction = "\n".join(reference_lines)

        consistency_instruction = (
            reference_instruction +
            "No original child avatar photo is attached for story image generation. Do not redesign the face or make "
            "the child look older or younger. Use the Character Identity Lock inside the rendered prompt for written "
            "identity and age guidance. "
            "Use the Visual Bible and scene prompt for the single locked story outfit, shoes, accessories, "
            "body scale, rendering style, and environment. Respect basic scene etiquette: in temples, prayer rooms, "
            "sacred spaces, no-shoe home areas, beds, mattresses, or bedding, do not draw outdoor shoes on feet "
            "or on the bed; use bare feet or socks and place the exact locked footwear neatly nearby if visible. "
            "This does not change the locked footwear design. Do not copy portrait clothing, portrait crop, "
            "white studio background, or head-and-shoulders framing. If the scene prompt conflicts with "
            "the master character face, hairstyle, or age appearance, keep the master facial identity and "
            "only change the action/environment. In water park, pool, beach, splash pad, rain, or water-play "
            "scenes, all children and adults must wear modest family-friendly clothing: rash guards or "
            "t-shirts covering shoulders and the upper body, knee-length shorts or leggings, and water shoes. "
            "Use covered water-play outfits for every visible person and keep background people tiny, "
            "simplified, fully clothed, or omitted.\n"
        )

        return (
            "Generate one polished children's storybook illustration. Character consistency is more important "
            "than scene costume, theme costume, or decorative story details."
            f"{consistency_instruction}"
            "Use a premium semi-realistic 3D storybook style while following this scene prompt. The child must "
            "match the Character Identity Lock and keep the same master-character face and hairstyle in every image. "
            "Use the same single 3D character model across the full book, as if the Master Character Reference "
            "Image has been posed in each scene. Character likeness, age consistency, "
            "modest child-safe clothing coverage, and family-friendly composition are more important than "
            "decorative scene details:\n\n"
            f"{prompt}"
        )

    def _build_audio_items(
        self,
        story_json: dict[str, Any],
        *,
        age_group: str | None = None,
    ) -> list[BatchAudioItem]:
        pages = story_json.get("pages", [])
        narration_age_group = age_group or story_json.get("age_group")
        moral = story_json.get("moral") if isinstance(story_json.get("moral"), dict) else {}
        default_speech_narration = (
            moral.get("speech_narration", {}) if isinstance(moral.get("speech_narration"), dict) else {}
        )
        items: list[BatchAudioItem] = []
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            page_number = int(page.get("page_number") or index + 1)
            text = (page.get("text") or "").strip()
            if not text:
                continue
            narration = page.get("narration") if isinstance(page.get("narration"), dict) else {}
            speech = page.get("speech_narration") or default_speech_narration or {}
            emotion = page.get("emotion") or speech.get("emotion", "wonder")
            derived_narration = build_page_narration(emotion, narration_age_group)
            narration = {
                "tone": narration.get("tone") or derived_narration["tone"],
                "pace": narration.get("pace") or derived_narration["pace"],
                "voice_style": narration.get("voice_style") or derived_narration["voice_style"],
            }
            page["narration"] = narration
            pace = narration["pace"]
            voice_style = narration["voice_style"]
            tone = narration["tone"]
            prompt = self.tts_provider.build_prompt(
                text,
                pace=pace,
                language=DEFAULT_STORY_LANGUAGE,
                voice_style=voice_style,
                tone=tone,
                emotion=emotion,
            )
            items.append(
                BatchAudioItem(
                    key=f"page_{page_number}",
                    page_number=page_number,
                    text=text,
                    prompt=prompt,
                    pace=pace,
                    voice_style=voice_style,
                    tone=tone,
                    emotion=emotion,
                )
            )
        return items

    @staticmethod
    def _missing_audio_items(story_json: dict[str, Any], items: list[BatchAudioItem]) -> list[BatchAudioItem]:
        pages_by_number = {
            int(page.get("page_number") or idx + 1): page
            for idx, page in enumerate(story_json.get("pages") or [])
            if isinstance(page, dict)
        }
        missing: list[BatchAudioItem] = []
        for item in items:
            page = pages_by_number.get(item.page_number) or {}
            if not page.get("audio_url") or not page.get("duration") or not page.get("word_timestamps"):
                missing.append(item)
        return missing

    def _build_audio_inlined_request(self, item: BatchAudioItem) -> types.InlinedRequest:
        return types.InlinedRequest(
            contents=[types.Content(role="user", parts=[types.Part(text=item.prompt)])],
            metadata={"key": item.key},
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=settings.GOOGLE_TTS_VOICE,
                        )
                    )
                ),
            ),
        )

    @staticmethod
    def _extract_audio_from_response(response: types.GenerateContentResponse) -> bytes:
        for part in response.parts or []:
            inline_data = part.inline_data
            if inline_data and inline_data.data:
                data = inline_data.data
                if isinstance(data, bytes):
                    return data
                return base64.b64decode(data)
        raise AppException("Gemini TTS batch response returned no audio data", code="EMPTY_TTS_RESPONSE")

    def _set_story_json_page_audio(
        self,
        story_json: dict[str, Any],
        item: BatchAudioItem,
        audio_url: str,
        duration: float,
    ) -> None:
        for index, page in enumerate(story_json.get("pages") or []):
            if not isinstance(page, dict):
                continue
            page_number = int(page.get("page_number") or index + 1)
            if page_number != item.page_number:
                continue
            page["tts_prompt"] = item.prompt
            page["tts_skipped"] = False
            page["tts_model"] = settings.GOOGLE_TTS_MODEL
            page["tts_voice"] = settings.GOOGLE_TTS_VOICE
            page["audio_url"] = audio_url
            page["duration"] = round(duration, 2)
            page["word_timestamps"] = generate_word_timestamps(item.text, duration)
            return

    def _apply_skipped_tts(self, story_json: dict[str, Any], items: list[BatchAudioItem]) -> None:
        items_by_number = {item.page_number: item for item in items}
        for index, page in enumerate(story_json.get("pages") or []):
            if not isinstance(page, dict):
                continue
            page_number = int(page.get("page_number") or index + 1)
            item = items_by_number.get(page_number)
            if item is None:
                continue
            page.pop("audio_url", None)
            page.pop("duration", None)
            page.pop("word_timestamps", None)
            page["tts_prompt"] = item.prompt
            page["tts_skipped"] = True
            page["tts_model"] = settings.GOOGLE_TTS_MODEL
            page["tts_voice"] = settings.GOOGLE_TTS_VOICE

    @staticmethod
    def _responses_by_key(responses: list[types.InlinedResponse]) -> dict[str, types.InlinedResponse]:
        by_key: dict[str, types.InlinedResponse] = {}
        for response in responses:
            metadata = response.metadata or {}
            key = metadata.get("key")
            if key:
                by_key[key] = response
        return by_key

    @staticmethod
    def _job_state_name(job: types.BatchJob) -> str:
        state = getattr(job, "state", None)
        return getattr(state, "name", None) or str(state or "")

    @staticmethod
    def _batch_cancel_response(story: Story, job: StoryBatchJob, message: str) -> dict[str, Any]:
        return {
            "story_id": story.id,
            "batch_job_id": job.id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "provider_job_name": job.provider_job_name,
            "provider_state": job.provider_state,
            "story_status": story.status.value,
            "message": message,
        }

    @staticmethod
    def _model_dump_safe(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return str(value)

    @staticmethod
    def _json_safe(value: Any) -> str:
        import json

        return json.dumps(value, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _image_item_payload(
        item: BatchImageItem,
        *,
        reference_images: list[BatchImageReference] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "key": item.key,
            "page_type": item.page_type,
            "page_number": item.page_number,
            "page_data": item.page_data,
            "source_image_prompt": item.source_image_prompt,
            "rendered_prompt": item.rendered_prompt,
            "aspect_ratio": item.aspect_ratio,
            "image_size": item.image_size,
            "file_name": item.file_name,
        }
        if reference_images is not None:
            payload["reference_character_ids_used"] = [reference.character_id for reference in reference_images]
            payload["reference_image_urls_used"] = [reference.image_url for reference in reference_images]
        return payload
