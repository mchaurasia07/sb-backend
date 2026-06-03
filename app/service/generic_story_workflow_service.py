from __future__ import annotations

from copy import deepcopy
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.generic_story_workflow import (
    GenericStoryWorkflow,
    GenericStoryWorkflowStatus,
    GenericStoryWorkflowStep,
)
from app.model.request.generic_story_workflow import (
    GenericStoryWorkflowCreateRequest,
    GenericStoryWorkflowExecuteRequest,
)
from app.model.response.common import PaginatedResponse
from app.model.response.generic_story import GenericStoryAudioUploadResponse, GenericStoryImageUploadResponse
from app.model.response.generic_story_workflow import (
    GenericStoryWorkflowResponse,
    GenericStoryWorkflowStepDetailResponse,
)
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.generic_story_workflow_repository import GenericStoryWorkflowRepository
from app.service.ai.google_provider import GoogleProvider
from app.service.image_storage_provider import get_image_storage_service
from app.service.story_audio_storage_provider import get_story_audio_storage_service
from app.service.story_narration_service import StoryNarrationService
from app.service.story_narration_profile import build_page_narration, normalize_page_emotion
from app.utils.google_tts_utils import GoogleTTSProvider
from app.utils.prompt_loader import load_and_render_prompt

logger = logging.getLogger(__name__)


SUPPORTED_STORY_LANGUAGES = ("en", "hi", "mr")
STORY_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
}
STORY_LANGUAGE_VARIANTS_KEY = "language_variants"
LANGUAGE_SPECIFIC_PAGE_FIELDS = {
    "audio_url",
    "audio_dummy",
    "tts_model",
    "tts_prompt",
    "tts_skipped",
    "tts_voice",
    "duration",
    "word_timestamps",
}
UPLOAD_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
UPLOAD_AUDIO_CONTENT_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
}
MAX_STORY_AUDIO_UPLOAD_BYTES = 50 * 1024 * 1024


def _repair_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return text


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class GenericStoryWorkflowService:
    """Google-backed workflow for converting actual story text into a generic story."""

    DUMMY_PNG_DATA_URL = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGP4DwQACfsD"
        "/WMmxKkAAAAASUVORK5CYII="
    )
    DUMMY_WAV_DATA_URL = (
        "data:audio/wav;base64,"
        "UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA="
    )
    DUMMY_AUDIO_DURATION_SECONDS = 0.1

    ORDERED_STEPS = [
        GenericStoryWorkflowStep.CHARACTER_EXTRACTION,
        GenericStoryWorkflowStep.SCENE_PLAN_GENERATION,
        GenericStoryWorkflowStep.STORY_GENERATION,
        GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        GenericStoryWorkflowStep.NARRATION_GENERATION,
        GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
    ]

    def __init__(self, session: AsyncSession):
        self.session = session
        self.workflows = GenericStoryWorkflowRepository(session)
        self.generic_stories = GenericStoryRepository(session)
        self.ai_provider = GoogleProvider(
            api_key=settings.GOOGLE_API_KEY,
            image_model=settings.GOOGLE_IMAGE_MODEL,
            text_model=settings.GOOGLE_TEXT_MODEL,
            reference_image_model=settings.GOOGLE_REFERENCE_IMAGE_MODEL,
        )

    async def create(
        self,
        user_id: UUID,
        payload: GenericStoryWorkflowCreateRequest,
    ) -> GenericStoryWorkflowResponse:
        workflow = await self.workflows.create(
            user_id=user_id,
            workflow_name="generic_story",
            actual_story=payload.actual_story,
            age_group=payload.age_group,
            language=payload.language.strip().lower(),
            requested_pages=payload.requested_pages,
            status=GenericStoryWorkflowStatus.PENDING.value,
            input_request=payload.model_dump(),
            title=payload.title,
            theme=payload.theme,
            genre=payload.genre,
            learning_goal=payload.learning_goal,
            ai_provider="google",
            text_model=settings.GOOGLE_TEXT_MODEL,
            image_model=settings.GOOGLE_IMAGE_MODEL,
        )
        await self.session.commit()
        return GenericStoryWorkflowResponse.model_validate(workflow)

    async def get(self, user_id: UUID, workflow_id: UUID) -> GenericStoryWorkflowResponse:
        workflow = await self._get_owned(user_id, workflow_id)
        return GenericStoryWorkflowResponse.model_validate(workflow)

    async def list(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> PaginatedResponse[GenericStoryWorkflowResponse]:
        workflows, total = await self.workflows.list_for_user(user_id, page=page, page_size=page_size)
        return PaginatedResponse[GenericStoryWorkflowResponse].create(
            items=[GenericStoryWorkflowResponse.model_validate(workflow) for workflow in workflows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_steps(
        self,
        user_id: UUID,
        workflow_id: UUID,
        *,
        step_name: str | None = None,
    ) -> list[GenericStoryWorkflowStepDetailResponse]:
        workflow = await self._get_owned(user_id, workflow_id)
        steps = self.ORDERED_STEPS
        if step_name:
            try:
                steps = [GenericStoryWorkflowStep(step_name)]
            except ValueError as exc:
                raise AppException(
                    f"Invalid generic story workflow step: {step_name}",
                    code="GENERIC_STORY_STEP_INVALID",
                ) from exc
        return [
            GenericStoryWorkflowStepDetailResponse(
                workflow_id=workflow.id,
                genric_story_id=self._compact_uuid(workflow.generic_story_id),
                step_name=step.value,
                status=self._step_status(workflow, step),
                summary=self._step_summary(workflow, step),
                output=self._step_output(workflow, step),
                error_message=workflow.error_message
                if workflow.status == GenericStoryWorkflowStatus.FAILED.value
                and self._step_status(workflow, step) == GenericStoryWorkflowStatus.FAILED.value
                else None,
            )
            for step in steps
        ]

    async def execute(
        self,
        user_id: UUID,
        workflow_id: UUID,
        payload: GenericStoryWorkflowExecuteRequest,
        *,
        public_base_url: str,
    ) -> GenericStoryWorkflowResponse:
        workflow = await self._get_owned(user_id, workflow_id)
        steps = self.ORDERED_STEPS if payload.step_name == "ALL" else [GenericStoryWorkflowStep(payload.step_name)]

        try:
            workflow.status = GenericStoryWorkflowStatus.IN_PROGRESS.value
            workflow.error_message = None
            await self.workflows.update(workflow)
            await self.session.commit()

            for step in steps:
                if step == GenericStoryWorkflowStep.IMAGE_GENERATION and payload.skip_image_generation:
                    workflow.current_step = step.value
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    self._generate_dummy_images(workflow)
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    continue
                if step == GenericStoryWorkflowStep.NARRATION_GENERATION and payload.skip_narration_generation:
                    workflow.current_step = step.value
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    self._generate_dummy_narration(workflow)
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    continue

                workflow.current_step = step.value
                await self.workflows.update(workflow)
                await self.session.commit()
                await self._execute_single_step(workflow, step, public_base_url=public_base_url, payload=payload)
                await self.workflows.update(workflow)
                await self.session.commit()

            workflow.current_step = None
            if workflow.generic_story_id is not None:
                workflow.status = GenericStoryWorkflowStatus.COMPLETED.value
            await self.workflows.update(workflow)
            await self.session.commit()
            return GenericStoryWorkflowResponse.model_validate(workflow)

        except Exception as exc:
            logger.exception("Generic story workflow failed: workflow_id=%s step=%s", workflow.id, workflow.current_step)
            workflow.status = GenericStoryWorkflowStatus.FAILED.value
            workflow.error_message = str(exc)
            workflow.current_step = None
            await self.workflows.update(workflow)
            await self.session.commit()
            raise

    async def upload_published_story_images(
        self,
        user_id: UUID,
        workflow_id: UUID,
        generic_story_id: UUID,
        uploads: dict[str, UploadFile],
        *,
        public_base_url: str,
    ) -> GenericStoryImageUploadResponse:
        workflow = await self._get_owned(user_id, workflow_id)
        if workflow.generic_story_id != generic_story_id:
            raise AppException(
                "Generic story does not belong to this workflow",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_STORY_WORKFLOW_MISMATCH",
            )

        story_json = workflow.story_json or {}
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        page_numbers = self._story_page_numbers(pages)
        if not page_numbers:
            raise AppException(
                "Workflow story JSON has no pages to update",
                code="GENERIC_STORY_PAGES_MISSING",
            )

        if workflow.status != GenericStoryWorkflowStatus.COMPLETED.value and workflow.current_step != GenericStoryWorkflowStep.IMAGE_GENERATION.value:
            raise AppException(
                "Workflow must be completed or currently on IMAGE_GENERATION before uploading story images",
                code="GENERIC_STORY_IMAGE_UPLOAD_STEP_INVALID",
            )

        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        cover_upload = uploads.get("cover")
        page_uploads = self._extract_page_uploads(uploads)
        missing_keys = ["cover"] if cover_upload is None else []
        missing_keys.extend(f"page_{page_number}" for page_number in page_numbers if page_number not in page_uploads)
        if missing_keys:
            raise AppException(
                "Missing required image upload fields",
                code="GENERIC_STORY_IMAGE_UPLOADS_MISSING",
                details={"missing_fields": missing_keys},
            )

        image_storage = get_image_storage_service()
        cover_image_url = await self._save_uploaded_story_image(
            image_storage,
            story_id=generic_story_id,
            upload=cover_upload,
            filename_stem="cover",
            public_base_url=public_base_url,
        )
        page_image_urls: dict[int, str] = {}
        for page_number in page_numbers:
            page_image_urls[page_number] = await self._save_uploaded_story_image(
                image_storage,
                story_id=generic_story_id,
                upload=page_uploads[page_number],
                filename_stem=f"page_{page_number}",
                public_base_url=public_base_url,
            )

        self._apply_story_image_urls(story_json, cover_image_url=cover_image_url, page_image_urls=page_image_urls)
        workflow.cover_image = cover_image_url
        workflow.story_json = story_json
        await self.workflows.update(workflow)

        generic_story.cover_image = cover_image_url
        updated_languages: list[str] = []
        for content in generic_story.contents:
            content_story_json = content.story_json if isinstance(content.story_json, dict) else {}
            self._apply_story_image_urls(
                content_story_json,
                cover_image_url=cover_image_url,
                page_image_urls=page_image_urls,
            )
            content.story_json = content_story_json
            await self.generic_stories.update_content(content)
            updated_languages.append(str(content.language))

        await self.session.commit()
        return GenericStoryImageUploadResponse(
            workflow_id=workflow_id,
            generic_story_id=generic_story_id,
            cover_image_url=cover_image_url,
            page_image_urls=page_image_urls,
            updated_languages=sorted(updated_languages),
        )

    async def upload_published_story_audio(
        self,
        user_id: UUID,
        workflow_id: UUID,
        generic_story_id: UUID,
        language: str,
        uploads: dict[str, UploadFile],
    ) -> GenericStoryAudioUploadResponse:
        normalized_language = language.strip().lower()
        workflow = await self._get_owned(user_id, workflow_id)
        if workflow.generic_story_id != generic_story_id:
            raise AppException(
                "Generic story does not belong to this workflow",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_STORY_WORKFLOW_MISMATCH",
            )

        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        contents = list(generic_story.contents or [])
        content = next(
            (
                item
                for item in contents
                if str(item.language).strip().lower() == normalized_language
            ),
            None,
        )
        if content is None:
            raise NotFoundException("Generic story content not found", "GENERIC_STORY_CONTENT_NOT_FOUND")

        content_story_json = content.story_json if isinstance(content.story_json, dict) else {}
        page_numbers = self._story_page_numbers(
            content_story_json.get("pages") if isinstance(content_story_json, dict) else []
        )
        if not page_numbers:
            raise AppException(
                "Generic story content has no pages to update",
                code="GENERIC_STORY_PAGES_MISSING",
            )

        audio_uploads = self._extract_page_audio_uploads(uploads)
        missing_fields = [
            f"page_{page_number}"
            for page_number in page_numbers
            if page_number not in audio_uploads
        ]
        if missing_fields:
            raise AppException(
                "Missing required audio upload fields",
                code="GENERIC_STORY_AUDIO_UPLOADS_MISSING",
                details={"missing_fields": missing_fields},
            )

        audio_storage = get_story_audio_storage_service()
        page_audio_urls: dict[int, str] = {}
        for page_number in page_numbers:
            audio_bytes = await self._read_uploaded_story_audio(audio_uploads[page_number])
            page_audio_urls[page_number] = await audio_storage.save_story_page_audio(
                story_id=workflow.id,
                language=normalized_language,
                page_number=page_number,
                audio_bytes=audio_bytes,
            )

        self._apply_story_audio_urls(content_story_json, page_audio_urls=page_audio_urls)
        content.story_json = content_story_json
        await self.generic_stories.update_content(content)

        self._apply_workflow_audio_urls(
            workflow,
            page_audio_urls={normalized_language: page_audio_urls},
            workflow_language=self._default_story_language(workflow.language),
        )
        await self.workflows.update(workflow)

        await self.session.commit()
        return GenericStoryAudioUploadResponse(
            workflow_id=workflow_id,
            generic_story_id=generic_story_id,
            language=normalized_language,
            page_audio_urls=page_audio_urls,
            updated_languages=[normalized_language],
        )

    async def _execute_single_step(
        self,
        workflow: GenericStoryWorkflow,
        step: GenericStoryWorkflowStep,
        *,
        public_base_url: str,
        payload: GenericStoryWorkflowExecuteRequest,
    ) -> None:
        if step == GenericStoryWorkflowStep.CHARACTER_EXTRACTION:
            workflow.character_analysis_json = await self._generate_character_analysis(workflow)
            self._apply_workflow_metadata(workflow)
            return

        if step == GenericStoryWorkflowStep.SCENE_PLAN_GENERATION:
            self._require(workflow.character_analysis_json, "Run CHARACTER_EXTRACTION before SCENE_PLAN_GENERATION.")
            workflow.scene_plan_json = await self._generate_scene_plan(workflow)
            self._apply_workflow_metadata(workflow)
            return

        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            self._require(workflow.character_analysis_json, "Run CHARACTER_EXTRACTION before STORY_GENERATION.")
            self._require(workflow.scene_plan_json, "Run SCENE_PLAN_GENERATION before STORY_GENERATION.")
            workflow.story_json = await self._generate_story_json(workflow)
            self._apply_workflow_metadata(workflow)
            return

        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            self._require(workflow.scene_plan_json, "Run SCENE_PLAN_GENERATION before IMAGE_PLAN_GENERATION.")
            self._require(workflow.story_json, "Run STORY_GENERATION before IMAGE_PLAN_GENERATION.")
            workflow.image_plan_json = await self._generate_image_plan(workflow)
            return

        if step == GenericStoryWorkflowStep.IMAGE_GENERATION:
            self._require(workflow.story_json, "Run STORY_GENERATION before IMAGE_GENERATION.")
            self._require(workflow.image_plan_json, "Run IMAGE_PLAN_GENERATION before IMAGE_GENERATION.")
            await self._generate_images(workflow, public_base_url=public_base_url)
            self._apply_workflow_metadata(workflow)
            return

        if step == GenericStoryWorkflowStep.NARRATION_GENERATION:
            self._require(workflow.story_json, "Run STORY_GENERATION before NARRATION_GENERATION.")
            workflow.story_json = await self._generate_google_narration(workflow)
            return

        if step == GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY:
            self._require(workflow.story_json, "Run STORY_GENERATION before PUBLISH_GENERIC_STORY.")
            await self._publish_generic_story(
                workflow,
                publish_status=payload.publish_status,
                public_base_url=public_base_url,
            )
            return

        raise AppException(f"Unsupported generic story workflow step: {step}", code="GENERIC_STORY_STEP_INVALID")

    async def _generate_character_analysis(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        if settings.STORY_MOCK_LLM_RESPONSES:
            return self._mock_character_analysis(workflow)
        prompt = load_and_render_prompt(
            "prompts/generic_story/character_extraction_prompt.txt",
            {
                "actual_story": workflow.actual_story,
                "title": workflow.title or "",
                "theme": workflow.theme or "",
                "genre": workflow.genre or "",
                "learning_goal": workflow.learning_goal or "",
            },
        )
        return await self._generate_json(prompt, max_tokens=6000)

    async def _generate_scene_plan(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        if settings.STORY_MOCK_LLM_RESPONSES:
            return self._mock_scene_plan(workflow)
        prompt = load_and_render_prompt(
            "prompts/generic_story/scene_plan_prompt.txt",
            {
                "age_group": workflow.age_group,
                "requested_pages": workflow.requested_pages or "",
                "title": workflow.title or "",
                "actual_story": workflow.actual_story,
                "character_analysis_json": _compact_json(workflow.character_analysis_json),
            },
        )
        plan = await self._generate_json(prompt, max_tokens=12000)
        expected_pages = workflow.requested_pages or self._default_page_count(workflow.age_group)
        pages = plan.get("pages")
        if not isinstance(pages, list) or len(pages) != expected_pages:
            raise AppException(
                f"Scene plan returned {len(pages) if isinstance(pages, list) else 0} pages; expected {expected_pages}",
                code="GENERIC_SCENE_PLAN_PAGE_COUNT_MISMATCH",
            )
        return plan

    async def _generate_story_json(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        if settings.STORY_MOCK_LLM_RESPONSES:
            return self._normalize_story_json(self._mock_story_json(workflow), workflow)
        prompt = load_and_render_prompt(
            "prompts/generic_story/story_generation_prompt.txt",
            {
                "actual_story": workflow.actual_story,
                "age_group": workflow.age_group,
                "title": workflow.title or "",
                "requested_language": self._default_story_language(workflow.language),
                "character_analysis_json": _compact_json(workflow.character_analysis_json),
                "scene_plan_json": _compact_json(workflow.scene_plan_json),
            },
        )
        raw = await self._generate_json(prompt, max_tokens=24000)
        return self._normalize_story_json(raw, workflow)

    async def _generate_image_plan(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        if settings.STORY_MOCK_LLM_RESPONSES:
            return self._mock_image_plan(workflow)
        prompt = load_and_render_prompt(
            "prompts/generic_story/image_plan_prompt.txt",
            {
                "scene_plan_json": _compact_json(workflow.scene_plan_json),
                "story_json": _compact_json(self._story_json_without_language_variants(workflow.story_json or {})),
                "character_analysis_json": _compact_json(workflow.character_analysis_json),
            },
        )
        image_plan = await self._generate_json(prompt, max_tokens=16000)
        pages = image_plan.get("pages")
        story_pages = (workflow.story_json or {}).get("pages") or []
        if not isinstance(pages, list) or len(pages) != len(story_pages):
            raise AppException("Image plan page count must match story JSON pages.", code="GENERIC_IMAGE_PLAN_PAGE_COUNT_MISMATCH")
        return image_plan

    async def _generate_images(self, workflow: GenericStoryWorkflow, *, public_base_url: str) -> None:
        image_storage = get_image_storage_service()
        story_json = workflow.story_json or {}
        image_plan = workflow.image_plan_json or {}
        visual_bible = image_plan.get("visual_bible") or (workflow.scene_plan_json or {}).get("visual_bible") or {}
        story_title = story_json.get("title") or workflow.title or "Untitled Story"

        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        if cover_plan:
            cover_prompt = self._render_image_prompt(
                page_type="cover",
                story_title=story_title,
                visual_bible=visual_bible,
                page_image_plan=cover_plan,
            )
            cover_result = await self.ai_provider.generate_image(
                cover_prompt,
                aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
            )
            workflow.cover_image = await image_storage.save_story_image(
                workflow.id,
                cover_result.image_bytes,
                "cover.png",
                public_base_url,
            )
            story_json["cover_image_url"] = workflow.cover_image
            story_json["cover_image_prompt"] = cover_prompt
            story_json["cover_planned_image_prompt"] = cover_plan.get("image_prompt")

        plan_pages = image_plan.get("pages") if isinstance(image_plan.get("pages"), list) else []
        story_pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        story_pages_by_number = {
            page.get("page_number"): page
            for page in story_pages
            if isinstance(page, dict)
        }
        for plan_page in plan_pages:
            if not isinstance(plan_page, dict):
                continue
            page_number = plan_page.get("page_number")
            if not isinstance(page_number, int):
                continue
            page_prompt = self._render_image_prompt(
                page_type="story_page",
                story_title=story_title,
                visual_bible=visual_bible,
                page_image_plan=plan_page,
            )
            result = await self.ai_provider.generate_image(
                page_prompt,
                aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
            )
            image_url = await image_storage.save_story_image(
                workflow.id,
                result.image_bytes,
                f"page_{page_number}.png",
                public_base_url,
            )
            story_page = story_pages_by_number.get(page_number)
            if story_page is not None:
                story_page["image_url"] = image_url
                story_page["image_prompt"] = page_prompt
                story_page["planned_image_prompt"] = plan_page.get("image_prompt")

        workflow.story_json = story_json

    def _generate_dummy_images(self, workflow: GenericStoryWorkflow) -> None:
        """Attach generated image prompts and dummy image URLs without image LLM/storage calls."""
        story_json = workflow.story_json or {}
        image_plan = workflow.image_plan_json or {}
        visual_bible = image_plan.get("visual_bible") or (workflow.scene_plan_json or {}).get("visual_bible") or {}
        story_title = story_json.get("title") or workflow.title or "Untitled Story"

        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        if cover_plan:
            cover_prompt = self._render_image_prompt(
                page_type="cover",
                story_title=story_title,
                visual_bible=visual_bible,
                page_image_plan=cover_plan,
            )
            workflow.cover_image = self.DUMMY_PNG_DATA_URL
            story_json["cover_image_url"] = self.DUMMY_PNG_DATA_URL
            story_json["cover_image_prompt"] = cover_prompt
            story_json["cover_planned_image_prompt"] = cover_plan.get("image_prompt")
            story_json["cover_image_dummy"] = True

        plan_pages = image_plan.get("pages") if isinstance(image_plan.get("pages"), list) else []
        prompts_by_page = {
            page.get("page_number"): page
            for page in plan_pages
            if isinstance(page, dict) and isinstance(page.get("page_number"), int)
        }
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_number = page.get("page_number")
            plan_page = prompts_by_page.get(page_number) if isinstance(page_number, int) else None
            page["image_url"] = self.DUMMY_PNG_DATA_URL
            page["image_dummy"] = True
            if isinstance(plan_page, dict):
                page["image_prompt"] = self._render_image_prompt(
                    page_type="story_page",
                    story_title=story_title,
                    visual_bible=visual_bible,
                    page_image_plan=plan_page,
                )
                page["planned_image_prompt"] = plan_page.get("image_prompt")

        workflow.story_json = story_json

    async def _generate_google_narration(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        story_json = dict(workflow.story_json or {})
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        if not isinstance(variants, dict):
            return await StoryNarrationService(self.session).generate_story_json_narration(
                story_json,
                story_id=workflow.id,
                language=workflow.language,
                overwrite=False,
                source="generic_story_workflow",
                age_group=workflow.age_group,
            )

        narration_service = StoryNarrationService(self.session)
        workflow_language = self._default_story_language(workflow.language)
        narrated_variants: dict[str, dict[str, Any]] = {}
        default_story_json: dict[str, Any] | None = None

        for language in SUPPORTED_STORY_LANGUAGES:
            if not isinstance(variants.get(language), dict):
                continue
            language_story_json = self._story_json_for_language(
                story_json,
                variants,
                language=language,
                workflow_language=workflow_language,
            )
            narrated_story_json = await narration_service.generate_story_json_narration(
                language_story_json,
                story_id=workflow.id,
                language=language,
                overwrite=False,
                source="generic_story_workflow",
                age_group=workflow.age_group,
            )
            narrated_variants[language] = self._story_json_without_language_variants(narrated_story_json)
            if language == workflow_language:
                default_story_json = narrated_story_json

        if default_story_json is None:
            default_story_json = next(iter(narrated_variants.values()), self._story_json_without_language_variants(story_json))
        default_story_json = self._story_json_without_language_variants(default_story_json)
        default_story_json[STORY_LANGUAGE_VARIANTS_KEY] = narrated_variants
        return default_story_json

    def _generate_dummy_narration(self, workflow: GenericStoryWorkflow) -> None:
        """Attach real rendered TTS prompts and dummy WAV data URLs without Google TTS/storage calls."""
        story_json = workflow.story_json or {}
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        if isinstance(variants, dict):
            workflow_language = self._default_story_language(workflow.language)
            narrated_variants: dict[str, dict[str, Any]] = {}
            default_story_json: dict[str, Any] | None = None
            for language in SUPPORTED_STORY_LANGUAGES:
                if not isinstance(variants.get(language), dict):
                    continue
                language_story_json = self._story_json_for_language(
                    story_json,
                    variants,
                    language=language,
                    workflow_language=workflow_language,
                )
                self._generate_dummy_narration_for_story_json(
                    language_story_json,
                    language=language,
                    age_group=workflow.age_group,
                )
                narrated_variants[language] = self._story_json_without_language_variants(language_story_json)
                if language == workflow_language:
                    default_story_json = language_story_json

            if default_story_json is None:
                default_story_json = next(iter(narrated_variants.values()), self._story_json_without_language_variants(story_json))
            default_story_json = self._story_json_without_language_variants(default_story_json)
            default_story_json[STORY_LANGUAGE_VARIANTS_KEY] = narrated_variants
            workflow.story_json = default_story_json
            return

        self._generate_dummy_narration_for_story_json(
            story_json,
            language=workflow.language,
            age_group=workflow.age_group,
        )
        workflow.story_json = story_json

    def _generate_dummy_narration_for_story_json(
        self,
        story_json: dict[str, Any],
        *,
        language: str,
        age_group: str,
    ) -> None:
        tts_provider = GoogleTTSProvider()
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for page in pages:
            if not isinstance(page, dict):
                continue
            text = str(page.get("text") or "").strip()
            emotion = normalize_page_emotion(page.get("emotion"))
            narration = page.get("narration") if isinstance(page.get("narration"), dict) else {}
            derived = build_page_narration(emotion, age_group)
            narration = {
                "tone": narration.get("tone") or derived["tone"],
                "pace": narration.get("pace") or derived["pace"],
                "voice_style": narration.get("voice_style") or derived["voice_style"],
            }
            tts_prompt = (
                tts_provider.build_prompt(
                    text,
                    pace=narration["pace"],
                    language=language,
                    voice_style=narration["voice_style"],
                    tone=narration["tone"],
                    emotion=emotion,
                )
                if text
                else ""
            )
            page["narration"] = narration
            page["audio_url"] = self.DUMMY_WAV_DATA_URL
            page["audio_dummy"] = True
            page["tts_skipped"] = True
            page["tts_model"] = settings.GOOGLE_TTS_MODEL
            page["tts_voice"] = settings.GOOGLE_TTS_VOICE
            page["tts_prompt"] = tts_prompt
            page["duration"] = self.DUMMY_AUDIO_DURATION_SECONDS
            page["word_timestamps"] = []


    async def _publish_generic_story(
        self,
        workflow: GenericStoryWorkflow,
        *,
        publish_status: str | None,
        public_base_url: str,
    ) -> None:
        story_json = workflow.story_json or {}
        title = str(story_json.get("title") or workflow.title or "Untitled Story")[:255]
        data = {
            "title": title,
            "summary": workflow.summary or story_json.get("summary") or "",
            "age_group": workflow.age_group,
            "theme": workflow.theme,
            "genre": workflow.genre,
            "moral": workflow.moral or story_json.get("moral"),
            "learning_goal": workflow.learning_goal,
            "reading_time_minutes": self._estimate_reading_time(story_json),
            "character_type": self._character_type(workflow.character_analysis_json),
            "total_pages": len(story_json.get("pages") or []),
            "cover_image": workflow.cover_image or story_json.get("cover_image_url"),
            "status": publish_status or (workflow.input_request or {}).get("status") or "inactive",
        }

        generic_story = await self.generic_stories.get_by_title(title)
        if generic_story is None:
            generic_story = await self.generic_stories.create(**data)
        else:
            for field, value in data.items():
                setattr(generic_story, field, value)

        story_json = await self._copy_story_images_to_generic_story_storage(
            story_json,
            generic_story_id=generic_story.id,
            public_base_url=public_base_url,
        )
        workflow.story_json = story_json
        workflow.cover_image = story_json.get("cover_image_url") or workflow.cover_image
        generic_story.cover_image = workflow.cover_image

        await self.generic_stories.upsert_contents(
            generic_story,
            self._story_content_payloads(story_json, workflow_language=workflow.language),
        )
        workflow.generic_story_id = generic_story.id
        workflow.status = GenericStoryWorkflowStatus.COMPLETED.value

    async def _copy_story_images_to_generic_story_storage(
        self,
        story_json: dict[str, Any],
        *,
        generic_story_id: UUID,
        public_base_url: str,
    ) -> dict[str, Any]:
        updated = deepcopy(story_json)
        image_storage = get_image_storage_service()

        cover_image_url = await self._copy_story_image_url(
            image_storage,
            image_url=updated.get("cover_image_url"),
            generic_story_id=generic_story_id,
            filename="cover.png",
            public_base_url=public_base_url,
        )
        if cover_image_url:
            updated["cover_image_url"] = cover_image_url
            updated.pop("cover_image_dummy", None)

        pages = updated.get("pages") if isinstance(updated.get("pages"), list) else []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            raw_page_number = page.get("page_number", index)
            try:
                page_number = int(raw_page_number)
            except (TypeError, ValueError):
                continue
            image_url = await self._copy_story_image_url(
                image_storage,
                image_url=page.get("image_url"),
                generic_story_id=generic_story_id,
                filename=f"page_{page_number}.png",
                public_base_url=public_base_url,
            )
            if image_url:
                page["image_url"] = image_url
                page.pop("image_dummy", None)

        return updated

    @staticmethod
    async def _copy_story_image_url(
        image_storage: Any,
        *,
        image_url: str | None,
        generic_story_id: UUID,
        filename: str,
        public_base_url: str,
    ) -> str | None:
        if not image_url or str(image_url).startswith("data:"):
            return image_url
        normalized_url = str(image_url).replace("\\", "/")
        if f"/stories/{generic_story_id}/" in normalized_url:
            return image_url
        try:
            image_bytes = await image_storage.get_image_bytes(image_url)
            return await image_storage.save_story_image(
                generic_story_id,
                image_bytes,
                filename,
                public_base_url,
            )
        except Exception:
            logger.exception(
                "Failed to copy generic story image into story storage: generic_story_id=%s filename=%s",
                generic_story_id,
                filename,
            )
            return image_url

    async def _generate_json(self, prompt: str, *, max_tokens: int) -> dict[str, Any]:
        result = await self.ai_provider.generate_text(
            prompt,
            max_tokens=max_tokens,
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError:
            parsed = json.loads(_repair_json(result.text))
        if not isinstance(parsed, dict):
            raise AppException("Google returned JSON that is not an object.", code="GENERIC_WORKFLOW_INVALID_JSON")
        return parsed

    def _render_image_prompt(
        self,
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

    def _normalize_story_json(self, raw: dict[str, Any], workflow: GenericStoryWorkflow) -> dict[str, Any]:
        default_language = self._default_story_language(getattr(workflow, "language", None))
        pages = []
        variant_pages: dict[str, list[dict[str, Any]]] = {language: [] for language in SUPPORTED_STORY_LANGUAGES}
        for index, page in enumerate(raw.get("pages") or [], start=1):
            if not isinstance(page, dict):
                continue
            page_texts = self._localized_text_map(page.get("text"), default_language=default_language)
            if not any(page_texts.values()):
                continue
            emotion = normalize_page_emotion(page.get("emotion"))
            narration = build_page_narration(emotion, workflow.age_group)
            page_number = len(pages) + 1
            pages.append(
                {
                    "page_number": page_number,
                    "emotion": emotion,
                    "text": page_texts[default_language],
                    "narration": narration,
                }
            )
            for language in SUPPORTED_STORY_LANGUAGES:
                variant_pages[language].append(
                    {
                        "page_number": page_number,
                        "emotion": emotion,
                        "text": page_texts[language],
                        "narration": dict(narration),
                    }
                )

        expected_pages = len((workflow.scene_plan_json or {}).get("pages") or [])
        if not pages:
            raise AppException("Story generation returned no pages.", code="GENERIC_STORY_EMPTY")
        if expected_pages and len(pages) != expected_pages:
            raise AppException(
                f"Story generation returned {len(pages)} pages; expected {expected_pages}.",
                code="GENERIC_STORY_PAGE_COUNT_MISMATCH",
            )

        title_by_language = self._localized_text_map(
            raw.get("title"),
            default_language=default_language,
            fallback=(workflow.scene_plan_json or {}).get("title") or "Untitled Story",
        )
        summary_by_language = self._localized_text_map(
            raw.get("summary"),
            default_language=default_language,
            fallback=(workflow.scene_plan_json or {}).get("summary") or "",
        )
        moral_by_language = self._localized_text_map(
            raw.get("moral"),
            default_language=default_language,
            fallback=(workflow.scene_plan_json or {}).get("moral_explanation") or "",
        )
        language_variants = {
            language: {
                "title": title_by_language[language],
                "summary": summary_by_language[language],
                "pages": variant_pages[language],
                "moral": moral_by_language[language],
            }
            for language in SUPPORTED_STORY_LANGUAGES
        }

        return {
            "title": title_by_language[default_language],
            "summary": summary_by_language[default_language],
            "pages": pages,
            "moral": moral_by_language[default_language],
            STORY_LANGUAGE_VARIANTS_KEY: language_variants,
        }

    @staticmethod
    def _default_story_language(language: str | None) -> str:
        normalized_language = str(language or "").strip().lower()
        if normalized_language in SUPPORTED_STORY_LANGUAGES:
            return normalized_language
        return "en"

    @staticmethod
    def _story_json_without_language_variants(story_json: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(story_json)
        cleaned.pop(STORY_LANGUAGE_VARIANTS_KEY, None)
        return cleaned

    def _localized_text_map(
        self,
        value: Any,
        *,
        default_language: str,
        fallback: Any = "",
    ) -> dict[str, str]:
        fallback_text = str(fallback or "").strip()
        texts: dict[str, str] = {}
        if isinstance(value, dict):
            for language in SUPPORTED_STORY_LANGUAGES:
                language_name = STORY_LANGUAGE_NAMES[language]
                text = value.get(language)
                if text is None:
                    text = value.get(language_name) or value.get(language_name.lower())
                texts[language] = str(text or "").strip()
        else:
            text = str(value or "").strip()
            texts = {language: text for language in SUPPORTED_STORY_LANGUAGES}

        fallback_candidates = [
            texts.get(default_language, ""),
            texts.get("en", ""),
            fallback_text,
        ]
        replacement = next((text for text in fallback_candidates if text), "")
        return {
            language: text or replacement
            for language, text in texts.items()
        }

    def _story_content_payloads(
        self,
        story_json: dict[str, Any],
        *,
        workflow_language: str,
    ) -> list[dict[str, Any]]:
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        if not isinstance(variants, dict):
            return [
                {
                    "language": self._default_story_language(workflow_language),
                    "story_json": self._story_json_without_language_variants(story_json),
                }
            ]

        default_language = self._default_story_language(workflow_language)
        return [
            {
                "language": language,
                "story_json": self._story_json_for_language(
                    story_json,
                    variants,
                    language=language,
                    workflow_language=default_language,
                ),
            }
            for language in SUPPORTED_STORY_LANGUAGES
            if isinstance(variants.get(language), dict)
        ]

    def _story_json_for_language(
        self,
        story_json: dict[str, Any],
        variants: dict[str, Any],
        *,
        language: str,
        workflow_language: str,
    ) -> dict[str, Any]:
        base_story_json = self._story_json_without_language_variants(story_json)
        localized = variants.get(language) if isinstance(variants.get(language), dict) else {}
        result = deepcopy(base_story_json)
        result["title"] = str(localized.get("title") or base_story_json.get("title") or "Untitled Story")
        result["summary"] = str(localized.get("summary") or base_story_json.get("summary") or "")
        result["moral"] = str(localized.get("moral") or base_story_json.get("moral") or "")

        localized_pages = localized.get("pages") if isinstance(localized.get("pages"), list) else []
        localized_by_number = {
            page.get("page_number"): page
            for page in localized_pages
            if isinstance(page, dict)
        }
        pages = []
        for index, base_page in enumerate(base_story_json.get("pages") or [], start=1):
            if not isinstance(base_page, dict):
                continue
            page_number = base_page.get("page_number", index)
            localized_page = localized_by_number.get(page_number) or {}
            page = deepcopy(base_page)
            if language != workflow_language:
                for field in LANGUAGE_SPECIFIC_PAGE_FIELDS:
                    page.pop(field, None)
            page["text"] = str(localized_page.get("text") or base_page.get("text") or "").strip()
            page["emotion"] = normalize_page_emotion(localized_page.get("emotion") or base_page.get("emotion"))
            narration = localized_page.get("narration") if isinstance(localized_page.get("narration"), dict) else None
            if narration is not None:
                page["narration"] = narration
            for field in LANGUAGE_SPECIFIC_PAGE_FIELDS:
                if field in localized_page:
                    page[field] = localized_page[field]
            pages.append(page)
        result["pages"] = pages
        return result

    @staticmethod
    def _story_page_numbers(pages: list[Any]) -> list[int]:
        page_numbers: list[int] = []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            raw_page_number = page.get("page_number", index)
            try:
                page_number = int(raw_page_number)
            except (TypeError, ValueError):
                continue
            if page_number > 0:
                page_numbers.append(page_number)
        return sorted(set(page_numbers))

    @staticmethod
    def _extract_page_uploads(uploads: dict[str, UploadFile]) -> dict[int, UploadFile]:
        page_uploads: dict[int, UploadFile] = {}
        for field_name, upload in uploads.items():
            normalized = field_name.strip().lower()
            if normalized.startswith("page_"):
                raw_page_number = normalized.removeprefix("page_")
            elif normalized.startswith("page"):
                raw_page_number = normalized.removeprefix("page")
            else:
                continue
            if not raw_page_number.isdigit():
                continue
            page_number = int(raw_page_number)
            if page_number in page_uploads:
                raise AppException(
                    f"Duplicate upload provided for page {page_number}",
                    code="GENERIC_STORY_PAGE_IMAGE_DUPLICATE",
                )
            page_uploads[page_number] = upload
        return page_uploads

    @staticmethod
    def _extract_page_audio_uploads(uploads: dict[str, UploadFile]) -> dict[int, UploadFile]:
        page_uploads: dict[int, UploadFile] = {}
        for field_name, upload in uploads.items():
            normalized = field_name.strip().lower()
            if normalized.startswith("page_"):
                raw_page_number = normalized.removeprefix("page_")
            elif normalized.startswith("page"):
                raw_page_number = normalized.removeprefix("page")
            else:
                continue
            if not raw_page_number.isdigit():
                continue
            page_number = int(raw_page_number)
            if page_number in page_uploads:
                raise AppException(
                    f"Duplicate audio upload provided for page {page_number}",
                    code="GENERIC_STORY_PAGE_AUDIO_DUPLICATE",
                )
            page_uploads[page_number] = upload
        return page_uploads

    @staticmethod
    async def _save_uploaded_story_image(
        image_storage: Any,
        *,
        story_id: UUID,
        upload: UploadFile,
        filename_stem: str,
        public_base_url: str,
    ) -> str:
        extension = GenericStoryWorkflowService._upload_image_extension(upload)
        content = await upload.read()
        if not content:
            raise AppException("Image file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_IMAGE")
        if len(content) > settings.IMAGE_MAX_UPLOAD_BYTES:
            raise AppException(
                "Image must be 5 MB or smaller",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "IMAGE_TOO_LARGE",
            )
        return await image_storage.save_story_image(
            story_id,
            content,
            f"{filename_stem}{extension}",
            public_base_url,
        )

    @staticmethod
    def _upload_image_extension(upload: UploadFile) -> str:
        content_type = str(upload.content_type or "").lower()
        if content_type in UPLOAD_IMAGE_CONTENT_TYPES:
            return UPLOAD_IMAGE_CONTENT_TYPES[content_type]

        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in set(UPLOAD_IMAGE_CONTENT_TYPES.values()):
            return suffix

        raise AppException(
            "Image must be a JPEG, PNG, or WEBP file",
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_IMAGE_TYPE",
        )

    @staticmethod
    async def _read_uploaded_story_audio(upload: UploadFile) -> bytes:
        GenericStoryWorkflowService._validate_audio_upload_type(upload)
        content = await upload.read()
        if not content:
            raise AppException("Audio file is empty", status.HTTP_400_BAD_REQUEST, "EMPTY_AUDIO")
        if len(content) > MAX_STORY_AUDIO_UPLOAD_BYTES:
            raise AppException(
                "Audio file must be 50 MB or smaller",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "AUDIO_TOO_LARGE",
            )
        return content

    @staticmethod
    def _validate_audio_upload_type(upload: UploadFile) -> None:
        content_type = str(upload.content_type or "").lower()
        if content_type in UPLOAD_AUDIO_CONTENT_TYPES:
            return

        suffix = Path(upload.filename or "").suffix.lower()
        if suffix == ".wav":
            return

        raise AppException(
            "Audio must be a WAV file",
            status.HTTP_400_BAD_REQUEST,
            "UNSUPPORTED_AUDIO_TYPE",
        )

    @staticmethod
    def _apply_story_image_urls(
        story_json: dict[str, Any],
        *,
        cover_image_url: str,
        page_image_urls: dict[int, str],
    ) -> None:
        story_json["cover_image_url"] = cover_image_url
        story_json.pop("cover_image_dummy", None)

        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            raw_page_number = page.get("page_number", index)
            try:
                page_number = int(raw_page_number)
            except (TypeError, ValueError):
                continue
            image_url = page_image_urls.get(page_number)
            if image_url is None:
                continue
            page["image_url"] = image_url
            page.pop("image_dummy", None)

    @staticmethod
    def _apply_story_audio_urls(
        story_json: dict[str, Any],
        *,
        page_audio_urls: dict[int, str],
    ) -> None:
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            raw_page_number = page.get("page_number", index)
            try:
                page_number = int(raw_page_number)
            except (TypeError, ValueError):
                continue
            audio_url = page_audio_urls.get(page_number)
            if audio_url is None:
                continue
            page["audio_url"] = audio_url
            page.pop("audio_dummy", None)
            page.pop("tts_skipped", None)

    def _apply_workflow_audio_urls(
        self,
        workflow: GenericStoryWorkflow,
        *,
        page_audio_urls: dict[str, dict[int, str]],
        workflow_language: str,
    ) -> None:
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        self._apply_story_audio_urls(
            story_json,
            page_audio_urls=page_audio_urls.get(workflow_language, {}),
        )
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        if isinstance(variants, dict):
            for language, language_story_json in variants.items():
                if not isinstance(language_story_json, dict):
                    continue
                self._apply_story_audio_urls(
                    language_story_json,
                    page_audio_urls=page_audio_urls.get(str(language).strip().lower(), {}),
                )
        workflow.story_json = story_json

    def _mock_character_analysis(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        return {
            "source_title": workflow.title or "The Helpful Adventure",
            "summary": "A hero notices a problem, helps carefully, and learns the original lesson.",
            "theme": workflow.theme or "kindness",
            "genre": workflow.genre or "adventure",
            "learning_goal": workflow.learning_goal or "helping others",
            "moral": "Small helpful actions can make a big difference.",
            "setting": "a bright storybook village",
            "central_conflict": "Something important is not working and the hero must help without giving up.",
            "ending_meaning": "The hero learns that careful, kind effort matters.",
            "characters": [
                {
                    "name": "Mira",
                    "role": "hero",
                    "type": "human",
                    "stable_identity": "A kind child hero who wants to help.",
                    "appearance_lock": "Mira has a round face, warm brown eyes, short dark hair, a blue tunic, yellow scarf, and red shoes.",
                    "personality_lock": "curious, kind, and persistent",
                    "relationship_to_hero": "self",
                    "must_appear_in_pages": [],
                },
                {
                    "name": "Luma",
                    "role": "companion",
                    "type": "animal",
                    "stable_identity": "A small silver owl companion.",
                    "appearance_lock": "Luma is a tiny silver owl with round amber eyes, soft speckled wings, and a little green ribbon.",
                    "personality_lock": "gentle and encouraging",
                    "relationship_to_hero": "companion",
                    "must_appear_in_pages": [],
                },
            ],
            "continuity_rules": ["Mira and Luma keep the same appearance on every page."],
            "do_not_change": ["Preserve the original problem, helping action, and moral."],
        }

    def _mock_scene_plan(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        page_count = workflow.requested_pages or self._default_page_count(workflow.age_group)
        pages = []
        for page_number in range(1, page_count + 1):
            if page_number == 1:
                role = "introduction"
            elif page_number == page_count - 1:
                role = "climax"
            elif page_number == page_count:
                role = "resolution"
            else:
                role = "build"
            pages.append(
                {
                    "page_number": page_number,
                    "story_role": role,
                    "scene_description": f"Mira and Luma follow the source story moment {page_number}.",
                    "characters_present": ["Mira", "Luma"],
                    "main_action": "Mira helps carefully while Luma stays beside her.",
                    "emotional_beat": "wonder" if page_number == 1 else "determination",
                    "source_story_connection": "This page preserves the original story sequence.",
                    "growth_or_meaning_step": "Mira keeps trying with kindness.",
                    "page_turn_hook": "The next clue waits ahead.",
                    "visual_continuity": ["Mira keeps the blue tunic, yellow scarf, and red shoes.", "Luma keeps the green ribbon."],
                }
            )
        return {
            "title": workflow.title or "The Helpful Adventure",
            "summary": "Mira follows the original story and learns that helpful actions matter.",
            "theme": workflow.theme or "kindness",
            "genre": workflow.genre or "adventure",
            "learning_goal": workflow.learning_goal or "helping others",
            "moral_theme": "kindness and persistence",
            "moral_explanation": "Small helpful actions can make a big difference.",
            "setting": "a bright storybook village",
            "tone": "warm and adventurous",
            "source_preservation": {
                "central_conflict": "The original problem is preserved.",
                "must_preserve": ["original meaning", "original moral", "original ending"],
                "do_not_add": ["random new characters", "new moral", "changed conflict"],
            },
            "visual_bible": {
                "style": "Premium semi-realistic 3D children's storybook illustration.",
                "characters": [
                    {
                        "name": "Mira",
                        "role": "hero",
                        "appearance": "Round face, warm brown eyes, short dark hair.",
                        "outfit_or_body_markings": "Blue tunic, yellow scarf, red shoes.",
                        "size_relative_to_hero": "hero",
                        "distinctive_feature": "yellow scarf",
                    },
                    {
                        "name": "Luma",
                        "role": "companion",
                        "appearance": "Small silver owl with amber eyes.",
                        "outfit_or_body_markings": "Speckled wings and green ribbon.",
                        "size_relative_to_hero": "tiny beside Mira",
                        "distinctive_feature": "green ribbon",
                    },
                ],
                "locations": ["storybook village"],
                "important_objects": ["helpful clue"],
            },
            "pages": pages,
        }

    def _mock_story_json(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        pages = []
        for page in (workflow.scene_plan_json or {}).get("pages") or []:
            page_number = page.get("page_number", len(pages) + 1)
            pages.append(
                {
                    "page_number": page_number,
                    "emotion": page.get("emotional_beat") or "wonder",
                    "text": {
                        "en": f"Mira and Luma moved through page {page_number} of the adventure, keeping the original story's meaning safe.",
                        "hi": f"मीरा और लूमा ने रोमांच के पेज {page_number} में आगे बढ़कर मूल कहानी का अर्थ सुरक्षित रखा.",
                        "mr": f"मीरा आणि लूमा साहसाच्या पान {page_number} मधून पुढे गेले आणि मूळ कथेचा अर्थ जपला.",
                    },
                }
            )
        return {
            "title": {
                "en": (workflow.scene_plan_json or {}).get("title") or "The Helpful Adventure",
                "hi": (workflow.scene_plan_json or {}).get("title") or "The Helpful Adventure",
                "mr": (workflow.scene_plan_json or {}).get("title") or "The Helpful Adventure",
            },
            "summary": {
                "en": (workflow.scene_plan_json or {}).get("summary") or "",
                "hi": (workflow.scene_plan_json or {}).get("summary") or "",
                "mr": (workflow.scene_plan_json or {}).get("summary") or "",
            },
            "pages": pages,
            "moral": {
                "en": (workflow.scene_plan_json or {}).get("moral_explanation") or "Kindness matters.",
                "hi": "दयालुता मायने रखती है.",
                "mr": "दयाळूपणा महत्त्वाचा असतो.",
            },
        }

    def _mock_image_plan(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        scene_plan = workflow.scene_plan_json or {}
        story_json = workflow.story_json or {}
        visual_bible = scene_plan.get("visual_bible") or {}
        pages = []
        for page in story_json.get("pages") or []:
            page_number = page.get("page_number", len(pages) + 1)
            pages.append(
                {
                    "page_number": page_number,
                    "story_role": "build",
                    "emotion": page.get("emotion") or "wonder",
                    "scene_action": "Mira and Luma act out the planned source-story moment.",
                    "environment": "bright storybook village",
                    "characters_present": ["Mira", "Luma"],
                    "image_prompt": "Mira in blue tunic and yellow scarf with Luma the silver owl, consistent storybook scene, no character redesign.",
                }
            )
        return {
            "visual_bible": visual_bible,
            "cover": {
                "title_text": story_json.get("title") or "The Helpful Adventure",
                "visual_focus": "Mira and Luma in the storybook village",
                "emotion": "wonder",
                "image_prompt": "Clean storybook cover with Mira and Luma, exact title text, no black rectangle.",
            },
            "pages": pages,
        }

    def _apply_workflow_metadata(self, workflow: GenericStoryWorkflow) -> None:
        character = workflow.character_analysis_json or {}
        scene_plan = workflow.scene_plan_json or {}
        story_json = workflow.story_json or {}
        workflow.title = str(story_json.get("title") or scene_plan.get("title") or character.get("source_title") or "")[:255] or None
        workflow.summary = str(story_json.get("summary") or scene_plan.get("summary") or character.get("summary") or "") or None
        workflow.theme = str(scene_plan.get("theme") or character.get("theme") or workflow.theme or "")[:100] or None
        workflow.genre = str(scene_plan.get("genre") or character.get("genre") or workflow.genre or "")[:100] or None
        workflow.learning_goal = str(scene_plan.get("learning_goal") or character.get("learning_goal") or workflow.learning_goal or "")[:500] or None
        workflow.moral = str(story_json.get("moral") or scene_plan.get("moral_explanation") or character.get("moral") or "")[:255] or None

    async def _get_owned(self, user_id: UUID, workflow_id: UUID) -> GenericStoryWorkflow:
        workflow = await self.workflows.get_for_user(user_id, workflow_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")
        return workflow

    @staticmethod
    def _compact_uuid(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return value.hex
        return str(value).replace("-", "")

    @staticmethod
    def _require(value: Any, message: str) -> None:
        if not value:
            raise AppException(message, code="GENERIC_STORY_WORKFLOW_DEPENDENCY_MISSING")

    def _step_status(self, workflow: GenericStoryWorkflow, step: GenericStoryWorkflowStep) -> str:
        if workflow.current_step == step.value and workflow.status == GenericStoryWorkflowStatus.IN_PROGRESS.value:
            return "IN_PROGRESS"
        if workflow.status == GenericStoryWorkflowStatus.FAILED.value:
            completed_before_failure = self._step_is_complete(workflow, step)
            return "COMPLETED" if completed_before_failure else "FAILED"
        return "COMPLETED" if self._step_is_complete(workflow, step) else "PENDING"

    def _step_is_complete(self, workflow: GenericStoryWorkflow, step: GenericStoryWorkflowStep) -> bool:
        if step == GenericStoryWorkflowStep.CHARACTER_EXTRACTION:
            return bool(workflow.character_analysis_json)
        if step == GenericStoryWorkflowStep.SCENE_PLAN_GENERATION:
            return bool(workflow.scene_plan_json)
        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            return bool(workflow.story_json)
        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return bool(workflow.image_plan_json)
        if step == GenericStoryWorkflowStep.IMAGE_GENERATION:
            return self._story_has_images(workflow.story_json or {})
        if step == GenericStoryWorkflowStep.NARRATION_GENERATION:
            return self._story_has_audio(workflow.story_json or {})
        if step == GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY:
            return workflow.generic_story_id is not None
        return False

    def _step_summary(self, workflow: GenericStoryWorkflow, step: GenericStoryWorkflowStep) -> dict[str, Any]:
        if step == GenericStoryWorkflowStep.CHARACTER_EXTRACTION:
            characters = (workflow.character_analysis_json or {}).get("characters") or []
            return {
                "title": (workflow.character_analysis_json or {}).get("source_title") or workflow.title,
                "character_count": len(characters) if isinstance(characters, list) else 0,
            }
        if step == GenericStoryWorkflowStep.SCENE_PLAN_GENERATION:
            pages = (workflow.scene_plan_json or {}).get("pages") or []
            return {
                "title": (workflow.scene_plan_json or {}).get("title") or workflow.title,
                "page_count": len(pages) if isinstance(pages, list) else 0,
                "requested_pages": workflow.requested_pages,
            }
        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            pages = (workflow.story_json or {}).get("pages") or []
            return {
                "title": (workflow.story_json or {}).get("title") or workflow.title,
                "page_count": len(pages) if isinstance(pages, list) else 0,
                "moral": (workflow.story_json or {}).get("moral"),
            }
        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            pages = (workflow.image_plan_json or {}).get("pages") or []
            return {
                "page_prompt_count": len(pages) if isinstance(pages, list) else 0,
                "has_cover_prompt": isinstance((workflow.image_plan_json or {}).get("cover"), dict),
            }
        if step == GenericStoryWorkflowStep.IMAGE_GENERATION:
            return {
                "cover_image_url": (workflow.story_json or {}).get("cover_image_url") or workflow.cover_image,
                "page_image_count": self._story_image_count(workflow.story_json or {}),
                "uses_dummy_images": self._story_uses_dummy_images(workflow.story_json or {}),
            }
        if step == GenericStoryWorkflowStep.NARRATION_GENERATION:
            return {
                "page_audio_count": self._story_audio_count(workflow.story_json or {}),
                "uses_dummy_audio": self._story_uses_dummy_audio(workflow.story_json or {}),
            }
        if step == GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY:
            return {
                "generic_story_id": str(workflow.generic_story_id) if workflow.generic_story_id else None,
                "publish_status": (workflow.input_request or {}).get("status"),
            }
        return {}

    def _step_output(self, workflow: GenericStoryWorkflow, step: GenericStoryWorkflowStep) -> dict[str, Any] | None:
        if step == GenericStoryWorkflowStep.CHARACTER_EXTRACTION:
            return workflow.character_analysis_json
        if step == GenericStoryWorkflowStep.SCENE_PLAN_GENERATION:
            return workflow.scene_plan_json
        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            return workflow.story_json
        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return workflow.image_plan_json
        if step == GenericStoryWorkflowStep.IMAGE_GENERATION:
            story_json = workflow.story_json or {}
            return {
                "visual_bible": (workflow.image_plan_json or {}).get("visual_bible")
                or (workflow.scene_plan_json or {}).get("visual_bible"),
                "final_prompts": self._image_generation_final_prompts(story_json),
                "cover_image_url": story_json.get("cover_image_url") or workflow.cover_image,
                "cover_image_prompt": story_json.get("cover_image_prompt"),
                "cover_planned_image_prompt": story_json.get("cover_planned_image_prompt"),
                "cover_image_dummy": story_json.get("cover_image_dummy"),
                "pages": [
                    {
                        "page_number": page.get("page_number"),
                        "image_url": page.get("image_url"),
                        "image_prompt": page.get("image_prompt"),
                        "planned_image_prompt": page.get("planned_image_prompt"),
                        "image_dummy": page.get("image_dummy"),
                    }
                    for page in story_json.get("pages") or []
                    if isinstance(page, dict)
                ],
            }
        if step == GenericStoryWorkflowStep.NARRATION_GENERATION:
            return self._narration_generation_step_output(
                workflow.story_json or {},
                workflow_language=getattr(workflow, "language", "en"),
            )
        if step == GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY:
            return {
                "generic_story_id": str(workflow.generic_story_id) if workflow.generic_story_id else None,
                "title": workflow.title,
                "cover_image": workflow.cover_image,
            }
        return None

    def _narration_generation_step_output(
        self,
        story_json: dict[str, Any],
        *,
        workflow_language: str,
    ) -> dict[str, Any]:
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        default_language = self._default_story_language(workflow_language)
        output = {
            "pages": self._narration_page_outputs(story_json.get("pages") or []),
        }
        if not isinstance(variants, dict):
            output["languages"] = {
                default_language: {
                    "pages": output["pages"],
                }
            }
            return output

        languages: dict[str, dict[str, Any]] = {}
        for language in SUPPORTED_STORY_LANGUAGES:
            localized_story_json = variants.get(language)
            if not isinstance(localized_story_json, dict):
                continue
            languages[language] = {
                "title": localized_story_json.get("title"),
                "pages": self._narration_page_outputs(localized_story_json.get("pages") or []),
            }
        output["languages"] = languages
        return output

    @staticmethod
    def _narration_page_outputs(pages: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "page_number": page.get("page_number"),
                "text": page.get("text"),
                "audio_url": page.get("audio_url"),
                "audio_dummy": page.get("audio_dummy"),
                "tts_skipped": page.get("tts_skipped"),
                "tts_prompt": page.get("tts_prompt"),
                "duration": page.get("duration"),
            }
            for page in pages
            if isinstance(page, dict)
        ]

    @staticmethod
    def _image_generation_final_prompts(story_json: dict[str, Any]) -> list[dict[str, Any]]:
        prompts: list[dict[str, Any]] = []
        cover_prompt = story_json.get("cover_image_prompt")
        if isinstance(cover_prompt, str) and cover_prompt.strip():
            prompts.append({"page": "cover", "prompt": cover_prompt})

        for page in story_json.get("pages") or []:
            if not isinstance(page, dict):
                continue
            prompt = page.get("image_prompt")
            if isinstance(prompt, str) and prompt.strip():
                prompts.append(
                    {
                        "page": page.get("page_number"),
                        "prompt": prompt,
                    }
                )
        return prompts

    @staticmethod
    def _default_page_count(age_group: str) -> int:
        if age_group == "2-4":
            return 6
        if age_group == "8-12":
            return 12
        return 10

    @staticmethod
    def _story_has_images(story_json: dict[str, Any]) -> bool:
        return bool(story_json.get("cover_image_url")) or GenericStoryWorkflowService._story_image_count(story_json) > 0

    @staticmethod
    def _story_image_count(story_json: dict[str, Any]) -> int:
        return sum(
            1
            for page in story_json.get("pages") or []
            if isinstance(page, dict) and bool(page.get("image_url"))
        )

    @staticmethod
    def _story_uses_dummy_images(story_json: dict[str, Any]) -> bool:
        if story_json.get("cover_image_dummy"):
            return True
        return any(
            isinstance(page, dict) and bool(page.get("image_dummy"))
            for page in story_json.get("pages") or []
        )

    @staticmethod
    def _story_has_audio(story_json: dict[str, Any]) -> bool:
        return GenericStoryWorkflowService._story_audio_count(story_json) > 0

    @staticmethod
    def _story_audio_count(story_json: dict[str, Any]) -> int:
        return sum(
            1
            for page in story_json.get("pages") or []
            if isinstance(page, dict) and bool(page.get("audio_url"))
        )

    @staticmethod
    def _story_uses_dummy_audio(story_json: dict[str, Any]) -> bool:
        return any(
            isinstance(page, dict) and bool(page.get("audio_dummy"))
            for page in story_json.get("pages") or []
        )

    @staticmethod
    def _estimate_reading_time(story_json: dict[str, Any]) -> int:
        word_count = sum(len(str(page.get("text") or "").split()) for page in story_json.get("pages") or [])
        return max(1, round(word_count / 120))

    @staticmethod
    def _character_type(character_analysis: dict[str, Any] | None) -> str | None:
        characters = (character_analysis or {}).get("characters")
        if not isinstance(characters, list):
            return None
        types = sorted({str(character.get("type")) for character in characters if isinstance(character, dict) and character.get("type")})
        return ", ".join(types)[:100] if types else None
