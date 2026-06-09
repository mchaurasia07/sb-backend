"""Generic story image batch submission and reconciliation."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID

from fastapi import status
from google import genai
from google.genai import types
from openai import AsyncOpenAI
from openai.types import Batch
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.generic_story_batch_job import GenericStoryBatchJob
from app.entity.generic_story_workflow import GenericStoryWorkflow, GenericStoryWorkflowStatus, GenericStoryWorkflowStep
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.model.request.generic_story_workflow import GenericStoryWorkflowExecuteRequest
from app.model.response.generic_story import GenericStoryBatchImageSubmitResponse
from app.repository.generic_story_batch_job_repository import GenericStoryBatchJobRepository
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.generic_story_workflow_repository import GenericStoryWorkflowRepository
from app.service.ai.google_provider import GoogleProvider
from app.service.generic_story_workflow_service import GenericStoryWorkflowService
from app.service.image_storage_provider import get_image_storage_service
from app.service.story_service import StoryService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenericBatchImageItem:
    key: str
    page_type: str
    page_number: int
    page_data: dict[str, Any]
    source_image_prompt: str
    rendered_prompt: str
    aspect_ratio: str
    file_name: str


class GenericStoryBatchService:
    """Submits and reconciles generic-story image batches only."""

    SUCCEEDED_STATES = {"JOB_STATE_SUCCEEDED", "SUCCEEDED"}
    CANCELLED_STATES = {"JOB_STATE_CANCELLED", "CANCELLED"}
    FAILED_STATES = {"JOB_STATE_FAILED", "JOB_STATE_EXPIRED", "FAILED"}
    OPENAI_IMAGE_BATCH_ENDPOINT = "/v1/responses"
    OPENAI_SUCCEEDED_STATES = {"completed"}
    OPENAI_CANCELLED_STATES = {"cancelled"}
    OPENAI_FAILED_STATES = {"failed", "expired"}
    WORKFLOW_MULTI_IMAGE_MODE = "generic_story_workflow_multi_image_pages"

    def __init__(self, session: AsyncSession):
        self.session = session
        self.generic_stories = GenericStoryRepository(session)
        self.workflows = GenericStoryWorkflowRepository(session)
        self.batch_jobs = GenericStoryBatchJobRepository(session)
        self.image_storage = get_image_storage_service()
        self.google_client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.openai_client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            organization=settings.OPENAI_ORG_ID or None,
            project=settings.OPENAI_PROJECT_ID or None,
        )

    @staticmethod
    def _log_event(event: str, **fields: Any) -> None:
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        logger.info("[generic_story_batch] event=%s %s", event, details)

    async def cancel_batch_job(
        self,
        *,
        user_id: UUID,
        generic_story_id: UUID,
        batch_job_id: UUID,
    ) -> dict[str, Any]:
        """Cancel a submitted Google Batch job for a generic story workflow."""
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        job = await self.batch_jobs.get_for_story(generic_story_id, batch_job_id)
        if job is None:
            raise NotFoundException("Generic story batch job not found", "GENERIC_STORY_BATCH_JOB_NOT_FOUND")

        workflow = await self.workflows.get_for_user(user_id, job.workflow_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")

        if job.status == StoryBatchJobStatus.SUCCEEDED:
            raise AppException(
                "Completed batch jobs cannot be cancelled",
                status.HTTP_409_CONFLICT,
                "BATCH_JOB_ALREADY_COMPLETED",
            )

        if job.status == StoryBatchJobStatus.CANCELLED:
            return self._batch_cancel_response(workflow, job, "Batch job was already cancelled")

        provider = self._provider_name(getattr(job, "provider", "google"))
        if not job.provider_job_name:
            raise AppException(
                f"Batch job has not been submitted to {provider} yet",
                status.HTTP_409_CONFLICT,
                "BATCH_JOB_NOT_SUBMITTED",
            )

        try:
            if provider == "openai":
                provider_job = await self.openai_client.batches.cancel(job.provider_job_name)
                provider_state = self._openai_job_state_name(provider_job)
            else:
                await self.google_client.aio.batches.cancel(name=job.provider_job_name)
                provider_job = await self.google_client.aio.batches.get(name=job.provider_job_name)
                provider_state = self._job_state_name(provider_job)
        except Exception as exc:
            raise AppException(
                f"Failed to cancel {provider} batch job: {exc}",
                status.HTTP_502_BAD_GATEWAY,
                "BATCH_CANCEL_FAILED",
            ) from exc

        job.status = StoryBatchJobStatus.CANCELLED
        job.provider_state = provider_state or "CANCEL_REQUESTED"
        job.error_message = "Cancelled by user request"
        if job.request_keys:
            job.missing_keys = job.request_keys
        await self.batch_jobs.update(job)

        if workflow.status == GenericStoryWorkflowStatus.IN_PROGRESS.value:
            workflow.status = GenericStoryWorkflowStatus.FAILED.value
            workflow.current_step = None
            workflow.error_message = f"Batch {job.job_type.value} job cancelled by user request"
            await self.workflows.update(workflow)

        await self.session.commit()
        return self._batch_cancel_response(workflow, job, "Batch job cancelled successfully")

    async def submit_image_batch(
        self,
        *,
        user_id: UUID,
        generic_story_id: UUID,
        force: bool = False,
        page_numbers: set[int] | None = None,
        provider: str = "google",
    ) -> GenericStoryBatchImageSubmitResponse:
        provider = self._provider_name(provider)
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        workflow = await self.workflows.latest_for_user_generic_story(user_id, generic_story_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")
        if not isinstance(workflow.image_plan_json, dict):
            raise AppException("Generic story workflow has no image plan", code="GENERIC_IMAGE_PLAN_MISSING")

        latest = await self.batch_jobs.latest_for_story_type(
            generic_story_id,
            StoryBatchJobType.IMAGE,
            provider=provider,
        )
        if latest and latest.status in {StoryBatchJobStatus.SUBMITTED, StoryBatchJobStatus.RUNNING}:
            return self._submit_response(
                workflow=workflow,
                job=latest,
                submitted_count=len(latest.request_keys or []),
                message=f"Image batch already exists with provider {latest.provider} and status {latest.status.value}",
            )

        story_json = self._story_json_for_image_plan(workflow, generic_story)
        items = self._build_image_items(workflow, story_json)
        if page_numbers:
            items = [item for item in items if item.page_type == "page" and item.page_number in page_numbers]
        if not items:
            raise AppException(
                "Generic story image batch has no matching prompts to generate",
                code="EMPTY_GENERIC_IMAGE_BATCH",
                details={"page_numbers": sorted(page_numbers) if page_numbers else None},
            )

        missing = items if force else await self._missing_image_items(story_json, items)
        if not missing:
            workflow.cover_image = story_json.get("cover_image_url") or workflow.cover_image
            workflow.story_json = story_json
            workflow.status = GenericStoryWorkflowStatus.COMPLETED.value
            workflow.current_step = None
            await self.workflows.update(workflow)
            await self._apply_image_urls_to_contents(
                generic_story,
                story_json,
                workflow=workflow,
            )
            await self.session.commit()
            return GenericStoryBatchImageSubmitResponse(
                generic_story_id=generic_story_id,
                workflow_id=workflow.id,
                job_type=StoryBatchJobType.IMAGE.value,
                status=StoryBatchJobStatus.SUCCEEDED.value,
                expected_item_count=len(items),
                submitted_item_count=0,
                message="All generic story images already exist",
            )

        workflow.status = GenericStoryWorkflowStatus.IN_PROGRESS.value
        workflow.current_step = GenericStoryWorkflowStep.IMAGE_GENERATION.value
        workflow.error_message = None
        await self.workflows.update(workflow)

        job = await self._submit_image_batch_job_only(
            workflow,
            generic_story_id,
            missing,
            attempt=1,
            force=force,
            provider=provider,
        )
        await self.session.commit()
        return self._submit_response(
            workflow=workflow,
            job=job,
            submitted_count=len(missing),
            message="Generic story image batch submitted; reconcile scheduler will process results",
        )

    async def reconcile_batch_jobs(self, *, limit: int = 50) -> dict[str, Any]:
        jobs = await self.batch_jobs.list_reconcilable(limit=limit)
        results: list[dict[str, Any]] = []
        processed_count = 0

        self._log_event("reconcile_started", job_count=len(jobs), limit=limit)
        for job in jobs:
            try:
                self._log_event(
                    "reconcile_job_started",
                    batch_job_id=job.id,
                    generic_story_id=job.generic_story_id,
                    workflow_id=job.workflow_id,
                    status=job.status.value,
                    provider_state=job.provider_state,
                )
                result = await self._reconcile_batch_job(job)
                if result["action"] not in {"still_running", "skipped"}:
                    processed_count += 1
                results.append(result)
                self._log_event(
                    "reconcile_job_completed",
                    batch_job_id=job.id,
                    generic_story_id=job.generic_story_id,
                    workflow_id=job.workflow_id,
                    action=result.get("action"),
                    status=result.get("status"),
                    provider_state=result.get("provider_state"),
                    message=result.get("message"),
                )
            except Exception as exc:
                self._log_event(
                    "reconcile_job_failed",
                    batch_job_id=job.id,
                    generic_story_id=job.generic_story_id,
                    workflow_id=job.workflow_id,
                    status=job.status.value,
                    provider_state=job.provider_state,
                    error=str(exc),
                )
                results.append(
                    {
                        "generic_story_id": job.generic_story_id,
                        "workflow_id": job.workflow_id,
                        "batch_job_id": job.id,
                        "job_type": job.job_type.value,
                        "status": job.status.value,
                        "provider_state": job.provider_state,
                        "action": "error",
                        "message": str(exc),
                    }
                )

        self._log_event("reconcile_completed", checked_count=len(jobs), processed_count=processed_count)
        return {"checked_count": len(jobs), "processed_count": processed_count, "results": results}

    async def _reconcile_batch_job(self, job: GenericStoryBatchJob) -> dict[str, Any]:
        if not job.provider_job_name:
            return self._reconcile_result(job, "skipped", "Batch job has no provider job name")

        if self._provider_name(getattr(job, "provider", "google")) == "openai":
            return await self._reconcile_openai_batch_job(job)

        provider_job = await self.google_client.aio.batches.get(name=job.provider_job_name)
        state_name = self._job_state_name(provider_job)
        job.provider_state = state_name

        if state_name in self.SUCCEEDED_STATES:
            if self._job_request_mode(job) == self.WORKFLOW_MULTI_IMAGE_MODE:
                await self._process_reconciled_workflow_multi_image_job(job, provider_job)
                return self._reconcile_result(job, "processed", "Generic story workflow multi-image job processed")
            await self._process_reconciled_image_job(job, provider_job)
            return self._reconcile_result(job, "processed", "Generic story image job processed")

        if state_name in self.CANCELLED_STATES:
            job.status = StoryBatchJobStatus.CANCELLED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(
                job.workflow_id,
                job.error_message,
                current_step=(
                    GenericStoryWorkflowStep.IMAGE_GENERATION.value
                    if self._job_request_mode(job) == self.WORKFLOW_MULTI_IMAGE_MODE
                    else None
                ),
            )
            await self.session.commit()
            return self._reconcile_result(job, "cancelled", job.error_message)

        if state_name in self.FAILED_STATES:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(
                job.workflow_id,
                job.error_message,
                current_step=(
                    GenericStoryWorkflowStep.IMAGE_GENERATION.value
                    if self._job_request_mode(job) == self.WORKFLOW_MULTI_IMAGE_MODE
                    else None
                ),
            )
            await self.session.commit()
            return self._reconcile_result(job, "failed", job.error_message)

        job.status = StoryBatchJobStatus.RUNNING
        await self.batch_jobs.update(job)
        await self.session.commit()
        return self._reconcile_result(job, "still_running", f"Provider state is {state_name}")

    async def _submit_image_batch_job_only(
        self,
        workflow: GenericStoryWorkflow,
        generic_story_id: UUID,
        items: list[GenericBatchImageItem],
        *,
        attempt: int,
        force: bool = False,
        provider: str = "google",
    ) -> GenericStoryBatchJob:
        provider = self._provider_name(provider)
        if provider == "openai":
            return await self._submit_openai_image_batch_job_only(
                workflow,
                generic_story_id,
                items,
                attempt=attempt,
                force=force,
            )

        requests = [self._build_image_inlined_request(item) for item in items]
        model = settings.GOOGLE_REFERENCE_IMAGE_MODEL.removeprefix("models/")
        job = await self.batch_jobs.create(
            generic_story_id=generic_story_id,
            workflow_id=workflow.id,
            job_type=StoryBatchJobType.IMAGE,
            attempt=attempt,
            expected_item_count=len(items),
            request_keys=[item.key for item in items],
            provider_model=model,
            provider="google",
            request_payload={
                "mode": "generic_story_image",
                "provider": "google",
                "attempt": attempt,
                "force": force,
                "items": [self._image_item_payload(item) for item in items],
            },
        )
        await self.session.flush()

        try:
            provider_job = await self.google_client.aio.batches.create(
                model=model,
                src=requests,
                config={"display_name": f"generic-story-{generic_story_id}-images-attempt-{attempt}"},
            )
            job.provider_job_name = provider_job.name
            job.provider_state = self._job_state_name(provider_job)
            await self.batch_jobs.update(job)
            self._log_event(
                "image_batch_submitted",
                provider="google",
                batch_job_id=job.id,
                generic_story_id=generic_story_id,
                workflow_id=workflow.id,
                provider_job_name=job.provider_job_name,
                provider_state=job.provider_state,
                item_count=len(items),
            )
            return job
        except Exception as exc:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            job.missing_keys = [item.key for item in items]
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(workflow.id, str(exc))
            await self.session.commit()
            raise

    async def _submit_openai_image_batch_job_only(
        self,
        workflow: GenericStoryWorkflow,
        generic_story_id: UUID,
        items: list[GenericBatchImageItem],
        *,
        attempt: int,
        force: bool = False,
    ) -> GenericStoryBatchJob:
        model = settings.OPENAI_IMAGE_MODEL
        batch_requests = [self._build_openai_image_batch_request(item, model=model) for item in items]
        jsonl_bytes = self._jsonl_bytes(batch_requests)
        job = await self.batch_jobs.create(
            generic_story_id=generic_story_id,
            workflow_id=workflow.id,
            job_type=StoryBatchJobType.IMAGE,
            attempt=attempt,
            expected_item_count=len(items),
            request_keys=[item.key for item in items],
            provider_model=model,
            provider="openai",
            request_payload={
                "mode": "generic_story_image",
                "provider": "openai",
                "endpoint": self.OPENAI_IMAGE_BATCH_ENDPOINT,
                "attempt": attempt,
                "force": force,
                "items": [self._image_item_payload(item) for item in items],
            },
        )
        await self.session.flush()

        try:
            input_file = await self.openai_client.files.create(
                file=(f"generic-story-{generic_story_id}-images-attempt-{attempt}.jsonl", jsonl_bytes),
                purpose="batch",
            )
            provider_job = await self.openai_client.post(
                "/batches",
                cast_to=Batch,
                body={
                    "input_file_id": input_file.id,
                    "endpoint": self.OPENAI_IMAGE_BATCH_ENDPOINT,
                    "completion_window": "24h",
                    "metadata": {
                        "mode": "generic_story_image",
                        "generic_story_id": str(generic_story_id),
                        "workflow_id": str(workflow.id),
                        "attempt": str(attempt),
                    },
                },
            )
            job.provider_job_name = str(self._object_value(provider_job, "id") or "")
            job.provider_state = self._openai_job_state_name(provider_job)
            request_payload = dict(job.request_payload or {})
            request_payload["input_file_id"] = input_file.id
            job.request_payload = request_payload
            await self.batch_jobs.update(job)
            self._log_event(
                "image_batch_submitted",
                provider="openai",
                batch_job_id=job.id,
                generic_story_id=generic_story_id,
                workflow_id=workflow.id,
                provider_job_name=job.provider_job_name,
                provider_state=job.provider_state,
                item_count=len(items),
                input_file_id=input_file.id,
            )
            return job
        except Exception as exc:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            job.missing_keys = [item.key for item in items]
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(workflow.id, str(exc))
            await self.session.commit()
            raise

    async def _reconcile_openai_batch_job(self, job: GenericStoryBatchJob) -> dict[str, Any]:
        provider_job = await self.openai_client.batches.retrieve(job.provider_job_name)
        state_name = self._openai_job_state_name(provider_job)
        job.provider_state = state_name

        if state_name in self.OPENAI_SUCCEEDED_STATES:
            await self._process_reconciled_openai_image_job(job, provider_job)
            return self._reconcile_result(job, "processed", "Generic story OpenAI image job processed")

        if state_name in self.OPENAI_CANCELLED_STATES:
            job.status = StoryBatchJobStatus.CANCELLED
            job.error_message = self._openai_batch_error_message(provider_job) or f"OpenAI batch state {state_name}"
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(job.workflow_id, job.error_message)
            await self.session.commit()
            return self._reconcile_result(job, "cancelled", job.error_message)

        if state_name in self.OPENAI_FAILED_STATES:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = self._openai_batch_error_message(provider_job) or f"OpenAI batch state {state_name}"
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(job.workflow_id, job.error_message)
            await self.session.commit()
            return self._reconcile_result(job, "failed", job.error_message)

        job.status = StoryBatchJobStatus.RUNNING
        await self.batch_jobs.update(job)
        await self.session.commit()
        return self._reconcile_result(job, "still_running", f"Provider state is {state_name}")

    async def _process_reconciled_openai_image_job(
        self,
        job: GenericStoryBatchJob,
        provider_job: Any,
    ) -> None:
        generic_story = await self.generic_stories.get_by_id(job.generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        workflow = await self.workflows.get_by_id(job.workflow_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")

        story_json = self._story_json_for_image_plan(workflow, generic_story)
        items = self._build_image_items(workflow, story_json)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]

        completed_keys, failed_keys, response_summary = await self._process_openai_image_batch_responses(
            workflow,
            story_json,
            items,
            provider_job,
            storage_story_id=job.generic_story_id,
        )

        job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
        job.completed_item_count = len(completed_keys)
        job.failed_item_count = len(failed_keys)
        job.missing_keys = sorted({item.key for item in items} - completed_keys)
        job.response_payload = response_summary
        job.error_message = f"Missing image keys: {', '.join(sorted(failed_keys))}" if failed_keys else None
        await self.batch_jobs.update(job)

        if failed_keys:
            workflow.status = GenericStoryWorkflowStatus.FAILED.value
            workflow.current_step = None
            workflow.error_message = job.error_message
        else:
            workflow.status = GenericStoryWorkflowStatus.COMPLETED.value
            workflow.current_step = None
            workflow.error_message = None
            await self._apply_image_urls_to_contents(
                generic_story,
                story_json,
                workflow=workflow,
            )
        await self.workflows.update(workflow)
        await self.session.commit()

    async def _process_reconciled_image_job(
        self,
        job: GenericStoryBatchJob,
        provider_job: types.BatchJob,
    ) -> None:
        generic_story = await self.generic_stories.get_by_id(job.generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
        workflow = await self.workflows.get_by_id(job.workflow_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")

        story_json = self._story_json_for_image_plan(workflow, generic_story)
        items = self._build_image_items(workflow, story_json)
        request_keys = set(job.request_keys or [])
        if request_keys:
            items = [item for item in items if item.key in request_keys]

        completed_keys, failed_keys, response_summary = await self._process_image_batch_responses(
            workflow,
            story_json,
            items,
            provider_job,
            storage_story_id=job.generic_story_id,
        )

        job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
        job.completed_item_count = len(completed_keys)
        job.failed_item_count = len(failed_keys)
        job.missing_keys = sorted({item.key for item in items} - completed_keys)
        job.response_payload = response_summary
        job.error_message = f"Missing image keys: {', '.join(sorted(failed_keys))}" if failed_keys else None
        await self.batch_jobs.update(job)

        if failed_keys:
            workflow.status = GenericStoryWorkflowStatus.FAILED.value
            workflow.current_step = None
            workflow.error_message = job.error_message
        else:
            workflow.status = GenericStoryWorkflowStatus.COMPLETED.value
            workflow.current_step = None
            workflow.error_message = None
            await self._apply_image_urls_to_contents(
                generic_story,
                story_json,
                workflow=workflow,
            )
        await self.workflows.update(workflow)
        await self.session.commit()

    async def _process_reconciled_workflow_multi_image_job(
        self,
        job: GenericStoryBatchJob,
        provider_job: types.BatchJob,
    ) -> None:
        workflow = await self.workflows.get_by_id(job.workflow_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")

        story_json = dict(workflow.story_json or {})
        request_payload = job.request_payload if isinstance(job.request_payload, dict) else {}
        items = self._workflow_multi_image_items_from_payload(job, request_payload)

        completed_keys, failed_keys, response_summary = await self._process_workflow_multi_image_response(
            workflow,
            story_json,
            items,
            provider_job,
            storage_story_id=job.generic_story_id or workflow.id,
        )

        job.status = StoryBatchJobStatus.SUCCEEDED if not failed_keys else StoryBatchJobStatus.FAILED
        job.completed_item_count = len(completed_keys)
        job.failed_item_count = len(failed_keys)
        job.missing_keys = sorted({str(item["key"]) for item in items} - completed_keys)
        job.response_payload = response_summary
        job.error_message = self._workflow_multi_image_error_message(response_summary, failed_keys)
        workflow.story_json = story_json

        if failed_keys:
            workflow.status = GenericStoryWorkflowStatus.FAILED.value
            workflow.current_step = GenericStoryWorkflowStep.IMAGE_GENERATION.value
            workflow.error_message = job.error_message
            await self.batch_jobs.update(job)
            await self.workflows.update(workflow)
            await self.session.commit()
            return

        continue_after_image_generation = request_payload.get("continue_after_image_generation", True)
        if continue_after_image_generation:
            workflow.status = GenericStoryWorkflowStatus.IN_PROGRESS.value
            workflow.current_step = GenericStoryWorkflowStep.IMAGE_GENERATION.value
        elif workflow.generic_story_id is not None:
            generic_story = await self.generic_stories.get_by_id(workflow.generic_story_id)
            if generic_story is not None:
                await self._apply_image_urls_to_contents(generic_story, story_json, workflow=workflow)
            workflow.status = GenericStoryWorkflowStatus.COMPLETED.value
            workflow.current_step = None
        else:
            workflow.status = GenericStoryWorkflowStatus.IN_PROGRESS.value
            workflow.current_step = GenericStoryWorkflowStep.IMAGE_GENERATION.value
        workflow.error_message = None
        await self.batch_jobs.update(job)
        await self.workflows.update(workflow)
        await self.session.commit()

        if continue_after_image_generation:
            await self._continue_workflow_after_multi_image_generation(workflow, request_payload)

    def _workflow_multi_image_items_from_payload(
        self,
        job: GenericStoryBatchJob,
        request_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_items = request_payload.get("items")
        cover_item = request_payload.get("cover_item")
        if not isinstance(raw_items, list):
            raw_items = []
        has_cover_item = isinstance(cover_item, dict) and bool(str(cover_item.get("key") or "").strip())
        if not raw_items and not has_cover_item:
            raise AppException(
                "Generic workflow multi-image batch has no request items.",
                code="GENERIC_WORKFLOW_MULTI_IMAGE_ITEMS_MISSING",
            )
        items = []
        if has_cover_item:
            normalized_cover_item = dict(cover_item)
            normalized_cover_item.pop("rendered_prompt", None)
            items.append(normalized_cover_item)
        for item in raw_items:
            if not isinstance(item, dict) or not str(item.get("key") or "").strip():
                continue
            normalized_item = dict(item)
            normalized_item.pop("rendered_prompt", None)
            items.append(normalized_item)
        if len(items) != len(raw_items) + (1 if has_cover_item else 0):
            raise AppException(
                "Generic workflow multi-image batch request items are invalid.",
                code="GENERIC_WORKFLOW_MULTI_IMAGE_ITEMS_INVALID",
            )
        requested_keys = [str(key) for key in (job.request_keys or []) if str(key).strip()]
        if not requested_keys:
            return items

        items_by_key = {str(item["key"]): item for item in items}
        missing_payload_keys = [key for key in requested_keys if key not in items_by_key]
        if missing_payload_keys:
            raise AppException(
                "Generic workflow multi-image batch payload is missing requested page items.",
                code="GENERIC_WORKFLOW_MULTI_IMAGE_REQUEST_KEYS_MISMATCH",
                details={"missing_keys": missing_payload_keys},
            )
        return [items_by_key[key] for key in requested_keys]

    async def _process_workflow_multi_image_response(
        self,
        workflow: GenericStoryWorkflow,
        story_json: dict[str, Any],
        items: list[dict[str, Any]],
        provider_job: types.BatchJob,
        *,
        storage_story_id: UUID,
    ) -> tuple[set[str], set[str], dict[str, Any]]:
        responses = list((provider_job.dest.inlined_responses if provider_job.dest else None) or [])
        by_key = self._responses_by_key(responses)
        cover_items = [item for item in items if str(item.get("key") or "") == "cover" or item.get("page_type") == "cover"]
        page_items = [item for item in items if item not in cover_items]
        response_summary: dict[str, Any] = {"items": [], "response_count": len(responses)}
        completed_keys: set[str] = set()
        failed_keys: set[str] = set()

        for cover_item in cover_items:
            key = str(cover_item["key"])
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
                aspect_ratio = str(cover_item.get("aspect_ratio") or settings.STORY_COVER_ASPECT_RATIO)
                cropped = StoryService._crop_image_bytes_to_aspect_ratio(image_bytes, aspect_ratio)
                image_url = await self.image_storage.save_story_image(
                    storage_story_id,
                    cropped,
                    str(cover_item.get("filename") or "cover.png"),
                    "",
                )
                self._set_story_json_page_image_fields(story_json, cover_item, image_url)
                completed_keys.add(key)
                response_summary["items"].append(
                    {"key": key, "status": "completed", "image_url": image_url, "response_text": response_text}
                )
            except Exception as exc:
                failed_keys.add(key)
                response_summary["items"].append({"key": key, "status": "save_failed", "error": str(exc)})

        if not page_items:
            return completed_keys, failed_keys, response_summary

        inlined_response = by_key.get("pages_multi") or (responses[0] if not cover_items and responses else None)
        if inlined_response is None:
            failed_keys.update({str(item["key"]) for item in page_items})
            response_summary["status"] = "missing_response"
            return completed_keys, failed_keys, response_summary
        if inlined_response.error:
            failed_keys.update({str(item["key"]) for item in page_items})
            response_summary["status"] = "error"
            response_summary["error"] = self._model_dump_safe(inlined_response.error)
            return completed_keys, failed_keys, response_summary
        if inlined_response.response is None:
            failed_keys.update({str(item["key"]) for item in page_items})
            response_summary["status"] = "empty_response"
            return completed_keys, failed_keys, response_summary

        try:
            images, response_text = GoogleProvider._extract_images_from_content_response(inlined_response.response)
        except Exception as exc:
            failed_keys.update({str(item["key"]) for item in page_items})
            response_summary["status"] = "parse_failed"
            response_summary["error"] = str(exc)
            return completed_keys, failed_keys, response_summary

        response_summary["response_text"] = response_text
        response_summary["received_count"] = len(images)
        response_summary["expected_count"] = len(page_items)
        if len(images) != len(page_items):
            error = f"Gemini returned {len(images)} images; expected {len(page_items)}."
            response_summary["status"] = "count_mismatch"
            response_summary["error"] = error
            failed_keys.update({str(item["key"]) for item in page_items})
            return completed_keys, failed_keys, response_summary

        marker_errors: list[dict[str, str]] = []
        for item, image in zip(page_items, images, strict=True):
            key = str(item["key"])
            try:
                self._validate_multi_image_marker(key, image.preceding_text)
            except Exception as exc:
                marker_errors.append(
                    {
                        "key": key,
                        "marker_text": str(image.preceding_text or ""),
                        "error": str(exc),
                    }
                )
        if marker_errors:
            response_summary["status"] = "marker_mismatch"
            response_summary["error"] = "Gemini image markers did not match the requested page order."
            response_summary["items"] = marker_errors
            failed_keys.update({str(item["key"]) for item in page_items})
            return completed_keys, failed_keys, response_summary

        for item, image in zip(page_items, images, strict=True):
            key = str(item["key"])
            try:
                aspect_ratio = str(item.get("aspect_ratio") or settings.STORY_PAGE_ASPECT_RATIO)
                cropped = StoryService._crop_image_bytes_to_aspect_ratio(image.image_bytes, aspect_ratio)
                image_url = await self.image_storage.save_story_image(
                    storage_story_id,
                    cropped,
                    str(item.get("filename") or f"{key}.png"),
                    "",
                )
                self._set_story_json_page_image_fields(story_json, item, image_url)
                completed_keys.add(key)
                response_summary["items"].append(
                    {
                        "key": key,
                        "status": "completed",
                        "image_url": image_url,
                        "marker_text": image.preceding_text,
                    }
                )
            except Exception as exc:
                failed_keys.add(key)
                response_summary["items"].append({"key": key, "status": "save_failed", "error": str(exc)})
        return completed_keys, failed_keys, response_summary

    @staticmethod
    def _validate_multi_image_marker(expected_key: str, marker_text: str | None) -> None:
        if not marker_text or "IMAGE_ITEM" not in marker_text.upper():
            return
        expected_marker = f"IMAGE_ITEM: {expected_key}".lower()
        if expected_marker not in marker_text.lower():
            raise AppException(
                f"Gemini image marker mismatch; expected {expected_key}.",
                code="GENERIC_WORKFLOW_MULTI_IMAGE_MARKER_MISMATCH",
                details={"expected_key": expected_key, "marker_text": marker_text},
            )

    @staticmethod
    def _set_story_json_page_image_fields(
        story_json: dict[str, Any],
        item: dict[str, Any],
        image_url: str,
    ) -> None:
        source_image_prompt = GenericStoryBatchService._workflow_item_source_image_prompt(item)
        if str(item.get("key") or "") == "cover" or item.get("page_type") == "cover":
            story_json["cover_image_url"] = image_url
            story_json["cover_image_prompt"] = source_image_prompt
            story_json["cover_planned_image_prompt"] = source_image_prompt
            story_json.pop("cover_image_dummy", None)
            return

        page_number = int(item.get("page_number") or 0)
        for index, page in enumerate(story_json.get("pages") or [], start=1):
            if not isinstance(page, dict):
                continue
            if int(page.get("page_number") or index) != page_number:
                continue
            page["image_url"] = image_url
            page["image_prompt"] = source_image_prompt
            page["planned_image_prompt"] = source_image_prompt
            page.pop("image_dummy", None)
            return

        raise AppException(
            f"Workflow story JSON is missing page {page_number} for multi-image result.",
            code="GENERIC_WORKFLOW_MULTI_IMAGE_STORY_PAGE_MISSING",
            details={"page_number": page_number, "item_key": item.get("key")},
        )

    @staticmethod
    def _workflow_item_source_image_prompt(item: dict[str, Any]) -> str | None:
        source_image_prompt = item.get("source_image_prompt")
        if isinstance(source_image_prompt, str) and source_image_prompt.strip():
            return source_image_prompt.strip()
        page_image_plan = item.get("page_image_plan")
        if isinstance(page_image_plan, dict) and page_image_plan:
            return json.dumps(page_image_plan, ensure_ascii=False, separators=(",", ":"))
        return None

    @staticmethod
    def _workflow_multi_image_error_message(
        response_summary: dict[str, Any],
        failed_keys: set[str],
    ) -> str | None:
        if not failed_keys:
            return None
        error = response_summary.get("error")
        if error:
            return str(error)
        return f"Missing image keys: {', '.join(sorted(failed_keys))}"

    async def _continue_workflow_after_multi_image_generation(
        self,
        workflow: GenericStoryWorkflow,
        request_payload: dict[str, Any],
    ) -> None:
        service = GenericStoryWorkflowService(self.session)
        payload = GenericStoryWorkflowExecuteRequest(
            step_name="ALL",
            skip_image_generation=False,
            skip_narration_generation=bool(request_payload.get("skip_narration_generation", True)),
            publish_status=request_payload.get("publish_status"),
        )
        await service._execute_steps(
            workflow,
            [
                GenericStoryWorkflowStep.NARRATION_GENERATION,
                GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
            ],
            payload=payload,
            public_base_url=str(request_payload.get("public_base_url") or ""),
            event_name="workflow_multi_image_batch_continuation_started",
            requested_step=GenericStoryWorkflowStep.NARRATION_GENERATION.value,
        )

    async def _process_image_batch_responses(
        self,
        workflow: GenericStoryWorkflow,
        story_json: dict[str, Any],
        items: list[GenericBatchImageItem],
        provider_job: types.BatchJob,
        *,
        storage_story_id: UUID,
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
                image_url = await self.image_storage.save_story_image(storage_story_id, cropped, item.file_name, "")
                self._set_story_json_image_url(story_json, item, image_url)
                completed_keys.add(item.key)
                response_summary["items"].append(
                    {"key": item.key, "status": "completed", "image_url": image_url, "response_text": response_text}
                )
            except Exception as exc:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "save_failed", "error": str(exc)})
        return completed_keys, failed_keys, response_summary

    async def _process_openai_image_batch_responses(
        self,
        workflow: GenericStoryWorkflow,
        story_json: dict[str, Any],
        items: list[GenericBatchImageItem],
        provider_job: Any,
        *,
        storage_story_id: UUID,
    ) -> tuple[set[str], set[str], dict[str, Any]]:
        _ = workflow
        output_file_id = self._object_value(provider_job, "output_file_id")
        error_file_id = self._object_value(provider_job, "error_file_id")
        response_summary: dict[str, Any] = {"items": [], "output_file_id": output_file_id, "error_file_id": error_file_id}
        by_key: dict[str, dict[str, Any]] = {}
        if output_file_id:
            by_key.update(await self._openai_batch_outputs_by_key(str(output_file_id)))
        if error_file_id:
            by_key.update(await self._openai_batch_outputs_by_key(str(error_file_id)))
        if not by_key:
            return set(), {item.key for item in items}, response_summary

        completed_keys: set[str] = set()
        failed_keys: set[str] = set()
        for item in items:
            output = by_key.get(item.key)
            if output is None:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "missing_response"})
                continue
            error = output.get("error")
            if error:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "error", "error": error})
                continue

            response = output.get("response") if isinstance(output.get("response"), dict) else {}
            status_code = int(response.get("status_code") or 0)
            if status_code < 200 or status_code >= 300:
                failed_keys.add(item.key)
                response_summary["items"].append(
                    {
                        "key": item.key,
                        "status": "http_error",
                        "status_code": status_code,
                        "error": self._openai_response_error_summary(response.get("body")),
                    }
                )
                continue

            try:
                image_bytes, revised_prompt = self._extract_openai_image_bytes(response.get("body"))
                cropped = StoryService._crop_image_bytes_to_aspect_ratio(image_bytes, item.aspect_ratio)
                image_url = await self.image_storage.save_story_image(storage_story_id, cropped, item.file_name, "")
                self._set_story_json_image_url(story_json, item, image_url)
                completed_keys.add(item.key)
                response_summary["items"].append(
                    {
                        "key": item.key,
                        "status": "completed",
                        "image_url": image_url,
                        "revised_prompt": revised_prompt,
                    }
                )
            except Exception as exc:
                failed_keys.add(item.key)
                response_summary["items"].append({"key": item.key, "status": "save_failed", "error": str(exc)})
        return completed_keys, failed_keys, response_summary

    async def _apply_image_urls_to_contents(
        self,
        generic_story,
        source_story_json: dict[str, Any],
        *,
        workflow: GenericStoryWorkflow,
    ) -> None:
        cover_fields = {
            "cover_image_url": source_story_json.get("cover_image_url"),
            "cover_image_prompt": source_story_json.get("cover_image_prompt"),
            "cover_planned_image_prompt": source_story_json.get("cover_planned_image_prompt"),
        }
        cover_image_url = str(cover_fields["cover_image_url"] or "")
        page_image_fields = {
            int(page.get("page_number") or index + 1): {
                "image_url": page.get("image_url"),
                "image_prompt": page.get("image_prompt"),
                "planned_image_prompt": page.get("planned_image_prompt"),
            }
            for index, page in enumerate(source_story_json.get("pages") or [])
            if isinstance(page, dict) and page.get("image_url")
        }
        if cover_image_url:
            generic_story.cover_image = cover_image_url
            workflow.cover_image = cover_image_url

        for content in generic_story.contents:
            content_story_json = content.story_json if isinstance(content.story_json, dict) else {}
            self._apply_story_image_fields(
                content_story_json,
                cover_fields=cover_fields,
                page_image_fields=page_image_fields,
            )
            content.story_json = content_story_json
            await self.generic_stories.update_content(content)

        self._apply_workflow_story_json_images_all_languages(
            workflow,
            cover_fields=cover_fields,
            page_image_fields=page_image_fields,
        )

    def _story_json_for_image_plan(
        self,
        workflow: GenericStoryWorkflow,
        generic_story,
    ) -> dict[str, Any]:
        if isinstance(workflow.story_json, dict) and workflow.story_json.get("pages"):
            return dict(workflow.story_json)

        workflow_language = GenericStoryWorkflowService._default_story_language(workflow.language)
        content = next(
            (
                item
                for item in generic_story.contents
                if str(item.language).strip().lower() == workflow_language
            ),
            None,
        )
        if content is None:
            content = next(iter(generic_story.contents or []), None)
        if content is None or not isinstance(content.story_json, dict):
            raise AppException("Generic story content has no story JSON", code="GENERIC_STORY_CONTENT_MISSING")
        return dict(content.story_json)

    def _build_image_items(
        self,
        workflow: GenericStoryWorkflow,
        story_json: dict[str, Any],
    ) -> list[GenericBatchImageItem]:
        image_plan = workflow.image_plan_json or {}
        visual_bible = GenericStoryWorkflowService._workflow_visual_bible(workflow)
        story_title = story_json.get("title") or workflow.title or "Untitled Story"
        items: list[GenericBatchImageItem] = []

        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        if cover_plan:
            items.append(
                GenericBatchImageItem(
                    key="cover",
                    page_type="cover",
                    page_number=0,
                    page_data=cover_plan,
                    source_image_prompt=GenericStoryWorkflowService._image_plan_summary(cover_plan),
                    rendered_prompt=self._render_image_prompt(
                        page_type="cover",
                        story_title=story_title,
                        visual_bible=visual_bible,
                        page_image_plan=cover_plan,
                    ),
                    aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                    file_name="cover.png",
                )
            )

        for page_plan in image_plan.get("pages") or []:
            if not isinstance(page_plan, dict):
                continue
            page_number = GenericStoryWorkflowService._image_plan_page_number(page_plan)
            if page_number is None:
                continue
            items.append(
                GenericBatchImageItem(
                    key=f"page_{page_number}",
                    page_type="page",
                    page_number=page_number,
                    page_data=page_plan,
                    source_image_prompt=GenericStoryWorkflowService._image_plan_summary(page_plan),
                    rendered_prompt=self._render_image_prompt(
                        page_type="story_page",
                        story_title=story_title,
                        visual_bible=visual_bible,
                        page_image_plan=page_plan,
                    ),
                    aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
                    file_name=f"page_{page_number}.png",
                )
            )
        return items

    async def _missing_image_items(
        self,
        story_json: dict[str, Any],
        items: list[GenericBatchImageItem],
    ) -> list[GenericBatchImageItem]:
        missing: list[GenericBatchImageItem] = []
        for item in items:
            image_url = self._story_json_image_url(story_json, item)
            if image_url and await self._image_url_exists(image_url):
                continue
            missing.append(item)
        return missing

    async def _image_url_exists(self, image_url: str) -> bool:
        try:
            return bool(await self.image_storage.get_image_bytes(image_url))
        except Exception:
            logger.warning("Generic story batch image URL is not readable and will be regenerated: %s", image_url)
            return False

    def _build_image_inlined_request(self, item: GenericBatchImageItem) -> types.InlinedRequest:
        return types.InlinedRequest(
            contents=[types.Content(role="user", parts=[types.Part(text=item.rendered_prompt)])],
            metadata={"key": item.key},
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(aspect_ratio=item.aspect_ratio),
            ),
        )

    def _build_openai_image_batch_request(self, item: GenericBatchImageItem, *, model: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": settings.OPENAI_TEXT_MODEL,
            "input": item.rendered_prompt,
            "tools": [
                {
                    "type": "image_generation",
                    "model": model,
                    "size": self._openai_image_size(item),
                    "quality": self._openai_image_quality(settings.STORY_IMAGE_QUALITY),
                    "output_format": "png",
                }
            ],
        }
        return {
            "custom_id": item.key,
            "method": "POST",
            "url": self.OPENAI_IMAGE_BATCH_ENDPOINT,
            "body": body,
        }

    @staticmethod
    def _render_image_prompt(
        *,
        page_type: str,
        story_title: str,
        visual_bible: dict[str, Any],
        page_image_plan: dict[str, Any],
    ) -> str:
        return GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)._render_image_prompt(
            page_type=page_type,
            story_title=story_title,
            visual_bible=visual_bible,
            page_image_plan=page_image_plan,
        )

    @staticmethod
    def _set_story_json_image_url(story_json: dict[str, Any], item: GenericBatchImageItem, image_url: str) -> None:
        if item.page_type == "cover":
            story_json["cover_image_url"] = image_url
            story_json["cover_image_prompt"] = item.rendered_prompt
            story_json["cover_planned_image_prompt"] = item.source_image_prompt
            story_json.pop("cover_image_dummy", None)
            return
        for index, page in enumerate(story_json.get("pages") or [], start=1):
            if not isinstance(page, dict):
                continue
            page_number = int(page.get("page_number") or index)
            if page_number != item.page_number:
                continue
            page["image_url"] = image_url
            page["image_prompt"] = item.rendered_prompt
            page["planned_image_prompt"] = item.source_image_prompt
            page.pop("image_dummy", None)
            return

    @staticmethod
    def _story_json_image_url(story_json: dict[str, Any], item: GenericBatchImageItem) -> str | None:
        if item.page_type == "cover":
            return story_json.get("cover_image_url")
        for index, page in enumerate(story_json.get("pages") or [], start=1):
            if isinstance(page, dict) and int(page.get("page_number") or index) == item.page_number:
                return page.get("image_url")
        return None

    def _apply_workflow_story_json_images_all_languages(
        self,
        workflow: GenericStoryWorkflow,
        *,
        cover_fields: dict[str, Any],
        page_image_fields: dict[int, dict[str, Any]],
    ) -> None:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        self._apply_story_image_fields(
            story_json,
            cover_fields=cover_fields,
            page_image_fields=page_image_fields,
        )
        variants = story_json.get("language_variants")
        if isinstance(variants, dict):
            for variant_story_json in variants.values():
                if not isinstance(variant_story_json, dict):
                    continue
                self._apply_story_image_fields(
                    variant_story_json,
                    cover_fields=cover_fields,
                    page_image_fields=page_image_fields,
                )
        workflow.story_json = story_json

    @staticmethod
    def _apply_story_image_fields(
        story_json: dict[str, Any],
        *,
        cover_fields: dict[str, Any],
        page_image_fields: dict[int, dict[str, Any]],
    ) -> None:
        for field_name, value in cover_fields.items():
            if value:
                story_json[field_name] = value
        story_json.pop("cover_image_dummy", None)

        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            page_number = int(page.get("page_number") or index)
            fields = page_image_fields.get(page_number)
            if not fields:
                continue
            for field_name, value in fields.items():
                if value:
                    page[field_name] = value
            page.pop("image_dummy", None)

    async def _mark_workflow_failed(
        self,
        workflow_id: UUID,
        error_message: str,
        *,
        current_step: str | None = None,
    ) -> None:
        workflow = await self.workflows.get_by_id(workflow_id)
        if workflow is None:
            return
        workflow.status = GenericStoryWorkflowStatus.FAILED.value
        workflow.current_step = current_step
        workflow.error_message = error_message
        await self.workflows.update(workflow)

    @staticmethod
    def _image_item_payload(item: GenericBatchImageItem) -> dict[str, Any]:
        return {
            "key": item.key,
            "page_type": item.page_type,
            "page_number": item.page_number,
            "source_image_prompt": item.source_image_prompt,
            "rendered_prompt": item.rendered_prompt,
            "aspect_ratio": item.aspect_ratio,
            "file_name": item.file_name,
        }

    @staticmethod
    def _jsonl_bytes(lines: list[dict[str, Any]]) -> bytes:
        return "\n".join(json.dumps(line, ensure_ascii=False) for line in lines).encode("utf-8")

    @staticmethod
    def _provider_name(provider: str | None) -> str:
        value = str(provider or "google").strip().lower()
        if value not in {"google", "openai"}:
            raise AppException(
                "Unsupported generic story image batch provider",
                code="UNSUPPORTED_BATCH_PROVIDER",
                details={"provider": provider, "supported_providers": ["google", "openai"]},
            )
        return value

    @staticmethod
    def _openai_image_size(item: GenericBatchImageItem) -> str:
        if item.page_type == "cover":
            return settings.STORY_COVER_IMAGE_SIZE
        return settings.STORY_PAGE_IMAGE_SIZE

    @staticmethod
    def _openai_image_quality(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"low", "medium", "high", "auto"}:
            return normalized
        if normalized == "standard":
            return "medium"
        if normalized == "hd":
            return "high"
        return "auto"

    async def _openai_batch_outputs_by_key(self, output_file_id: str) -> dict[str, dict[str, Any]]:
        content = await self.openai_client.files.content(output_file_id)
        text = content.text
        by_key: dict[str, dict[str, Any]] = {}
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppException(
                    f"OpenAI batch output JSONL is invalid at line {line_number}",
                    code="OPENAI_BATCH_OUTPUT_INVALID",
                ) from exc
            custom_id = item.get("custom_id")
            if custom_id:
                by_key[str(custom_id)] = item
        return by_key

    @staticmethod
    def _openai_response_error_summary(body: Any) -> Any:
        if not isinstance(body, dict):
            return body
        error = body.get("error")
        if not isinstance(error, dict):
            return body
        return {
            "message": error.get("message"),
            "type": error.get("type"),
            "param": error.get("param"),
            "code": error.get("code"),
        }

    @staticmethod
    def _extract_openai_image_bytes(body: Any) -> tuple[bytes, str | None]:
        if not isinstance(body, dict):
            raise AppException("OpenAI image batch response body is invalid", code="OPENAI_IMAGE_RESPONSE_INVALID")
        output = body.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "image_generation_call":
                    continue
                b64_result = item.get("result")
                if not b64_result:
                    continue
                try:
                    return base64.b64decode(str(b64_result)), item.get("revised_prompt")
                except ValueError as exc:
                    raise AppException(
                        "OpenAI image batch b64 result is invalid",
                        code="OPENAI_IMAGE_RESPONSE_INVALID",
                    ) from exc
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise AppException("OpenAI image batch response returned no image data", code="OPENAI_IMAGE_RESPONSE_EMPTY")
        first = data[0]
        if not isinstance(first, dict):
            raise AppException("OpenAI image batch image object is invalid", code="OPENAI_IMAGE_RESPONSE_INVALID")
        b64_json = first.get("b64_json")
        if not b64_json:
            raise AppException("OpenAI image batch response has no b64_json", code="OPENAI_IMAGE_RESPONSE_EMPTY")
        try:
            return base64.b64decode(str(b64_json)), first.get("revised_prompt")
        except ValueError as exc:
            raise AppException("OpenAI image batch b64_json is invalid", code="OPENAI_IMAGE_RESPONSE_INVALID") from exc

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
    def _job_request_mode(job: GenericStoryBatchJob) -> str:
        request_payload = job.request_payload if isinstance(job.request_payload, dict) else {}
        return str(request_payload.get("mode") or "")

    @staticmethod
    def _job_state_name(job: types.BatchJob) -> str:
        state = getattr(job, "state", None)
        return getattr(state, "name", None) or str(state or "")

    @classmethod
    def _openai_job_state_name(cls, job: Any) -> str:
        return str(cls._object_value(job, "status") or "").lower()

    @staticmethod
    def _openai_batch_error_message(job: Any) -> str | None:
        errors = GenericStoryBatchService._object_value(job, "errors")
        if errors is None:
            return None
        data = GenericStoryBatchService._object_value(errors, "data")
        if isinstance(data, list) and data:
            first = data[0]
            message = GenericStoryBatchService._object_value(first, "message")
            if message:
                return str(message)
        return str(errors)

    @staticmethod
    def _object_value(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    @staticmethod
    def _model_dump_safe(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "dict"):
            return value.dict()
        return str(value)

    @staticmethod
    def _detect_image_mime_type(image_bytes: bytes) -> str:
        with Image.open(BytesIO(image_bytes)) as image:
            return Image.MIME.get(image.format or "", "image/png")

    @staticmethod
    def _reconcile_result(job: GenericStoryBatchJob, action: str, message: str | None = None) -> dict[str, Any]:
        return {
            "generic_story_id": job.generic_story_id,
            "workflow_id": job.workflow_id,
            "batch_job_id": job.id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "provider_state": job.provider_state,
            "action": action,
            "message": message,
        }

    @staticmethod
    def _batch_cancel_response(
        workflow: GenericStoryWorkflow,
        job: GenericStoryBatchJob,
        message: str,
    ) -> dict[str, Any]:
        return {
            "generic_story_id": job.generic_story_id,
            "workflow_id": workflow.id,
            "batch_job_id": job.id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "provider_job_name": job.provider_job_name,
            "provider_state": job.provider_state,
            "workflow_status": workflow.status,
            "message": message,
        }

    @staticmethod
    def _submit_response(
        *,
        workflow: GenericStoryWorkflow,
        job: GenericStoryBatchJob,
        submitted_count: int,
        message: str,
    ) -> GenericStoryBatchImageSubmitResponse:
        return GenericStoryBatchImageSubmitResponse(
            generic_story_id=job.generic_story_id,
            workflow_id=workflow.id,
            batch_job_id=job.id,
            job_type=job.job_type.value,
            status=job.status.value,
            provider_job_name=job.provider_job_name,
            provider_state=job.provider_state,
            expected_item_count=job.expected_item_count,
            submitted_item_count=submitted_count,
            message=message,
        )
