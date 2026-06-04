from __future__ import annotations

from copy import deepcopy
import io
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID
import wave

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.age_groups import page_count_range_for_age_group, validate_age_group
from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.core.illustration_styles import illustration_style_block, normalize_illustration_type
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
    GenericStoryWorkflowListResponse,
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
from app.utils.word_timestamps import generate_word_timestamps

logger = logging.getLogger(__name__)


SUPPORTED_STORY_LANGUAGES = ("en", "hi", "mr")
STORY_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
}
STORY_LANGUAGE_VARIANTS_KEY = "language_variants"
CONTENT_STORY_TOP_LEVEL_EXCLUDED_FIELDS = {
    "cover_image_prompt",
    "cover_planned_image_prompt",
}
CONTENT_STORY_PAGE_EXCLUDED_FIELDS = {
    "image_prompt",
    "planned_image_prompt",
    "tts_prompt",
}
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
        GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION,
        GenericStoryWorkflowStep.STORY_GENERATION,
        GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        GenericStoryWorkflowStep.NARRATION_GENERATION,
        GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
    ]
    DETAIL_STEPS = [
        GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION,
        GenericStoryWorkflowStep.STORY_GENERATION,
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        GenericStoryWorkflowStep.NARRATION_GENERATION,
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
        title = self._validated_unique_workflow_title(payload.title)
        age_group = validate_age_group(payload.age_group)
        await self._ensure_generic_story_title_available(title)
        illustration_style_block(payload.illustration_type)

        input_request = payload.model_dump()
        input_request["title"] = title
        input_request["age_group"] = age_group
        input_request["illustration_type"] = normalize_illustration_type(payload.illustration_type)

        workflow = await self.workflows.create(
            user_id=user_id,
            workflow_name="generic_story",
            actual_story=payload.actual_story,
            age_group=age_group,
            language=payload.language.strip().lower(),
            requested_pages=None,
            status=GenericStoryWorkflowStatus.PENDING.value,
            input_request=input_request,
            title=title,
            theme=payload.theme,
            genre=payload.genre,
            learning_goal=payload.learning_goal,
            ai_provider="google",
            text_model=settings.GOOGLE_TEXT_MODEL,
            image_model=settings.GOOGLE_IMAGE_MODEL,
        )
        await self.session.commit()
        return GenericStoryWorkflowResponse.model_validate(workflow)

    async def _ensure_generic_story_title_available(self, title: str, *, current_story_id: UUID | None = None) -> None:
        existing = await self.generic_stories.get_by_title(title)
        if existing is None or (current_story_id is not None and existing.id == current_story_id):
            return
        raise AppException(
            "A generic story with this title already exists",
            status.HTTP_409_CONFLICT,
            "GENERIC_STORY_TITLE_EXISTS",
        )

    @staticmethod
    def _validated_unique_workflow_title(title: str | None) -> str:
        normalized = str(title or "").strip()
        if not normalized:
            raise AppException(
                "Generic story workflow title is required",
                status.HTTP_400_BAD_REQUEST,
                "GENERIC_STORY_TITLE_REQUIRED",
            )
        return normalized[:255]

    async def get(self, user_id: UUID, workflow_id: UUID) -> GenericStoryWorkflowResponse:
        workflow = await self._get_owned(user_id, workflow_id)
        return GenericStoryWorkflowResponse.model_validate(workflow)

    async def list(
        self,
        user_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> PaginatedResponse[GenericStoryWorkflowListResponse]:
        workflows, total = await self.workflows.list_for_user(user_id, page=page, page_size=page_size)
        return PaginatedResponse[GenericStoryWorkflowListResponse].create(
            items=[GenericStoryWorkflowListResponse.model_validate(workflow) for workflow in workflows],
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
        steps = self.DETAIL_STEPS
        if step_name:
            try:
                requested_step = GenericStoryWorkflowStep(step_name)
            except ValueError as exc:
                raise AppException(
                    f"Invalid generic story workflow step: {step_name}",
                    code="GENERIC_STORY_STEP_INVALID",
                ) from exc
            if requested_step not in self.DETAIL_STEPS:
                raise AppException(
                    f"Generic story workflow step is not exposed by this endpoint: {step_name}",
                    code="GENERIC_STORY_STEP_NOT_EXPOSED",
                )
            steps = [requested_step]
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
        workflow_started_at = perf_counter()

        try:
            workflow.status = GenericStoryWorkflowStatus.IN_PROGRESS.value
            workflow.error_message = None
            await self.workflows.update(workflow)
            await self.session.commit()
            self._log_workflow_event(
                "workflow_started",
                workflow,
                requested_step=payload.step_name,
                step_count=len(steps),
                skip_image_generation=payload.skip_image_generation,
                skip_narration_generation=payload.skip_narration_generation,
            )

            for step in steps:
                step_started_at = perf_counter()
                if step == GenericStoryWorkflowStep.IMAGE_GENERATION and payload.skip_image_generation:
                    workflow.current_step = step.value
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    self._log_workflow_event("step_skipped_started", workflow, step=step, reason="skip_image_generation")
                    self._generate_dummy_images(workflow)
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    self._log_workflow_event(
                        "step_skipped_completed",
                        workflow,
                        step=step,
                        reason="skip_image_generation",
                        duration_ms=self._duration_ms(step_started_at),
                    )
                    continue
                if step == GenericStoryWorkflowStep.NARRATION_GENERATION and payload.skip_narration_generation:
                    workflow.current_step = step.value
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    self._log_workflow_event(
                        "step_skipped_started",
                        workflow,
                        step=step,
                        reason="skip_narration_generation",
                    )
                    self._generate_dummy_narration(workflow)
                    await self.workflows.update(workflow)
                    await self.session.commit()
                    self._log_workflow_event(
                        "step_skipped_completed",
                        workflow,
                        step=step,
                        reason="skip_narration_generation",
                        duration_ms=self._duration_ms(step_started_at),
                    )
                    continue

                workflow.current_step = step.value
                await self.workflows.update(workflow)
                await self.session.commit()
                self._log_workflow_event("step_started", workflow, step=step)
                await self._execute_single_step(workflow, step, public_base_url=public_base_url, payload=payload)
                await self.workflows.update(workflow)
                await self.session.commit()
                self._log_workflow_event(
                    "step_completed",
                    workflow,
                    step=step,
                    duration_ms=self._duration_ms(step_started_at),
                )

            workflow.current_step = None
            if workflow.generic_story_id is not None:
                workflow.status = GenericStoryWorkflowStatus.COMPLETED.value
            await self.workflows.update(workflow)
            await self.session.commit()
            self._log_workflow_event(
                "workflow_completed",
                workflow,
                duration_ms=self._duration_ms(workflow_started_at),
            )
            return GenericStoryWorkflowResponse.model_validate(workflow)

        except Exception as exc:
            workflow.status = GenericStoryWorkflowStatus.FAILED.value
            workflow.error_message = str(exc)
            self._log_workflow_event(
                "workflow_failed",
                workflow,
                level=logging.ERROR,
                error=str(exc),
                duration_ms=self._duration_ms(workflow_started_at),
            )
            logger.exception("Generic story workflow failed: workflow_id=%s step=%s", workflow.id, workflow.current_step)
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
            content.story_json = self._story_json_for_content_table(content_story_json)
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
        page_audio_metadata: dict[int, dict[str, Any]] = {}
        for page_number in page_numbers:
            audio_bytes = await self._read_uploaded_story_audio(audio_uploads[page_number])
            duration = self._uploaded_wav_duration_seconds(audio_bytes)
            page_text = self._story_page_text(content_story_json, page_number)
            page_audio_metadata[page_number] = {
                "duration": round(duration, 2),
                "word_timestamps": generate_word_timestamps(page_text, duration),
            }
            page_audio_urls[page_number] = await audio_storage.save_story_page_audio(
                story_id=workflow.id,
                language=normalized_language,
                page_number=page_number,
                audio_bytes=audio_bytes,
            )

        self._apply_story_audio_urls(
            content_story_json,
            page_audio_urls=page_audio_urls,
            page_audio_metadata=page_audio_metadata,
        )
        content.story_json = self._story_json_for_content_table(content_story_json)
        await self.generic_stories.update_content(content)

        self._apply_workflow_audio_urls(
            workflow,
            page_audio_urls={normalized_language: page_audio_urls},
            page_audio_metadata={normalized_language: page_audio_metadata},
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

        if step == GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION:
            self._require(workflow.character_analysis_json, "Run CHARACTER_EXTRACTION before VISUAL_BIBLE_GENERATION.")
            self._require(workflow.scene_plan_json, "Run SCENE_PLAN_GENERATION before VISUAL_BIBLE_GENERATION.")
            workflow.visual_bible_json = await self._generate_visual_bible(workflow)
            return

        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            self._require(workflow.character_analysis_json, "Run CHARACTER_EXTRACTION before STORY_GENERATION.")
            self._require(workflow.scene_plan_json, "Run SCENE_PLAN_GENERATION before STORY_GENERATION.")
            self._require(workflow.visual_bible_json, "Run VISUAL_BIBLE_GENERATION before STORY_GENERATION.")
            workflow.story_json = await self._generate_story_json(workflow)
            self._apply_workflow_metadata(workflow)
            return

        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            self._require(workflow.scene_plan_json, "Run SCENE_PLAN_GENERATION before IMAGE_PLAN_GENERATION.")
            self._require(workflow.visual_bible_json, "Run VISUAL_BIBLE_GENERATION before IMAGE_PLAN_GENERATION.")
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
                "title": workflow.title or "",
                "actual_story": workflow.actual_story,
                "character_analysis_json": _compact_json(workflow.character_analysis_json),
            },
        )
        plan = await self._generate_json(prompt, max_tokens=12000)
        min_pages, max_pages = self._scene_plan_page_count_range(workflow.age_group)
        pages = plan.get("pages")
        page_count = len(pages) if isinstance(pages, list) else 0
        if not isinstance(pages, list) or page_count < min_pages or page_count > max_pages:
            raise AppException(
                f"Scene plan returned {page_count} pages; expected {min_pages}-{max_pages}",
                code="GENERIC_SCENE_PLAN_PAGE_COUNT_MISMATCH",
            )
        return plan

    async def _generate_visual_bible(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        if settings.STORY_MOCK_LLM_RESPONSES:
            return self._mock_visual_bible(workflow)
        prompt = load_and_render_prompt(
            "prompts/generic_story/visual_bible_generator_prompt.txt",
            {
                "title": workflow.title or "",
                "actual_story": workflow.actual_story,
                "character_analysis_json": _compact_json(workflow.character_analysis_json),
                "scene_plan_json": _compact_json(workflow.scene_plan_json),
                "illustration_style": self._workflow_illustration_style(workflow),
            },
        )
        visual_bible = await self._generate_json(prompt, max_tokens=12000)
        visual_bible["style"] = self._workflow_illustration_style(workflow)
        visual_bible["age_group"] = workflow.age_group
        characters = visual_bible.get("characters")
        if not isinstance(characters, list) or not characters:
            raise AppException("Visual bible must include characters.", code="GENERIC_VISUAL_BIBLE_CHARACTERS_MISSING")
        return visual_bible

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
                "visual_bible_json": _compact_json(self._workflow_visual_bible(workflow)),
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
                "visual_bible_json": _compact_json(self._workflow_visual_bible(workflow)),
                "story_json": _compact_json(self._story_json_for_image_plan_prompt(workflow.story_json or {})),
                "illustration_style": self._workflow_illustration_style(workflow),
            },
        )
        image_plan = await self._generate_json(prompt, max_tokens=16000)
        image_plan["style"] = self._workflow_illustration_style(workflow)
        pages = image_plan.get("pages")
        story_pages = (workflow.story_json or {}).get("pages") or []
        if not isinstance(pages, list) or len(pages) != len(story_pages):
            raise AppException("Image plan page count must match story JSON pages.", code="GENERIC_IMAGE_PLAN_PAGE_COUNT_MISMATCH")
        self._validate_and_normalize_image_cover_plan(image_plan, workflow)
        return image_plan

    async def _generate_images(self, workflow: GenericStoryWorkflow, *, public_base_url: str) -> None:
        image_storage = get_image_storage_service()
        story_json = workflow.story_json or {}
        image_plan = workflow.image_plan_json or {}
        visual_bible = self._workflow_visual_bible(workflow)
        story_title = story_json.get("title") or workflow.title or "Untitled Story"

        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        if cover_plan:
            image_started_at = perf_counter()
            self._log_workflow_event("image_generation_cover_started", workflow, step=GenericStoryWorkflowStep.IMAGE_GENERATION)
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
            story_json["cover_planned_image_prompt"] = self._image_plan_summary(cover_plan)
            self._log_workflow_event(
                "image_generation_cover_completed",
                workflow,
                step=GenericStoryWorkflowStep.IMAGE_GENERATION,
                duration_ms=self._duration_ms(image_started_at),
            )

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
            page_number = self._image_plan_page_number(plan_page)
            if page_number is None:
                continue
            image_started_at = perf_counter()
            self._log_workflow_event(
                "image_generation_page_started",
                workflow,
                step=GenericStoryWorkflowStep.IMAGE_GENERATION,
                page_number=page_number,
            )
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
                story_page["planned_image_prompt"] = self._image_plan_summary(plan_page)
            self._log_workflow_event(
                "image_generation_page_completed",
                workflow,
                step=GenericStoryWorkflowStep.IMAGE_GENERATION,
                page_number=page_number,
                duration_ms=self._duration_ms(image_started_at),
            )

        workflow.story_json = story_json

    def _generate_dummy_images(self, workflow: GenericStoryWorkflow) -> None:
        """Attach generated image prompts and dummy image URLs without image LLM/storage calls."""
        story_json = workflow.story_json or {}
        image_plan = workflow.image_plan_json or {}
        visual_bible = self._workflow_visual_bible(workflow)
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
            story_json["cover_planned_image_prompt"] = self._image_plan_summary(cover_plan)
            story_json["cover_image_dummy"] = True

        plan_pages = image_plan.get("pages") if isinstance(image_plan.get("pages"), list) else []
        prompts_by_page = {
            self._image_plan_page_number(page): page
            for page in plan_pages
            if isinstance(page, dict) and self._image_plan_page_number(page) is not None
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
                page["planned_image_prompt"] = self._image_plan_summary(plan_page)

        workflow.story_json = story_json

    async def _generate_google_narration(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        story_json = dict(workflow.story_json or {})
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        if not isinstance(variants, dict):
            narration_started_at = perf_counter()
            self._log_workflow_event(
                "narration_language_started",
                workflow,
                step=GenericStoryWorkflowStep.NARRATION_GENERATION,
                language=workflow.language,
            )
            narrated_story_json = await StoryNarrationService(self.session).generate_story_json_narration(
                story_json,
                story_id=workflow.id,
                language=workflow.language,
                overwrite=False,
                source="generic_story_workflow",
                age_group=workflow.age_group,
            )
            self._log_workflow_event(
                "narration_language_completed",
                workflow,
                step=GenericStoryWorkflowStep.NARRATION_GENERATION,
                language=workflow.language,
                duration_ms=self._duration_ms(narration_started_at),
            )
            return narrated_story_json

        narration_service = StoryNarrationService(self.session)
        workflow_language = self._default_story_language(workflow.language)
        narrated_variants: dict[str, dict[str, Any]] = {}
        default_story_json: dict[str, Any] | None = None

        for language in SUPPORTED_STORY_LANGUAGES:
            if not isinstance(variants.get(language), dict):
                continue
            narration_started_at = perf_counter()
            self._log_workflow_event(
                "narration_language_started",
                workflow,
                step=GenericStoryWorkflowStep.NARRATION_GENERATION,
                language=language,
            )
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
            self._log_workflow_event(
                "narration_language_completed",
                workflow,
                step=GenericStoryWorkflowStep.NARRATION_GENERATION,
                language=language,
                duration_ms=self._duration_ms(narration_started_at),
            )

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

        if workflow.generic_story_id is not None:
            generic_story = await self.generic_stories.get_by_id(workflow.generic_story_id)
            if generic_story is None:
                raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")
            await self._ensure_generic_story_title_available(title, current_story_id=generic_story.id)
        else:
            await self._ensure_generic_story_title_available(title)
            generic_story = await self.generic_stories.create(**data)

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
        self._log_workflow_event(
            "generic_story_published",
            workflow,
            step=GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
            publish_status=data["status"],
        )

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
        rendered_plan = self._image_plan_for_render(page_type, story_title, page_image_plan)
        return load_and_render_prompt(
            "prompts/generic_story/image_generation_prompt.txt",
            {
                "page_type": page_type,
                "aspect_ratio": self._image_prompt_aspect_ratio(page_type),
                "story_title": story_title,
                "age_group": self._image_prompt_age_group(visual_bible),
                "illustration_style": self._image_prompt_illustration_style(visual_bible),
                "title_instruction": self._image_title_instruction(page_type, story_title),
                "scene_instruction": self._image_scene_instruction(page_type, rendered_plan),
                "visual_context": self._image_visual_context(visual_bible, page_image_plan),
                "page_image_plan_json": _compact_json(rendered_plan),
            },
        )

    @staticmethod
    def _image_plan_for_render(
        page_type: str,
        story_title: str,
        page_image_plan: dict[str, Any],
    ) -> dict[str, Any]:
        rendered_plan = dict(page_image_plan)
        if page_type == "cover" and story_title:
            rendered_plan["title_text"] = story_title
        return rendered_plan

    @classmethod
    def _image_scene_instruction(cls, page_type: str, page_image_plan: dict[str, Any]) -> str:
        lines: list[str] = []

        if page_type == "cover":
            lines.append(
                "This is a finished front book cover based on the whole story, not an interior page illustration."
            )
            cover_direction = cls._image_scene_value(page_image_plan.get("book_cover_prompt"))
            if cover_direction:
                lines.append(f"Overall cover direction: {cover_direction}")
            genre_signal = cls._image_scene_value(page_image_plan.get("genre_signal"))
            if genre_signal:
                lines.append(f"Story promise and genre signal: {genre_signal}")
            title_layout = cls._image_scene_value(page_image_plan.get("title_layout"))
            if title_layout:
                lines.append(f"Required title layout: {title_layout}")
            lines.append(
                "Do not copy page 1 or any interior page composition; use a cover-style hierarchy with title first, story world second."
            )

        if page_type == "cover" and str(page_image_plan.get("title_text") or "").strip():
            lines.append(f"Required cover title: {str(page_image_plan.get('title_text')).strip()}")

        for label, key in (
            ("Visual focus", "visual_focus"),
            ("Action and composition", "composition"),
            ("Environment", "environment"),
            ("Emotion", "emotion"),
            ("Camera shot", "camera_shot"),
        ):
            text = cls._image_scene_value(page_image_plan.get(key))
            if text:
                lines.append(f"{label}: {text}")

        characters = cls._image_scene_value(page_image_plan.get("characters"))
        if characters:
            lines.append(f"Allowed characters only: {characters}")

        objects = cls._image_scene_value(page_image_plan.get("important_objects"))
        if objects:
            lines.append(f"Required objects only: {objects}")

        if lines:
            return "\n".join(lines)
        return cls._image_plan_summary(page_image_plan) or "Use the IMAGE PLAN as the exact scene contract."

    @staticmethod
    def _image_scene_value(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, dict):
            return _compact_json(value)
        return str(value or "").strip()

    def _validate_and_normalize_image_cover_plan(
        self,
        image_plan: dict[str, Any],
        workflow: GenericStoryWorkflow,
    ) -> None:
        cover = image_plan.get("cover")
        if not isinstance(cover, dict):
            raise AppException("Image plan must include a cover plan.", code="GENERIC_IMAGE_PLAN_COVER_MISSING")

        story_title = self._image_plan_story_title(workflow)
        cover["title_text"] = story_title

        missing_fields = [
            field
            for field in ("visual_focus", "composition", "camera_shot", "title_text")
            if not str(cover.get(field) or "").strip()
        ]
        if missing_fields:
            raise AppException(
                "Image plan cover is missing required book-cover fields.",
                code="GENERIC_IMAGE_PLAN_COVER_INCOMPLETE",
                details={"missing_fields": missing_fields},
            )

        title_context = " ".join(
            str(cover.get(field) or "")
            for field in ("composition", "title_layout", "book_cover_prompt")
        ).lower()
        if not any(keyword in title_context for keyword in ("title", "typography", "text", "letter")):
            raise AppException(
                "Image plan cover must describe title placement/readability.",
                code="GENERIC_IMAGE_PLAN_COVER_TITLE_LAYOUT_MISSING",
            )

    @staticmethod
    def _image_plan_story_title(workflow: GenericStoryWorkflow) -> str:
        story_json = getattr(workflow, "story_json", None)
        if isinstance(story_json, dict) and str(story_json.get("title") or "").strip():
            return str(story_json["title"]).strip()
        if str(getattr(workflow, "title", "") or "").strip():
            return str(workflow.title).strip()
        scene_plan_json = getattr(workflow, "scene_plan_json", None)
        if isinstance(scene_plan_json, dict) and str(scene_plan_json.get("title") or "").strip():
            return str(scene_plan_json["title"]).strip()
        return "Untitled Story"

    @staticmethod
    def _workflow_illustration_type(workflow: GenericStoryWorkflow) -> str:
        input_request = getattr(workflow, "input_request", None)
        if isinstance(input_request, dict):
            return normalize_illustration_type(input_request.get("illustration_type"))
        return normalize_illustration_type(None)

    @classmethod
    def _workflow_illustration_style(cls, workflow: GenericStoryWorkflow) -> str:
        return illustration_style_block(cls._workflow_illustration_type(workflow))

    @staticmethod
    def _image_prompt_illustration_style(visual_bible: dict[str, Any]) -> str:
        style = str((visual_bible or {}).get("style") or "").strip()
        return style or illustration_style_block(None)

    @staticmethod
    def _image_prompt_age_group(visual_bible: dict[str, Any]) -> str:
        age_group = str((visual_bible or {}).get("age_group") or "").strip()
        return age_group or "children"

    @staticmethod
    def _image_prompt_aspect_ratio(page_type: str) -> str:
        if page_type == "cover":
            return settings.STORY_COVER_ASPECT_RATIO
        return settings.STORY_PAGE_ASPECT_RATIO

    @staticmethod
    def _image_title_instruction(page_type: str, story_title: str) -> str:
        if page_type != "cover":
            return (
                "This is an interior story page. Do not render any written text, letters, captions, signs, "
                "labels, logos, or typography."
            )
        title = str(story_title or "").strip() or "Untitled Story"
        return (
            "COVER TITLE CONTRACT:\n"
            "This is a finished front book cover, not an interior page.\n"
            f"Render this exact visible title text: \"{title}\"\n"
            "The title must be large, centered or top-centered, fully readable, correctly spelled, and unobstructed.\n"
            "Use clean storybook cover typography integrated directly into the artwork.\n"
            "Leave a calm clear title area with simple background behind the letters.\n"
            "Do not use a banner, label, card, sticker, plaque, black rectangle, UI panel, watermark, subtitle, "
            "extra words, alternate spelling, or decorative text.\n"
            "Do not let characters, trees, objects, clouds, hands, or decorations overlap the title."
        )

    def _image_visual_context(self, visual_bible: dict[str, Any], page_image_plan: dict[str, Any]) -> str:
        """Build a compact page-scoped model sheet for image generation."""
        if not isinstance(visual_bible, dict):
            return ""

        character_refs = page_image_plan.get("characters") if isinstance(page_image_plan.get("characters"), list) else []
        object_refs = (
            page_image_plan.get("important_objects")
            if isinstance(page_image_plan.get("important_objects"), list)
            else []
        )
        scene_text = " ".join(
            str(page_image_plan.get(field) or "")
            for field in ("environment", "visual_focus", "composition", "continuity_notes")
        )

        characters = self._matching_character_visual_bible_items(
            visual_bible.get("characters"),
            character_refs,
            fallback_all=not bool(character_refs),
        )
        objects = self._matching_visual_bible_items(visual_bible.get("important_objects"), object_refs, fallback_all=False)
        locations = self._matching_visual_bible_items(
            visual_bible.get("locations"),
            [scene_text],
            fallback_all=True,
        )

        lines: list[str] = []
        style = str(visual_bible.get("style") or "").strip()
        if style:
            lines.append(f"STYLE: {style}")

        if characters:
            lines.append("CHARACTER MODEL SHEET:")
            for character in characters:
                lines.append(f"- {self._character_visual_context(character)}")

        if locations:
            lines.append("LOCATION CONTINUITY:")
            for location in locations:
                name = str(location.get("name") or "").strip()
                identity = str(location.get("visual_identity") or location.get("description") or "").strip()
                if name or identity:
                    lines.append(f"- {name}: {identity}".strip())

        if objects:
            lines.append("OBJECT CONTINUITY:")
            for obj in objects:
                name = str(obj.get("name") or "").strip()
                description = str(obj.get("description") or "").strip()
                requirements = self._join_text_list(obj.get("continuity_requirements"), limit=3)
                details = "; ".join(part for part in (description, f"keep {requirements}" if requirements else "") if part)
                if name or details:
                    lines.append(f"- {name}: {details}".strip())

        return "\n".join(lines)

    @classmethod
    def _matching_visual_bible_items(
        cls,
        items: Any,
        references: list[Any],
        *,
        fallback_all: bool,
    ) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        dict_items = [item for item in items if isinstance(item, dict)]
        normalized_refs = [cls._normalize_visual_ref(ref) for ref in references if cls._normalize_visual_ref(ref)]
        if not normalized_refs:
            return dict_items if fallback_all else []

        matches = [
            item
            for item in dict_items
            if any(cls._visual_item_matches_ref(item, ref) for ref in normalized_refs)
        ]
        return matches or (dict_items if fallback_all else [])

    @classmethod
    def _matching_character_visual_bible_items(
        cls,
        items: Any,
        references: list[Any],
        *,
        fallback_all: bool,
    ) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        dict_items = [item for item in items if isinstance(item, dict)]
        normalized_refs = [cls._normalize_visual_ref(ref) for ref in references if cls._normalize_visual_ref(ref)]
        if not normalized_refs:
            return dict_items if fallback_all else []

        matches = [
            item
            for item in dict_items
            if any(cls._character_item_matches_ref(item, ref) for ref in normalized_refs)
        ]
        return matches or (dict_items if fallback_all else [])

    @classmethod
    def _character_item_matches_ref(cls, item: dict[str, Any], normalized_ref: str) -> bool:
        for candidate in (item.get("name"), item.get("anchor")):
            normalized_candidate = cls._normalize_visual_ref(candidate)
            if normalized_candidate and (
                normalized_candidate in normalized_ref or normalized_ref in normalized_candidate
            ):
                return True
        return False

    @classmethod
    def _visual_item_matches_ref(cls, item: dict[str, Any], normalized_ref: str) -> bool:
        candidates = [
            item.get("name"),
            item.get("anchor"),
            item.get("description"),
            item.get("visual_identity"),
        ]
        for candidate in candidates:
            normalized_candidate = cls._normalize_visual_ref(candidate)
            if normalized_candidate and (
                normalized_candidate in normalized_ref or normalized_ref in normalized_candidate
            ):
                return True
        return False

    @staticmethod
    def _normalize_visual_ref(value: Any) -> str:
        return "".join(char for char in str(value or "").lower() if char.isalnum())

    @classmethod
    def _character_visual_context(cls, character: dict[str, Any]) -> str:
        name = str(character.get("name") or "").strip()
        role = str(character.get("role") or "").strip()
        anchor = str(character.get("anchor") or "").strip()
        raw_appearance = character.get("appearance")
        appearance_text = str(raw_appearance or "").strip() if not isinstance(raw_appearance, dict) else ""
        appearance = raw_appearance if isinstance(raw_appearance, dict) else {}
        locks = character.get("locks") if isinstance(character.get("locks"), dict) else {}
        hair = appearance.get("hair") if isinstance(appearance.get("hair"), dict) else {}
        outfit = appearance.get("outfit") if isinstance(appearance.get("outfit"), dict) else {}
        parts = [
            name,
            f"role={role}" if role else "",
            anchor,
            appearance_text,
            f"face={locks.get('face_lock') or appearance.get('face_shape') or ''}".strip(),
            f"hair={locks.get('hair_lock') or cls._join_mapping_values(hair)}".strip(),
            f"outfit={locks.get('outfit_lock') or cls._join_mapping_values(outfit)}".strip(),
            f"accessory={locks.get('accessory_lock') or cls._join_text_list(appearance.get('accessories'), limit=4)}".strip(),
            f"feature={appearance.get('distinctive_feature') or ''}".strip(),
            f"scale={character.get('size_relative_to_hero') or ''}".strip(),
            f"forbid={cls._join_text_list(character.get('forbidden_variations'), limit=6)}".strip(),
        ]
        return "; ".join(part for part in parts if part and not part.endswith("="))

    @staticmethod
    def _join_mapping_values(value: dict[str, Any]) -> str:
        return ", ".join(str(item).strip() for item in value.values() if str(item or "").strip())

    @staticmethod
    def _join_text_list(value: Any, *, limit: int) -> str:
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value[:limit] if str(item or "").strip())
        return str(value or "").strip()

    @staticmethod
    def _workflow_visual_bible(workflow: GenericStoryWorkflow) -> dict[str, Any]:
        visual_bible_json = getattr(workflow, "visual_bible_json", None)
        if isinstance(visual_bible_json, dict):
            return visual_bible_json
        image_plan_json = getattr(workflow, "image_plan_json", None)
        image_plan = image_plan_json if isinstance(image_plan_json, dict) else {}
        if isinstance(image_plan.get("visual_bible"), dict):
            return image_plan["visual_bible"]
        scene_plan_json = getattr(workflow, "scene_plan_json", None)
        scene_plan = scene_plan_json if isinstance(scene_plan_json, dict) else {}
        if isinstance(scene_plan.get("visual_bible"), dict):
            return scene_plan["visual_bible"]
        return {}

    @staticmethod
    def _image_plan_page_number(page_plan: dict[str, Any]) -> int | None:
        raw_page_number = page_plan.get("page_number", page_plan.get("page"))
        try:
            page_number = int(raw_page_number)
        except (TypeError, ValueError):
            return None
        return page_number if page_number > 0 else None

    @staticmethod
    def _image_plan_summary(page_plan: dict[str, Any]) -> str:
        if isinstance(page_plan.get("image_prompt"), str) and page_plan["image_prompt"].strip():
            return page_plan["image_prompt"].strip()
        return _compact_json(page_plan)

    @staticmethod
    def _scene_page_number(scene_page: dict[str, Any], fallback: int) -> int:
        raw_page_number = scene_page.get("page_number", scene_page.get("page", fallback))
        try:
            page_number = int(raw_page_number)
        except (TypeError, ValueError):
            return fallback
        return page_number if page_number > 0 else fallback

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
            fallback=(workflow.scene_plan_json or {}).get("moral")
            or (workflow.scene_plan_json or {}).get("moral_explanation")
            or "",
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

    def _story_json_for_image_plan_prompt(self, story_json: dict[str, Any]) -> dict[str, Any]:
        source = self._story_json_without_language_variants(story_json)
        compact_story = {
            key: source[key]
            for key in ("title", "summary", "moral")
            if source.get(key) is not None
        }
        pages = source.get("pages") if isinstance(source.get("pages"), list) else []
        compact_pages: list[dict[str, Any]] = []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            compact_page = {
                key: page[key]
                for key in ("page_number", "emotion", "text")
                if page.get(key) is not None
            }
            if "page_number" not in compact_page:
                compact_page["page_number"] = index
            narration = page.get("narration") if isinstance(page.get("narration"), dict) else None
            if narration:
                compact_page["narration"] = {
                    key: narration[key]
                    for key in ("tone", "pace", "voice_style")
                    if narration.get(key) is not None
                }
            compact_pages.append(compact_page)
        compact_story["pages"] = compact_pages
        return compact_story

    @staticmethod
    def _story_json_for_content_table(story_json: dict[str, Any]) -> dict[str, Any]:
        cleaned = deepcopy(story_json)
        cleaned.pop(STORY_LANGUAGE_VARIANTS_KEY, None)
        for field in CONTENT_STORY_TOP_LEVEL_EXCLUDED_FIELDS:
            cleaned.pop(field, None)
        pages = cleaned.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict):
                    continue
                for field in CONTENT_STORY_PAGE_EXCLUDED_FIELDS:
                    page.pop(field, None)
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
                    "story_json": self._story_json_for_content_table(story_json),
                }
            ]

        default_language = self._default_story_language(workflow_language)
        return [
            {
                "language": language,
                "story_json": self._story_json_for_content_table(
                    self._story_json_for_language(
                        story_json,
                        variants,
                        language=language,
                        workflow_language=default_language,
                    )
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
    def _story_page_text(story_json: dict[str, Any], page_number: int) -> str:
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            raw_page_number = page.get("page_number", index)
            try:
                current_page_number = int(raw_page_number)
            except (TypeError, ValueError):
                continue
            if current_page_number == page_number:
                return str(page.get("text") or "").strip()
        return ""

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
    def _uploaded_wav_duration_seconds(audio_bytes: bytes) -> float:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
        except (EOFError, wave.Error) as exc:
            raise AppException(
                "Audio must be a valid WAV file",
                status.HTTP_400_BAD_REQUEST,
                "INVALID_AUDIO_WAV",
            ) from exc

        if frame_rate <= 0 or frame_count <= 0:
            raise AppException(
                "Audio WAV file has no duration",
                status.HTTP_400_BAD_REQUEST,
                "INVALID_AUDIO_DURATION",
            )
        return frame_count / frame_rate

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
        page_audio_metadata: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        metadata_by_page = page_audio_metadata or {}
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
            metadata = metadata_by_page.get(page_number)
            if isinstance(metadata, dict):
                if isinstance(metadata.get("duration"), (int, float)):
                    page["duration"] = round(float(metadata["duration"]), 2)
                timestamps = metadata.get("word_timestamps")
                if isinstance(timestamps, list):
                    page["word_timestamps"] = timestamps

    def _apply_workflow_audio_urls(
        self,
        workflow: GenericStoryWorkflow,
        *,
        page_audio_urls: dict[str, dict[int, str]],
        page_audio_metadata: dict[str, dict[int, dict[str, Any]]] | None = None,
        workflow_language: str,
    ) -> None:
        metadata_by_language = page_audio_metadata or {}
        story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        self._apply_story_audio_urls(
            story_json,
            page_audio_urls=page_audio_urls.get(workflow_language, {}),
            page_audio_metadata=metadata_by_language.get(workflow_language, {}),
        )
        variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
        if isinstance(variants, dict):
            for language, language_story_json in variants.items():
                if not isinstance(language_story_json, dict):
                    continue
                self._apply_story_audio_urls(
                    language_story_json,
                    page_audio_urls=page_audio_urls.get(str(language).strip().lower(), {}),
                    page_audio_metadata=metadata_by_language.get(str(language).strip().lower(), {}),
                )
        workflow.story_json = story_json

    def _mock_character_analysis(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        return {
            "title": workflow.title or "The Helpful Adventure",
            "summary": "A hero notices a problem, helps carefully, and learns the original lesson.",
            "theme": workflow.theme or "kindness",
            "genre": workflow.genre or "adventure",
            "goal": workflow.learning_goal or "helping others",
            "moral": "Small helpful actions can make a big difference.",
            "setting": "a bright storybook village",
            "conflict": "Something important is not working and the hero must help without giving up.",
            "ending": "The hero learns that careful, kind effort matters.",
            "chars": [
                {
                    "name": "Mira",
                    "role": "hero",
                    "type": "human",
                    "anchor": "A kind child hero with a yellow scarf.",
                    "look": {"age": "child", "hair": "short dark hair", "eyes": "warm brown", "skin": "", "outfit": "blue tunic, yellow scarf, red shoes", "features": ["yellow scarf"]},
                    "traits": ["curious", "kind", "persistent"],
                    "relation": "self",
                    "pages": [],
                    "lock": ["yellow scarf", "blue tunic", "red shoes"],
                },
                {
                    "name": "Luma",
                    "role": "companion",
                    "type": "animal",
                    "anchor": "A tiny silver owl companion with a green ribbon.",
                    "look": {"age": "", "hair": "", "eyes": "round amber", "skin": "", "outfit": "green ribbon", "features": ["speckled wings"]},
                    "traits": ["gentle", "encouraging"],
                    "relation": "companion",
                    "pages": [],
                    "lock": ["silver feathers", "green ribbon"],
                },
            ],
            "rules": ["Mira and Luma keep the same identity on every page."],
            "preserve": ["Preserve the original problem, helping action, and moral."],
        }

    def _mock_scene_plan(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        page_count = self._default_page_count(workflow.age_group)
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
                    "page": page_number,
                    "story_role": role,
                    "scene_summary": f"Mira and Luma follow the source story moment {page_number}.",
                    "location": "storybook village",
                    "characters": ["Mira", "Luma"],
                    "main_action": "Mira helps carefully while Luma stays beside her.",
                    "emotion": "wonder" if page_number == 1 else "determination",
                    "visual_focus": "Mira and Luma acting out the source story moment.",
                    "source_connection": "This page preserves the original story sequence.",
                    "page_turn_hook": "The next clue waits ahead.",
                    "continuity": {
                        "characters": ["Mira and Luma keep their identities."],
                        "objects": ["helpful clue remains important"],
                        "location_state": ["storybook village stays bright"],
                    },
                }
            )
        return {
            "title": workflow.title or "The Helpful Adventure",
            "summary": "Mira follows the original story and learns that helpful actions matter.",
            "theme": workflow.theme or "kindness",
            "genre": workflow.genre or "adventure",
            "goal": workflow.learning_goal or "helping others",
            "moral": "Small helpful actions can make a big difference.",
            "setting": "a bright storybook village",
            "tone": "warm and adventurous",
            "cover_brief": {
                "book_cover_goal": "Present the story as a warm adventure about helpful effort.",
                "title_text": workflow.title or "The Helpful Adventure",
                "cover_moment": "Mira and Luma stand together in the bright village with the helpful clue.",
                "hero_focus": "Mira is the clear hero with Luma beside her.",
                "supporting_elements": ["storybook village", "helpful clue"],
                "title_area": "Clean open sky area at the top for large readable title typography.",
                "genre_signal": "warm adventure storybook",
                "emotional_promise": "curiosity, kindness, and gentle courage",
            },
            "preserve": {
                "conflict": "The original problem is preserved.",
                "must_keep": ["original meaning", "original moral", "original ending"],
                "do_not_add": ["random new characters", "new moral", "changed conflict"],
            },
            "pages": pages,
        }

    def _mock_visual_bible(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        return {
            "style": self._workflow_illustration_style(workflow),
            "age_group": workflow.age_group,
            "characters": [
                {
                    "name": "Mira",
                    "role": "hero",
                    "anchor": "A kind child hero with a yellow scarf.",
                    "appearance": {
                        "age": "young child",
                        "height_build": "average child height, slim build",
                        "skin_tone": "warm medium skin tone",
                        "face_shape": "round face",
                        "eye_shape": "large round eyes",
                        "eye_color": "warm brown",
                        "hair": {"color": "dark brown", "length": "short", "style": "neatly combed bob"},
                        "outfit": {"type": "tunic", "primary_color": "blue", "secondary_color": "yellow scarf", "pattern": "plain fabric"},
                        "footwear": "red shoes",
                        "accessories": ["yellow scarf always present"],
                        "distinctive_feature": "yellow scarf",
                    },
                    "locks": {
                        "face_lock": "round face with warm brown eyes",
                        "hair_lock": "short dark brown neatly combed bob",
                        "outfit_lock": "blue tunic, yellow scarf, and red shoes",
                        "accessory_lock": "yellow scarf always present",
                    },
                    "forbidden_variations": ["different scarf color", "different outfit", "long hair"],
                    "size_relative_to_hero": "hero",
                },
                {
                    "name": "Luma",
                    "role": "companion",
                    "anchor": "A tiny silver owl companion with a green ribbon.",
                    "appearance": {
                        "age": "",
                        "height_build": "tiny beside Mira",
                        "skin_tone": "",
                        "face_shape": "round owl face",
                        "eye_shape": "round eyes",
                        "eye_color": "amber",
                        "hair": {"color": "", "length": "", "style": "soft silver feathers"},
                        "outfit": {"type": "ribbon", "primary_color": "green", "secondary_color": "", "pattern": "plain"},
                        "footwear": "",
                        "accessories": ["green ribbon always present"],
                        "distinctive_feature": "green ribbon",
                    },
                    "locks": {
                        "face_lock": "round owl face with amber eyes",
                        "hair_lock": "soft silver feathers",
                        "outfit_lock": "green ribbon",
                        "accessory_lock": "green ribbon always present",
                    },
                    "forbidden_variations": ["different ribbon color", "different species", "missing ribbon"],
                    "size_relative_to_hero": "tiny beside Mira",
                },
            ],
            "locations": [{"name": "storybook village", "description": "bright friendly village", "visual_identity": "warm colorful homes and sunny paths"}],
            "important_objects": [{"name": "helpful clue", "description": "small story clue", "continuity_requirements": ["keep recognizable when shown"]}],
        }

    def _mock_story_json(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        pages = []
        for page in (workflow.scene_plan_json or {}).get("pages") or []:
            page_number = self._scene_page_number(page, len(pages) + 1)
            pages.append(
                {
                    "page_number": page_number,
                    "emotion": page.get("emotion") or page.get("emotional_beat") or "wonder",
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
                "en": (workflow.scene_plan_json or {}).get("moral") or (workflow.scene_plan_json or {}).get("moral_explanation") or "Kindness matters.",
                "hi": "दयालुता मायने रखती है.",
                "mr": "दयाळूपणा महत्त्वाचा असतो.",
            },
        }

    def _mock_image_plan(self, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        story_json = workflow.story_json or {}
        pages = []
        for page in story_json.get("pages") or []:
            page_number = page.get("page_number", len(pages) + 1)
            pages.append(
                {
                    "page": page_number,
                    "story_role": "build",
                    "visual_focus": "Mira and Luma act out the planned source-story moment.",
                    "camera_shot": "medium shot",
                    "composition": "Mira centered with Luma beside her, clear action, no extra characters.",
                    "emotion": page.get("emotion") or "wonder",
                    "environment": "bright storybook village",
                    "characters": ["Mira", "Luma"],
                    "important_objects": ["helpful clue"],
                    "continuity_notes": [
                        "Mira keeps blue tunic, yellow scarf, red shoes, short dark bob.",
                        "Luma remains a tiny silver owl with a green ribbon.",
                    ],
                }
            )
        return {
            "style": self._workflow_illustration_style(workflow),
            "cover": {
                "title_text": story_json.get("title") or "The Helpful Adventure",
                "book_cover_prompt": (
                    "Front book cover showing Mira and Luma in the storybook village with clean title space "
                    f"for \"{story_json.get('title') or 'The Helpful Adventure'}\"."
                ),
                "visual_focus": "Mira and Luma in the storybook village",
                "emotion": "wonder",
                "camera_shot": "wide shot",
                "composition": "Clean cover composition with title area, Mira and Luma clearly visible.",
                "title_layout": "Large readable title at the top over a calm clean sky area.",
                "genre_signal": "warm adventure storybook",
                "characters": ["Mira", "Luma"],
                "important_objects": ["helpful clue"],
                "continuity_notes": [
                    "Mira keeps blue tunic, yellow scarf, red shoes, short dark bob.",
                    "Luma remains a tiny silver owl with a green ribbon.",
                ],
            },
            "pages": pages,
        }

    def _apply_workflow_metadata(self, workflow: GenericStoryWorkflow) -> None:
        character = workflow.character_analysis_json or {}
        scene_plan = workflow.scene_plan_json or {}
        story_json = workflow.story_json or {}
        workflow.title = str(story_json.get("title") or scene_plan.get("title") or character.get("title") or character.get("source_title") or "")[:255] or None
        workflow.summary = str(story_json.get("summary") or scene_plan.get("summary") or character.get("summary") or "") or None
        workflow.theme = str(scene_plan.get("theme") or character.get("theme") or workflow.theme or "")[:100] or None
        workflow.genre = str(scene_plan.get("genre") or character.get("genre") or workflow.genre or "")[:100] or None
        workflow.learning_goal = str(scene_plan.get("goal") or scene_plan.get("learning_goal") or character.get("goal") or character.get("learning_goal") or workflow.learning_goal or "")[:500] or None
        workflow.moral = str(story_json.get("moral") or scene_plan.get("moral") or scene_plan.get("moral_explanation") or character.get("moral") or "")[:255] or None

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

    def _log_workflow_event(
        self,
        event: str,
        workflow: GenericStoryWorkflow,
        *,
        step: GenericStoryWorkflowStep | str | None = None,
        level: int = logging.INFO,
        **details: Any,
    ) -> None:
        step_name = self._log_step_name(step) or getattr(workflow, "current_step", None)
        fields = {
            "event": event,
            "workflow_id": getattr(workflow, "id", None),
            "generic_story_id": getattr(workflow, "generic_story_id", None),
            "step": step_name,
            "status": getattr(workflow, "status", None),
        }
        fields.update({key: value for key, value in details.items() if value is not None})
        message = "generic_story_workflow " + " ".join(
            f"{key}={self._log_field_value(value)}"
            for key, value in fields.items()
        )
        logger.log(level, message)

    @staticmethod
    def _log_step_name(step: GenericStoryWorkflowStep | str | None) -> str | None:
        if step is None:
            return None
        if isinstance(step, GenericStoryWorkflowStep):
            return step.value
        return str(step)

    @staticmethod
    def _log_field_value(value: Any) -> str:
        if value is None:
            return "none"
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).replace("\n", "\\n")
        if not text:
            return '""'
        if any(char.isspace() for char in text) or "=" in text:
            return json.dumps(text, ensure_ascii=False)
        return text

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)

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
        if step == GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION:
            return bool(self._workflow_visual_bible(workflow))
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
            characters = (workflow.character_analysis_json or {}).get("chars") or (workflow.character_analysis_json or {}).get("characters") or []
            return {
                "title": (workflow.character_analysis_json or {}).get("title") or (workflow.character_analysis_json or {}).get("source_title") or workflow.title,
                "character_count": len(characters) if isinstance(characters, list) else 0,
            }
        if step == GenericStoryWorkflowStep.SCENE_PLAN_GENERATION:
            pages = (workflow.scene_plan_json or {}).get("pages") or []
            return {
                "title": (workflow.scene_plan_json or {}).get("title") or workflow.title,
                "page_count": len(pages) if isinstance(pages, list) else 0,
                "requested_pages": workflow.requested_pages,
            }
        if step == GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION:
            visual_bible_json = self._workflow_visual_bible(workflow)
            characters = visual_bible_json.get("characters") or []
            return {
                "character_count": len(characters) if isinstance(characters, list) else 0,
                "location_count": len(visual_bible_json.get("locations") or []),
                "object_count": len(visual_bible_json.get("important_objects") or []),
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
        if step == GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION:
            visual_bible = self._workflow_visual_bible(workflow)
            return visual_bible or None
        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            return workflow.story_json
        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            return workflow.image_plan_json
        if step == GenericStoryWorkflowStep.IMAGE_GENERATION:
            story_json = workflow.story_json or {}
            return {
                "visual_bible": self._workflow_visual_bible(workflow),
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
        return page_count_range_for_age_group(age_group)[0]

    @staticmethod
    def _scene_plan_page_count_range(age_group: str) -> tuple[int, int]:
        return page_count_range_for_age_group(age_group)

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
