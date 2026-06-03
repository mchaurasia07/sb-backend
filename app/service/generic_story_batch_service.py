"""Generic story image batch submission and reconciliation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID

from google import genai
from google.genai import types
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.generic_story_batch_job import GenericStoryBatchJob
from app.entity.generic_story_workflow import GenericStoryWorkflow, GenericStoryWorkflowStatus, GenericStoryWorkflowStep
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.model.response.generic_story import GenericStoryBatchImageSubmitResponse
from app.repository.generic_story_batch_job_repository import GenericStoryBatchJobRepository
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.generic_story_workflow_repository import GenericStoryWorkflowRepository
from app.service.ai.google_provider import GoogleProvider
from app.service.generic_story_workflow_service import GenericStoryWorkflowService, _compact_json
from app.service.image_storage_provider import get_image_storage_service
from app.service.story_service import StoryService
from app.utils.prompt_loader import load_and_render_prompt

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

    def __init__(self, session: AsyncSession):
        self.session = session
        self.generic_stories = GenericStoryRepository(session)
        self.workflows = GenericStoryWorkflowRepository(session)
        self.batch_jobs = GenericStoryBatchJobRepository(session)
        self.image_storage = get_image_storage_service()
        self.google_client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    async def submit_image_batch(
        self,
        *,
        user_id: UUID,
        generic_story_id: UUID,
        force: bool = False,
        page_numbers: set[int] | None = None,
    ) -> GenericStoryBatchImageSubmitResponse:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        workflow = await self.workflows.latest_for_user_generic_story(user_id, generic_story_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")
        if not isinstance(workflow.image_plan_json, dict):
            raise AppException("Generic story workflow has no image plan", code="GENERIC_IMAGE_PLAN_MISSING")

        latest = await self.batch_jobs.latest_for_story_type(generic_story_id, StoryBatchJobType.IMAGE)
        if latest and latest.status in {StoryBatchJobStatus.SUBMITTED, StoryBatchJobStatus.RUNNING}:
            return self._submit_response(
                workflow=workflow,
                job=latest,
                submitted_count=len(latest.request_keys or []),
                message=f"Image batch already exists with status {latest.status.value}",
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

        for job in jobs:
            try:
                result = await self._reconcile_batch_job(job)
                if result["action"] not in {"still_running", "skipped"}:
                    processed_count += 1
                results.append(result)
            except Exception as exc:
                logger.exception(
                    "generic_story_batch_reconcile_job_failed batch_job_id=%s error=%s",
                    job.id,
                    exc,
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

        return {"checked_count": len(jobs), "processed_count": processed_count, "results": results}

    async def _reconcile_batch_job(self, job: GenericStoryBatchJob) -> dict[str, Any]:
        if not job.provider_job_name:
            return self._reconcile_result(job, "skipped", "Batch job has no provider job name")

        provider_job = await self.google_client.aio.batches.get(name=job.provider_job_name)
        state_name = self._job_state_name(provider_job)
        job.provider_state = state_name

        if state_name in self.SUCCEEDED_STATES:
            await self._process_reconciled_image_job(job, provider_job)
            return self._reconcile_result(job, "processed", "Generic story image job processed")

        if state_name in self.CANCELLED_STATES:
            job.status = StoryBatchJobStatus.CANCELLED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(job.workflow_id, job.error_message)
            await self.session.commit()
            return self._reconcile_result(job, "cancelled", job.error_message)

        if state_name in self.FAILED_STATES:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(getattr(provider_job, "error", None) or f"Google batch state {state_name}")
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(job.workflow_id, job.error_message)
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
    ) -> GenericStoryBatchJob:
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
            request_payload={
                "mode": "generic_story_image",
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
            return job
        except Exception as exc:
            job.status = StoryBatchJobStatus.FAILED
            job.error_message = str(exc)
            job.missing_keys = [item.key for item in items]
            await self.batch_jobs.update(job)
            await self._mark_workflow_failed(workflow.id, str(exc))
            await self.session.commit()
            raise

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
        visual_bible = image_plan.get("visual_bible") or (workflow.scene_plan_json or {}).get("visual_bible") or {}
        story_title = story_json.get("title") or workflow.title or "Untitled Story"
        items: list[GenericBatchImageItem] = []

        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        if cover_plan.get("image_prompt"):
            items.append(
                GenericBatchImageItem(
                    key="cover",
                    page_type="cover",
                    page_number=0,
                    page_data=cover_plan,
                    source_image_prompt=cover_plan["image_prompt"],
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
            page_number = int(page_plan.get("page_number") or 0)
            if page_number <= 0 or not page_plan.get("image_prompt"):
                continue
            items.append(
                GenericBatchImageItem(
                    key=f"page_{page_number}",
                    page_type="page",
                    page_number=page_number,
                    page_data=page_plan,
                    source_image_prompt=page_plan["image_prompt"],
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

    @staticmethod
    def _render_image_prompt(
        *,
        page_type: str,
        story_title: str,
        visual_bible: dict[str, Any],
        page_image_plan: dict[str, Any],
    ) -> str:
        return load_and_render_prompt(
            "prompts/generic_story/image_generation_prompt.txt",
            {
                "page_type": page_type,
                "story_title": story_title,
                "visual_bible_json": _compact_json(visual_bible),
                "page_image_plan_json": _compact_json(page_image_plan),
            },
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

    async def _mark_workflow_failed(self, workflow_id: UUID, error_message: str) -> None:
        workflow = await self.workflows.get_by_id(workflow_id)
        if workflow is None:
            return
        workflow.status = GenericStoryWorkflowStatus.FAILED.value
        workflow.current_step = None
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
