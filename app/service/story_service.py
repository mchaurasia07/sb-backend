import base64
from io import BytesIO
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.story import Story, StoryStatus
from app.entity.story_step import StoryStepName, StepStatus
from app.model.request.story import StoryGenerationRequest
from app.model.response.common import PaginatedResponse
from app.model.response.story import StoryResponse, StoryPageResponse, StoryStatusResponse, StoryStepResponse
from app.model.response.story_content import StoryContentResponse
from app.repository.child_repository import ChildRepository
from app.repository.story_repository import StoryRepository
from app.repository.story_step_repository import StoryStepRepository
from app.repository.story_page_repository import StoryPageRepository
from app.service.ai.base import AIProvider
from app.service.ai.factory import get_ai_provider
from app.service.image_storage_provider import get_image_storage_service
from app.service.plan_validator import PlanValidator
from app.service.image_plan_validator import ImagePlanValidator
from app.service.story_input_safety_service import StoryInputSafetyService
from app.service.story_completion_email_service import StoryCompletionEmailService
from app.service.story_narration_service import StoryNarrationService
from app.service.story_narration_profile import build_page_narration, normalize_page_emotion
from app.utils.prompt_loader import load_prompt, render_prompt

logger = logging.getLogger(__name__)

DEFAULT_STORY_LANGUAGE = "en"


def _repair_json(text: str) -> str:
    """Attempt to repair common JSON issues from LLM output."""
    # Remove markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

    # Fix unterminated strings: find quotes with newlines before closing quote
    # This is a heuristic - replace literal newlines in string values with \n
    text = re.sub(r':\s*"([^"]*)\n([^"]*)"', r': "\1\\n\2"', text)

    # Remove any control characters that might break JSON parsing
    text = "".join(ch if ord(ch) >= 32 or ch in "\n\r\t" else "" for ch in text)

    return text.strip()


def _finish_reason(result: Any) -> str | None:
    """Read provider finish reason metadata if available."""
    metadata = result.metadata or {}
    finish_reason = metadata.get("finish_reason")
    return str(finish_reason) if finish_reason is not None else None


def _is_token_limit_finish(result: Any) -> bool:
    finish_reason = (_finish_reason(result) or "").lower()
    return "length" in finish_reason or "max_token" in finish_reason


def _looks_truncated_json(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return True
    return not stripped.endswith(("}", "]"))


def _compact_json(data: Any) -> str:
    """Serialize prompt context without pretty-print whitespace."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _workflow_usage(result: Any, *, output_text: str | None = None) -> dict[str, Any]:
    metadata = result.metadata or {}
    usage = {
        "provider": metadata.get("provider"),
        "model": getattr(result, "model", None),
        "finish_reason": metadata.get("finish_reason"),
        "usage": metadata.get("usage"),
        "prompt_chars": len(getattr(result, "prompt_used", "") or ""),
    }
    if output_text is not None:
        usage["output_chars"] = len(output_text)
    return {key: value for key, value in usage.items() if value is not None}


def _with_workflow_usage(payload: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "_workflow_usage": usage,
    }


def _safe_prompt_value(value: str | None, default: str = "") -> str:
    """Keep user-provided prompt inputs on one line for safer template insertion."""
    text = (value or default).strip()
    return text.replace("\n", " ").replace('"', '\\"')


def _story_source_inputs(story: Story) -> dict[str, str]:
    """Canonical story-driving inputs used by plan, story, and image prompts."""
    return {
        "category": story.category or story.event_description or "adventure",
        "learning_goal": story.learning_goal or "personal growth",
        "context": story.context or "",
    }


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return value[:max_length]


def _story_moral_text(story_json: dict[str, Any]) -> str | None:
    moral = story_json.get("moral")
    if isinstance(moral, dict):
        return _first_non_empty(moral.get("text"))
    return _first_non_empty(moral, story_json.get("moral_theme"))


def _normalize_story_output(raw_story_json: dict[str, Any], plan: dict[str, Any], story: Story) -> dict[str, Any]:
    """Coerce LLM story output into the canonical story_json contract."""
    source_inputs = plan.get("source_inputs") or _story_source_inputs(story)
    raw_moral = raw_story_json.get("moral")
    raw_moral_text = raw_moral.get("text") if isinstance(raw_moral, dict) else raw_moral

    pages = []
    for idx, page in enumerate(raw_story_json.get("pages") or []):
        if not isinstance(page, dict):
            continue
        text = _first_non_empty(page.get("text"), page.get("narration_sample"))
        if not text:
            continue
        emotion = normalize_page_emotion(page.get("emotion"))
        pages.append(
            {
                "page_number": len(pages) + 1,
                "text": text,
                "emotion": emotion,
                "narration": build_page_narration(emotion, story.age_group),
            }
        )

    if not pages:
        raise AppException("Story generation returned no valid pages", code="INVALID_STORY_JSON")
    expected_page_count = len(plan.get("pages") or [])
    if expected_page_count and len(pages) != expected_page_count:
        raise AppException(
            f"Story generation returned {len(pages)} pages; expected {expected_page_count}",
            code="STORY_PAGE_COUNT_MISMATCH",
        )

    return {
        "title": _first_non_empty(raw_story_json.get("title"), plan.get("title"), "Untitled"),
        "theme": _first_non_empty(
            raw_story_json.get("theme"),
            plan.get("moral_theme"),
            source_inputs.get("category") if isinstance(source_inputs, dict) else None,
            story.category,
            "adventure",
        ),
        "art_style": _first_non_empty(
            raw_story_json.get("art_style"),
            plan.get("global_visual_style"),
            "",
        ) or "",
        "summary": _first_non_empty(raw_story_json.get("summary"), plan.get("summary")) or "",
        "pages": pages,
        "moral": _first_non_empty(raw_moral_text, raw_story_json.get("moral_theme"), plan.get("moral_theme")) or "",
    }


# Testing flags helper
class StoryGenerationFlags:
    """Helper for managing story generation test/feature flags."""

    def __init__(
        self,
        skip_image_generation: bool = False,
        skip_validation: bool = False,
    ):
        self.skip_image_generation = skip_image_generation
        self.skip_validation = skip_validation

    @classmethod
    def from_request(cls, payload: StoryGenerationRequest) -> "StoryGenerationFlags":
        return cls(
            skip_image_generation=payload.skip_image_generation,
            skip_validation=payload.skip_validation,
        )


class StoryService:
    """Orchestrates the story generation workflow."""

    MAX_RETRIES = 3
    PLAN_MAX_TOKENS = 14000
    STORY_MAX_TOKENS_BY_AGE = {
        "2-4": 4000,
        "5-7": 8000,
        "8-12": 12000,
    }
    IMAGE_PLAN_MAX_TOKENS_BY_AGE = {
        "2-4": 16000,
        "5-7": 24000,
        "8-12": 32000,
    }

    def __init__(self, session: AsyncSession):
        self.session = session
        self.stories = StoryRepository(session)
        self.story_steps = StoryStepRepository(session)
        self.story_pages = StoryPageRepository(session)
        self.children = ChildRepository(session)
        self._ai_provider: AIProvider | None = None
        self.plan_validator = PlanValidator()
        self.image_plan_validator = ImagePlanValidator()

    @property
    def ai_provider(self) -> AIProvider:
        """Initialize AI provider only for generation workflows."""
        if self._ai_provider is None:
            self._ai_provider = get_ai_provider()
        return self._ai_provider

    @staticmethod
    def _current_ai_config() -> dict[str, str | None]:
        provider = settings.AI_PROVIDER.strip().lower()
        if provider == "google":
            return {
                "ai_provider": provider,
                "text_model": settings.GOOGLE_TEXT_MODEL,
                "image_model": settings.GOOGLE_IMAGE_MODEL,
                "reference_image_model": settings.GOOGLE_REFERENCE_IMAGE_MODEL,
            }
        if provider == "openai":
            return {
                "ai_provider": provider,
                "text_model": settings.OPENAI_TEXT_MODEL,
                "image_model": settings.OPENAI_IMAGE_MODEL,
                "reference_image_model": settings.OPENAI_IMAGE_MODEL,
            }
        return {
            "ai_provider": provider,
            "text_model": None,
            "image_model": None,
            "reference_image_model": None,
        }

    async def _ensure_story_ai_config(self, story: Story) -> None:
        """Persist provider/model choices once so retries do not drift with env changes."""
        if story.ai_provider:
            self._ai_provider = get_ai_provider(story.ai_provider)
            return

        config = self._current_ai_config()
        story.ai_provider = config["ai_provider"]
        story.text_model = config["text_model"]
        story.image_model = config["image_model"]
        story.reference_image_model = config["reference_image_model"]
        await self.stories.update(story)
        await self.session.commit()
        self._ai_provider = get_ai_provider(story.ai_provider)

    @staticmethod
    def _story_image_model_kwargs(story: Story) -> dict[str, str]:
        kwargs: dict[str, str] = {}
        if story.reference_image_model:
            kwargs["model"] = story.reference_image_model
            kwargs["reference_image_model"] = story.reference_image_model
        if story.image_model:
            kwargs["image_model"] = story.image_model
        return kwargs

    @classmethod
    def _story_max_tokens(cls, age_group: str) -> int:
        value = getattr(age_group, "value", str(age_group))
        return cls.STORY_MAX_TOKENS_BY_AGE.get(value, cls.STORY_MAX_TOKENS_BY_AGE["5-7"])

    @classmethod
    def _image_plan_max_tokens(cls, age_group: str) -> int:
        value = getattr(age_group, "value", str(age_group))
        return cls.IMAGE_PLAN_MAX_TOKENS_BY_AGE.get(value, cls.IMAGE_PLAN_MAX_TOKENS_BY_AGE["5-7"])

    async def _set_current_step(self, story: Story, step_name: StoryStepName) -> None:
        story.status = StoryStatus.IN_PROGRESS
        story.current_step = step_name.value
        await self.stories.update(story)
        await self.session.commit()

    async def _persist_story_content(self, story: Story, story_json: dict[str, Any]) -> None:
        await self.stories.upsert_content(
            story,
            language=DEFAULT_STORY_LANGUAGE,
            story_json=story_json,
        )
        await self.session.commit()

    async def _load_existing_story_json(self, story: Story) -> dict[str, Any] | None:
        content = await self.stories.get_content_by_story_and_language(
            story_id=story.id,
            language=DEFAULT_STORY_LANGUAGE,
        )
        if content and isinstance(content.story_json, dict) and content.story_json.get("pages"):
            return content.story_json
        return None

    async def generate_story_async(
        self,
        user_id: UUID,
        child_id: UUID,
        payload: StoryGenerationRequest,
        public_base_url: str,
    ) -> StoryResponse:
        """Create story record and return immediately.

        Background task will execute the workflow asynchronously.
        """
        await StoryInputSafetyService().validate(payload)

        # Validate child exists and belongs to user
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found")

        # Require character image for visual consistency
        if not child.character_image_url:
            raise AppException(
                "Child must have a generated character image before story generation",
                code="NO_CHARACTER_IMAGE",
            )

        # Calculate age_group from child's date of birth
        age_group = self._get_age_group_from_dob(child.dob)
        logger.info(f"Calculated age_group={age_group} for child {child_id} with DOB={child.dob}")

        # Create story record with PENDING status
        story = await self.stories.create(
            user_id=user_id,
            child_id=child_id,
            generation_mode=payload.mode,
            age_group=age_group,
            category=payload.category,
            learning_goal=payload.learning_goal,
            context=payload.context,
            event_description=payload.event_description,
            input_request=payload.model_dump(mode="json"),
            **self._current_ai_config(),
        )
        await self.session.commit()

        logger.info(f"Story {story.id} created with status=PENDING, ready for background execution")

        # Manually construct response to avoid lazy-loading pages relationship
        return StoryResponse(
            id=story.id,
            title=story.title,
            moral=story.moral,
            summary=story.summary,
            status=story.status.value,
            current_step=story.current_step,
            generation_mode=story.generation_mode.value,
            age_group=story.age_group.value,
            category=story.category,
            learning_goal=story.learning_goal,
            context=story.context,
            pages=[],  # No pages yet, story is PENDING
            created_at=story.created_at,
            updated_at=story.updated_at,
        )

    async def retry_story_async(self, user_id: UUID, story_id: UUID) -> StoryStatusResponse:
        """Mark a failed story ready for a resumable background retry."""
        story = await self.stories.get_for_user_for_update(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found")

        if story.status == StoryStatus.IN_PROGRESS:
            raise AppException(
                "Story generation is already in progress",
                code="STORY_ALREADY_IN_PROGRESS",
            )
        if story.status == StoryStatus.COMPLETED:
            raise AppException(
                "Completed stories cannot be retried",
                code="STORY_ALREADY_COMPLETED",
            )

        story.status = StoryStatus.PENDING
        story.current_step = None
        story.error_message = None
        await self.stories.update(story)
        await self.session.commit()
        return StoryStatusResponse(
            story_id=story.id,
            status=story.status.value,
            current_step=story.current_step,
            error_message=story.error_message,
            updated_at=story.updated_at,
        )

    async def recover_story_async(
        self,
        user_id: UUID,
        story_id: UUID,
        *,
        stale_after_minutes: int = 15,
    ) -> StoryStatusResponse:
        """Mark a stale in-progress story as failed so the UI can retry it."""
        story = await self.stories.get_for_user_for_update(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found")

        if story.status != StoryStatus.IN_PROGRESS:
            return StoryStatusResponse(
                story_id=story.id,
                status=story.status.value,
                current_step=story.current_step,
                error_message=story.error_message,
                updated_at=story.updated_at,
            )

        updated_at = story.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        age = datetime.now(UTC) - updated_at
        if age < timedelta(minutes=stale_after_minutes):
            raise AppException(
                f"Story is still active. Recover is allowed after {stale_after_minutes} minutes without updates.",
                code="STORY_NOT_STALE",
            )

        story.status = StoryStatus.FAILED
        story.current_step = None
        story.error_message = (
            f"Story workflow was recovered after being stuck in progress for {int(age.total_seconds() // 60)} minutes"
        )
        await self.stories.update(story)
        await self.session.commit()
        return StoryStatusResponse(
            story_id=story.id,
            status=story.status.value,
            current_step=story.current_step,
            error_message=story.error_message,
            updated_at=story.updated_at,
        )

    async def execute_workflow(
        self,
        story_id: UUID,
        flags: StoryGenerationFlags = None,
        resume: bool = False,
    ) -> Story:
        """Execute the story generation workflow.

        This method is called by a background task with a fresh database session.
        """
        logger.info(f"[WORKFLOW] Starting for story {story_id}")
        if flags is None:
            flags = StoryGenerationFlags()

        logger.info(f"[WORKFLOW] Fetching story {story_id} from database")
        story = await self.stories.get_by_id_for_update(story_id)
        if story is None:
            logger.error(f"[WORKFLOW] Story {story_id} not found")
            raise NotFoundException(f"Story {story_id} not found")
        if story.status == StoryStatus.IN_PROGRESS:
            logger.warning("Story %s is already in progress; skipping duplicate workflow runner", story_id)
            return story
        await self._ensure_story_ai_config(story)
        story.status = StoryStatus.IN_PROGRESS
        story.error_message = None
        await self.stories.update(story)
        await self.session.commit()

        logger.info(f"[WORKFLOW] Story found, starting execution resume={resume}")
        try:
            # Step 1: Story Plan Generation
            if (
                resume
                and story.story_plan_validated
                and isinstance(story.story_plan_json, dict)
                and story.story_plan_json.get("pages")
            ):
                story_plan = story.story_plan_json
                logger.info("Story %s: Reusing existing validated story plan checkpoint", story_id)
            else:
                await self._set_current_step(story, StoryStepName.STORY_PLAN_GENERATION)
                logger.info(f"Story {story_id}: Starting step 1 - Story Plan Generation")
                story_plan = await self._step_generate_plan(story, flags)
                story.story_plan_json = story_plan
                story.story_plan_validated = False
                await self.stories.update(story)
                await self.session.commit()

            # Step 2: Story Plan Validation (with retries)
            await self._set_current_step(story, StoryStepName.STORY_PLAN_VALIDATION)
            logger.info(f"Story {story_id}: Starting step 2 - Story Plan Validation")

            story_plan = await self._step_validate_plan(story, story_plan, flags)
            story.story_plan_json = story_plan
            story.story_plan_validated = True
            await self.stories.update(story)
            await self.session.commit()

            # Step 3: Story Generation
            story_json = await self._load_existing_story_json(story) if resume else None
            if story_json is not None:
                logger.info("Story %s: Reusing existing story JSON checkpoint", story_id)
                self._apply_story_metadata(story, story_plan, story_json)
                await self.stories.update(story)
                await self.session.commit()
            else:
                await self._set_current_step(story, StoryStepName.STORY_GENERATION)
                logger.info(f"Story {story_id}: Starting step 3 - Story Generation")
                story_json = await self._step_generate_story(story, story_plan, flags)
                self._apply_story_metadata(story, story_plan, story_json)
                await self.stories.update(story)
                await self._persist_story_content(story, story_json)

            # Step 4: Image Plan Generation
            if (
                resume
                and story.image_plan_validated
                and isinstance(story.image_plan_json, dict)
                and story.image_plan_json.get("pages")
            ):
                image_plan = story.image_plan_json
                logger.info("Story %s: Reusing existing validated image plan checkpoint", story_id)
            else:
                await self._set_current_step(story, StoryStepName.IMAGE_PLAN_GENERATION)
                logger.info(f"Story {story_id}: Starting step 4 - Image Plan Generation")
                image_plan = await self._step_generate_image_plan(story, story_plan, story_json, flags)
                story.image_plan_json = image_plan
                story.image_plan_validated = False
                await self.stories.update(story)
                await self.session.commit()

            # Step 5: Image Plan Validation (optional, can skip)
            if not flags.skip_validation:
                await self._set_current_step(story, StoryStepName.IMAGE_PLAN_VALIDATION)
                logger.info(f"Story {story_id}: Starting step 5 - Image Plan Validation")
                image_plan = await self._step_validate_image_plan(story, image_plan, story_json, flags)
                story.image_plan_json = image_plan
                story.image_plan_validated = True
                await self.stories.update(story)
                await self.session.commit()
            else:
                story.image_plan_validated = True
                await self.stories.update(story)
                await self.session.commit()

            # Step 6: Image Generation
            if not flags.skip_image_generation:
                await self._set_current_step(story, StoryStepName.IMAGE_GENERATION)
                logger.info(f"Story {story_id}: Starting step 6 - Image Generation")
                await self._step_generate_images(story, story_json, image_plan, flags)
            else:
                logger.info(f"Story {story_id}: Skipping image generation (test mode)")
                await self._create_pages_without_images(story, story_json)
                await self._persist_story_content(story, story_json)
                await self.session.commit()

            # Step 7: Narration Generation
            story_json = await self._load_existing_story_json(story) or story_json
            await self._set_current_step(story, StoryStepName.NARRATION_GENERATION)
            logger.info(f"Story {story_id}: Starting step 7 - Narration Generation")
            story_json = await self._step_generate_narration(story, story_json)

            # Mark story as completed
            story.status = StoryStatus.COMPLETED
            story.current_step = None
            self._apply_story_metadata(story, story_plan, story_json)
            story.story_plan_json = story_plan
            story.image_plan_json = image_plan
            await self.stories.upsert_content(story, language=DEFAULT_STORY_LANGUAGE, story_json=story_json)
            await self.stories.update(story)
            await self.session.commit()
            await StoryCompletionEmailService(self.session).send_story_completed(story, story_json)

            logger.info(f"Story {story_id}: Workflow completed successfully")
            return story

        except Exception as e:
            logger.exception(f"Story {story_id}: Workflow failed with error: {str(e)}")
            story.status = StoryStatus.FAILED
            story.error_message = str(e)
            story.current_step = None
            await self.stories.update(story)
            await self.session.commit()
            raise

    async def _step_generate_narration(self, story: Story, story_json: dict[str, Any]) -> dict[str, Any]:
        """Step 7: Generate narration audio and timing metadata for story JSON."""
        step = await self.story_steps.create(story.id, StoryStepName.NARRATION_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()
        step.prompt = json.dumps(
            {
                "language": DEFAULT_STORY_LANGUAGE,
                "overwrite": False,
                "source": "story_workflow",
                "page_count": len(story_json.get("pages", [])),
            },
            indent=2,
        )
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            narration_service = StoryNarrationService(self.session)
            narrated_story_json = await narration_service.generate_story_json_narration(
                story_json,
                story_id=story.id,
                language=DEFAULT_STORY_LANGUAGE,
                overwrite=False,
                source="story_workflow",
            )

            pages = narrated_story_json.get("pages", [])
            narrated_pages = [
                page
                for page in pages
                if isinstance(page, dict)
                and (page.get("tts_skipped") or page.get("audio_url") or page.get("duration"))
            ]
            total_duration = sum(
                page.get("duration") or 0
                for page in narrated_pages
                if isinstance(page.get("duration"), (int, float))
            )

            step.response = {
                "narration_generated": True,
                "language": DEFAULT_STORY_LANGUAGE,
                "page_count": len(pages),
                "narrated_page_count": len(narrated_pages),
                "total_duration": round(total_duration, 2),
                "tts_skipped": settings.GOOGLE_TTS_SKIP_CALL,
                "_workflow_usage": {
                    "provider": "google",
                    "model": settings.GOOGLE_TTS_MODEL,
                    "voice": settings.GOOGLE_TTS_VOICE,
                    "page_count": len(pages),
                    "audio_page_count": len(
                        [
                            page
                            for page in pages
                            if isinstance(page, dict) and not page.get("tts_skipped") and page.get("audio_url")
                        ]
                    ),
                    "total_text_chars": sum(
                        len(page.get("text") or "")
                        for page in pages
                        if isinstance(page, dict)
                    ),
                    "total_duration": round(total_duration, 2),
                    "note": "Gemini TTS token usage is not returned by the current REST response.",
                },
            }
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return narrated_story_json

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    @staticmethod
    def _apply_story_metadata(story: Story, story_plan: dict[str, Any], story_json: dict[str, Any]) -> None:
        """Copy generated metadata into searchable top-level story columns."""
        source_inputs = story_json.get("source_inputs") or story_plan.get("source_inputs") or {}

        story.title = _truncate(
            _first_non_empty(
                story_json.get("title"),
                story_plan.get("title"),
                story.title,
            ),
            255,
        )
        story.moral = _truncate(
            _first_non_empty(
                _story_moral_text(story_json),
                story_plan.get("moral_theme"),
                story.moral,
            ),
            255,
        )
        story.summary = _first_non_empty(
            story_json.get("summary"),
            story_plan.get("summary"),
            story.summary,
        )

        # `category` is the existing stories-table column that represents the requested theme/category.
        story.category = _truncate(
            _first_non_empty(
                story.category,
                source_inputs.get("category") if isinstance(source_inputs, dict) else None,
                story_plan.get("theme"),
                story_plan.get("category"),
                story.event_description,
            ),
            100,
        )
        story.learning_goal = _truncate(
            _first_non_empty(
                story.learning_goal,
                source_inputs.get("learning_goal") if isinstance(source_inputs, dict) else None,
            ),
            500,
        )

    async def _step_generate_plan(self, story: Story, flags: StoryGenerationFlags) -> dict[str, Any]:
        """Step 1: Generate story plan using LLM."""
        child = await self.children.get_for_user(story.user_id, story.child_id)
        if child is None:
            raise NotFoundException("Child profile not found during plan generation")

        # Load prompt template
        template = load_prompt("prompts/story/story_plan_prompt.txt")

        # Prepare variables
        pages = self._get_page_count_for_age_group(story.age_group)
        source_inputs = _story_source_inputs(story)
        theme = source_inputs["category"]

        # Generate better hobby suggestions based on age group
        hobby = self._get_hobby_for_age_group(story.age_group)

        # Extract detailed character metadata for consistent visual anchor
        character_context = self._build_character_reference_context(child)

        prompt = self._render_story_plan_prompt(
            template,
            story=story,
            child=child,
            source_inputs=source_inputs,
            theme=theme,
            hobby=hobby,
            pages=pages,
            character_context=character_context,
        )

        # Create step record
        step = await self.story_steps.create(story.id, StoryStepName.STORY_PLAN_GENERATION)
        step.prompt = prompt
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            # Call LLM
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=self.PLAN_MAX_TOKENS,
                temperature=0.4,
                response_format={"type": "json_object"},
            )

            # Log raw response for debugging
            logger.info(f"Story {story.id}: Raw LLM response (first 2000 chars):\n{result.text[:2000]}")

            # Parse response with JSON repair
            try:
                story_plan = json.loads(result.text)
            except json.JSONDecodeError as e:
                logger.error(f"Story {story.id}: JSON parse error - {str(e)}\nFull response:\n{result.text}")
                # Try to repair common JSON issues
                repaired = _repair_json(result.text)
                logger.info(f"Story {story.id}: Repaired response (first 2000 chars):\n{repaired[:2000]}")
                try:
                    story_plan = json.loads(repaired)
                    logger.warning(f"Story {story.id}: Recovered JSON after repair (original error: {str(e)})")
                except json.JSONDecodeError as repair_error:
                    logger.error(f"Story {story.id}: Repair failed - {str(repair_error)}")
                    finish_reason = _finish_reason(result)
                    if _is_token_limit_finish(result) or _looks_truncated_json(result.text):
                        raise AppException(
                            "Story plan generation returned incomplete JSON. "
                            f"Finish reason: {finish_reason or 'unknown'}. "
                            "Please retry; the workflow will regenerate the plan.",
                            code="INCOMPLETE_LLM_JSON",
                        )
                    raise AppException(
                        f"Invalid JSON from LLM (even after repair): {str(repair_error)}. Original: {str(e)}",
                        code="INVALID_LLM_JSON",
                    )

            story_plan["source_inputs"] = source_inputs
            step.response = _with_workflow_usage(
                story_plan,
                _workflow_usage(result, output_text=result.text),
            )
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return story_plan

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _step_validate_plan(
        self, story: Story, plan: dict[str, Any], flags: StoryGenerationFlags
    ) -> dict[str, Any]:
        """Step 2: Validate story plan with retry logic."""
        if flags.skip_validation:
            logger.info(f"Story {story.id}: Skipping plan validation (test mode)")
            return plan

        step = await self.story_steps.create(story.id, StoryStepName.STORY_PLAN_VALIDATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()
        retry_usages: list[dict[str, Any]] = []

        for attempt in range(1, self.MAX_RETRIES + 1):
            step.retry_count = attempt - 1

            # Validate
            result = self.plan_validator.validate(
                plan,
                age_group=story.age_group,
                source_inputs=_story_source_inputs(story),
            )

            if result.ok:
                step.status = StepStatus.COMPLETED
                step.completed_at = datetime.utcnow()
                step.response = {
                    "valid": True,
                    "_workflow_usage": {
                        "validator": "local",
                        "retry_text_generations": retry_usages,
                    },
                }
                await self.story_steps.update(step)
                await self.session.commit()
                logger.info(f"Story {story.id}: Plan validation passed on attempt {attempt}")
                return plan

            # Validation failed
            error_list = "\n".join([f"  - {err}" for err in result.errors])
            logger.warning(f"Story {story.id}: Plan validation failed on attempt {attempt}:\n{error_list}")

            if attempt < self.MAX_RETRIES:
                # Regenerate with errors as feedback
                await self.session.commit()
                try:
                    plan, usage = await self._retry_plan_generation(story, plan, result.errors, attempt)
                    retry_usages.append(usage)
                except Exception as e:
                    logger.error(f"Story {story.id}: Failed to regenerate plan on attempt {attempt}: {str(e)}")
                    step.status = StepStatus.FAILED
                    step.error_message = str(e)
                    step.completed_at = datetime.utcnow()
                    await self.story_steps.update(step)
                    await self.session.commit()
                    raise

        # All retries exhausted - perform final validation to get errors for logging
        final_result = self.plan_validator.validate(
            plan,
            age_group=story.age_group,
            source_inputs=_story_source_inputs(story),
        )
        error_details = "\n".join([f"  - {err}" for err in final_result.errors])
        error_msg = f"Plan validation failed after {self.MAX_RETRIES} attempts:\n{error_details}"

        step.status = StepStatus.FAILED
        step.error_message = error_msg
        step.completed_at = datetime.utcnow()
        step.response = {
            "valid": False,
            "_workflow_usage": {
                "validator": "local",
                "retry_text_generations": retry_usages,
            },
        }
        await self.story_steps.update(step)
        await self.session.commit()

        logger.error(f"Story {story.id}: {error_msg}")
        raise AppException(
            f"Story plan validation failed after {self.MAX_RETRIES} retries",
            code="PLAN_VALIDATION_FAILED",
        )

    async def _retry_plan_generation(
        self, story: Story, previous_plan: dict[str, Any], errors: list[str], attempt: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Regenerate story plan with validation errors as feedback."""
        logger.info(f"Story {story.id}: Regenerating plan (attempt {attempt + 1}) with error feedback")

        child = await self.children.get_for_user(story.user_id, story.child_id)
        if child is None:
            raise NotFoundException("Child profile not found during plan retry")
        template = load_prompt("prompts/story/story_plan_prompt.txt")

        pages = self._get_page_count_for_age_group(story.age_group)
        source_inputs = _story_source_inputs(story)
        theme = source_inputs["category"]
        hobby = self._get_hobby_for_age_group(story.age_group)
        character_context = self._build_character_reference_context(child)

        error_feedback = "\n".join([f"- {err}" for err in errors])
        enhanced_prompt = self._render_story_plan_prompt(
            template,
            story=story,
            child=child,
            source_inputs=source_inputs,
            theme=theme,
            hobby=hobby,
            pages=pages,
            character_context=character_context,
        )
        enhanced_prompt += f"\n\nPREVIOUS VALIDATION ERRORS (fix these):\n{error_feedback}"

        result = await self.ai_provider.generate_text(
            enhanced_prompt,
            max_tokens=self.PLAN_MAX_TOKENS,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        # Log raw response for debugging
        logger.info(f"Story {story.id}: Raw retry LLM response (first 2000 chars):\n{result.text[:2000]}")

        try:
            new_plan = json.loads(result.text)
        except json.JSONDecodeError as e:
            logger.error(f"Story {story.id}: JSON parse error in retry - {str(e)}\nFull response:\n{result.text}")
            # Try to repair common JSON issues
            repaired = _repair_json(result.text)
            logger.info(f"Story {story.id}: Repaired retry response (first 2000 chars):\n{repaired[:2000]}")
            try:
                new_plan = json.loads(repaired)
                logger.warning(f"Story {story.id}: Recovered JSON in retry after repair")
            except json.JSONDecodeError as repair_error:
                logger.error(f"Story {story.id}: Retry repair failed - {str(repair_error)}")
                raise AppException(
                    f"Invalid JSON from regenerated plan (even after repair): {str(repair_error)}",
                    code="INVALID_LLM_JSON",
                )

        new_plan["source_inputs"] = source_inputs
        return new_plan, _workflow_usage(result, output_text=result.text)

    async def _step_generate_story(
        self, story: Story, plan: dict[str, Any], flags: StoryGenerationFlags
    ) -> dict[str, Any]:
        """Step 3: Generate story text from validated plan."""
        step = await self.story_steps.create(story.id, StoryStepName.STORY_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        template = load_prompt("prompts/story/story_generation_prompt.txt")
        prompt_plan = self._build_story_generation_context(plan)
        prompt = template.replace("{story_plan_json}", _compact_json(prompt_plan))
        step.prompt = prompt
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=self._story_max_tokens(story.age_group),
                temperature=0.7,
                response_format={"type": "json_object"},
            )

            try:
                raw_story_json = json.loads(result.text)
            except json.JSONDecodeError as e:
                raise AppException(f"Invalid JSON from story generation: {str(e)}", code="INVALID_LLM_JSON")

            story_json = _normalize_story_output(raw_story_json, plan, story)

            step.response = _with_workflow_usage(
                story_json,
                _workflow_usage(result, output_text=result.text),
            )
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return story_json

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _step_generate_image_plan(
        self, story: Story, story_plan: dict[str, Any], story_json: dict[str, Any], flags: StoryGenerationFlags
    ) -> dict[str, Any]:
        """Step 4: Generate image plan from story."""
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_PLAN_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        template = load_prompt("prompts/story/image_plan_prompt.txt")

        child = await self.children.get_for_user(story.user_id, story.child_id)
        if child is None:
            raise NotFoundException("Child profile not found during image plan generation")
        character_context = self._build_character_reference_context(child)
        compact_story_plan, compact_story_json = self._build_image_plan_context(story_plan, story_json)

        # Populate all placeholders in template
        prompt = template.replace("{story_plan_json}", _compact_json(compact_story_plan))
        prompt = prompt.replace("{story_json}", _compact_json(compact_story_json))
        prompt = prompt.replace("{character_description}", character_context["character_description"])
        prompt = prompt.replace("{character_profile}", character_context["character_description"])
        prompt = prompt.replace("{child_age_label}", character_context["child_age_label"])
        prompt = prompt.replace("{child_age_visual_guidance}", character_context["child_age_visual_guidance"])
        step.prompt = prompt
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=self._image_plan_max_tokens(story.age_group),
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            try:
                image_plan = json.loads(result.text)
            except json.JSONDecodeError as e:
                finish_reason = _finish_reason(result)
                logger.error(
                    "Story %s: Image plan JSON parse error - %s. Finish reason: %s. "
                    "Response length: %s. Response start:\n%s\nResponse end:\n%s",
                    story.id,
                    str(e),
                    finish_reason,
                    len(result.text),
                    result.text[:2000],
                    result.text[-2000:],
                )

                repaired = _repair_json(result.text)
                try:
                    image_plan = json.loads(repaired)
                    logger.warning(
                        "Story %s: Recovered image plan JSON after repair. Original error: %s",
                        story.id,
                        str(e),
                    )
                except json.JSONDecodeError as repair_error:
                    if _is_token_limit_finish(result) or _looks_truncated_json(result.text):
                        raise AppException(
                            "Image plan generation returned incomplete JSON. "
                            f"Finish reason: {finish_reason or 'unknown'}. "
                            "This is usually caused by the model hitting its output token limit.",
                            code="INVALID_LLM_JSON",
                        )

                    raise AppException(
                        f"Invalid JSON from image plan generation: {str(repair_error)}",
                        code="INVALID_LLM_JSON",
                    )

            step.response = _with_workflow_usage(
                image_plan,
                _workflow_usage(result, output_text=result.text),
            )
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return image_plan

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _step_validate_image_plan(
        self,
        story: Story,
        image_plan: dict[str, Any],
        story_json: dict[str, Any],
        flags: StoryGenerationFlags,
    ) -> dict[str, Any]:
        """Step 5: Validate image plan (optional)."""
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_PLAN_VALIDATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        image_plan = self._normalize_image_plan(image_plan)
        result = self.image_plan_validator.validate(image_plan, story_json=story_json)

        if result.ok:
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            step.response = {
                "valid": True,
                "_workflow_usage": {
                    "validator": "local",
                    "token_usage": None,
                },
            }
            await self.story_steps.update(step)
            await self.session.commit()
            logger.info(f"Story {story.id}: Image plan validation passed")
            return image_plan

        # Validation failed
        logger.warning(f"Story {story.id}: Image plan validation failed: {result.errors}")
        step.status = StepStatus.FAILED
        step.error_message = "; ".join(result.errors)
        step.completed_at = datetime.utcnow()
        step.response = {
            "valid": False,
            "_workflow_usage": {
                "validator": "local",
                "token_usage": None,
            },
        }
        await self.story_steps.update(step)
        await self.session.commit()
        raise AppException(
            f"Image plan validation failed: {'; '.join(result.errors)}",
            code="IMAGE_PLAN_VALIDATION_FAILED",
        )

    @staticmethod
    def _normalize_image_plan(image_plan: dict[str, Any]) -> dict[str, Any]:
        return image_plan

    async def _step_generate_images(
        self,
        story: Story,
        story_json: dict[str, Any],
        image_plan: dict[str, Any],
        flags: StoryGenerationFlags,
    ) -> None:
        """Step 6: Generate story images using the character image as reference."""
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        # Store image plan as prompt for audit trail
        step.prompt = json.dumps(image_plan, indent=2)
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            pages = story_json.get("pages", [])
            cover = image_plan.get("cover", {})
            back_cover = image_plan.get("back_cover", {})
            image_pages = image_plan.get("pages", [])
            visual_bible = image_plan.get("visual_bible", {})
            image_generation_template = load_prompt("prompts/story/image_generation_prompt.txt")
            child = await self.children.get_for_user(story.user_id, story.child_id)
            if child is None:
                raise NotFoundException("Child profile not found during image generation")
            if not child.avatar_image_url:
                raise AppException(
                    "Child profile photo is required for story image generation",
                    code="NO_PHOTO",
                )
            if not child.character_image_url:
                raise AppException(
                    "Generated character image is required for story image generation",
                    code="NO_CHARACTER_IMAGE",
                )

            image_storage = get_image_storage_service()
            avatar_image_base64 = await self._load_image_as_base64(child.avatar_image_url)
            character_image_base64 = await self._load_image_as_base64(child.character_image_url)
            character_context = self._build_character_reference_context(child)
            image_model_kwargs = self._story_image_model_kwargs(story)
            generated_image_prompts: list[dict[str, Any]] = []

            # Generate cover image
            existing_cover = await self.story_pages.get_by_story_page(story.id, 0)
            if existing_cover and existing_cover.image_url:
                logger.info("Story %s: Reusing existing cover image", story.id)
                generated_image_prompts.append(
                    {
                        "page_type": "cover",
                        "page_number": 0,
                        "image_url": existing_cover.image_url,
                        "skipped_existing": True,
                    }
                )
                story_json["cover_image_url"] = existing_cover.image_url
            elif cover and cover.get("image_prompt"):
                logger.info(f"Story {story.id}: Generating cover image")
                cover_prompt = self._render_story_image_prompt(
                    image_generation_template,
                    visual_bible,
                    cover.get("image_prompt"),
                    character_context,
                    page_type="cover",
                    target_aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                    page_data=cover,
                )
                cover_bytes = await self.ai_provider.create_story_image(
                    cover_prompt,
                    reference_image_base64=avatar_image_base64,
                    consistency_reference_image_base64=character_image_base64,
                    child_age_label=character_context["child_age_label"],
                    child_age_visual_guidance=character_context["child_age_visual_guidance"],
                    **image_model_kwargs,
                    size=settings.STORY_COVER_IMAGE_SIZE,
                    quality=settings.STORY_IMAGE_QUALITY,
                    aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                )
                generated_image_prompts.append(
                    {
                        "page_type": "cover",
                        "page_number": 0,
                        "target_aspect_ratio": settings.STORY_COVER_ASPECT_RATIO,
                        "image_size": settings.STORY_COVER_IMAGE_SIZE,
                        "source_image_prompt": cover.get("image_prompt"),
                        "rendered_prompt": cover_prompt,
                        "provider_prompt_used": cover_bytes.prompt_used,
                        "used_character_reference": True,
                        "model": cover_bytes.model,
                        "provider": (cover_bytes.metadata or {}).get("provider"),
                        "usage": (cover_bytes.metadata or {}).get("usage"),
                    }
                )
                cover_image_bytes = self._crop_image_bytes_to_aspect_ratio(
                    cover_bytes.image_bytes,
                    settings.STORY_COVER_ASPECT_RATIO,
                )
                cover_url = await image_storage.save_story_image(
                    story.id,
                    cover_image_bytes,
                    "cover.png",
                    "",  # base_url will be added by storage service
                )
                await self.story_pages.upsert_page(
                    story.id,
                    page_number=0,
                    page_type="cover",
                    text="",
                    image_prompt=cover.get("image_prompt"),
                    image_url=cover_url,
                )
                story_json["cover_image_url"] = cover_url

            # Generate page images
            for img_page in image_pages:
                page_num = img_page.get("page_number", 0)
                if img_page.get("image_prompt") and page_num > 0:
                    existing_page = await self.story_pages.get_by_story_page(story.id, page_num)
                    if existing_page and existing_page.image_url:
                        logger.info("Story %s: Reusing existing image for page %s", story.id, page_num)
                        generated_image_prompts.append(
                            {
                                "page_type": "page",
                                "page_number": page_num,
                                "image_url": existing_page.image_url,
                                "skipped_existing": True,
                            }
                        )
                        self._set_story_json_page_image_url(story_json, page_num, existing_page.image_url)
                        continue

                    logger.info(f"Story {story.id}: Generating image for page {page_num}")
                    page_prompt = self._render_story_image_prompt(
                        image_generation_template,
                        visual_bible,
                        img_page.get("image_prompt"),
                        character_context,
                        page_type="story_page",
                        target_aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
                        page_data=img_page,
                    )
                    image_bytes = await self.ai_provider.create_story_image(
                        page_prompt,
                        reference_image_base64=avatar_image_base64,
                        consistency_reference_image_base64=character_image_base64,
                        child_age_label=character_context["child_age_label"],
                        child_age_visual_guidance=character_context["child_age_visual_guidance"],
                        **image_model_kwargs,
                        size=settings.STORY_PAGE_IMAGE_SIZE,
                        quality=settings.STORY_IMAGE_QUALITY,
                        aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
                    )
                    generated_image_prompts.append(
                        {
                            "page_type": "page",
                            "page_number": page_num,
                            "target_aspect_ratio": settings.STORY_PAGE_ASPECT_RATIO,
                            "image_size": settings.STORY_PAGE_IMAGE_SIZE,
                            "source_image_prompt": img_page.get("image_prompt"),
                            "rendered_prompt": page_prompt,
                            "provider_prompt_used": image_bytes.prompt_used,
                            "used_character_reference": True,
                            "model": image_bytes.model,
                            "provider": (image_bytes.metadata or {}).get("provider"),
                            "usage": (image_bytes.metadata or {}).get("usage"),
                        }
                    )
                    page_image_bytes = self._crop_image_bytes_to_aspect_ratio(
                        image_bytes.image_bytes,
                        settings.STORY_PAGE_ASPECT_RATIO,
                    )
                    image_url = await image_storage.save_story_image(
                        story.id,
                        page_image_bytes,
                        f"page_{page_num}.png",
                        "",
                    )

                    # Find corresponding story page
                    if page_num <= len(pages):
                        story_page = pages[page_num - 1]
                        await self.story_pages.upsert_page(
                            story.id,
                            page_number=page_num,
                            page_type="page",
                            text=story_page.get("text", ""),
                            image_prompt=img_page.get("image_prompt"),
                            image_url=image_url,
                        )
                        self._set_story_json_page_image_url(story_json, page_num, image_url)

            # Generate back cover image
            back_cover_page_number = len(pages) + 1
            existing_back_cover = await self.story_pages.get_by_story_page(story.id, back_cover_page_number)
            if existing_back_cover and existing_back_cover.image_url:
                logger.info("Story %s: Reusing existing back cover image", story.id)
                generated_image_prompts.append(
                    {
                        "page_type": "back_cover",
                        "page_number": back_cover_page_number,
                        "image_url": existing_back_cover.image_url,
                        "skipped_existing": True,
                    }
                )
                story_json["back_cover_image_url"] = existing_back_cover.image_url
            elif back_cover and back_cover.get("image_prompt"):
                logger.info(f"Story {story.id}: Generating back cover image")
                back_cover_prompt = self._render_story_image_prompt(
                    image_generation_template,
                    visual_bible,
                    back_cover.get("image_prompt"),
                    character_context,
                    page_type="back_cover",
                    target_aspect_ratio=settings.STORY_BACK_COVER_ASPECT_RATIO,
                    page_data=back_cover,
                )
                back_cover_bytes = await self.ai_provider.create_story_image(
                    back_cover_prompt,
                    reference_image_base64=avatar_image_base64,
                    consistency_reference_image_base64=character_image_base64,
                    child_age_label=character_context["child_age_label"],
                    child_age_visual_guidance=character_context["child_age_visual_guidance"],
                    **image_model_kwargs,
                    size=settings.STORY_BACK_COVER_IMAGE_SIZE,
                    quality=settings.STORY_IMAGE_QUALITY,
                    aspect_ratio=settings.STORY_BACK_COVER_ASPECT_RATIO,
                )
                generated_image_prompts.append(
                    {
                        "page_type": "back_cover",
                        "page_number": back_cover_page_number,
                        "target_aspect_ratio": settings.STORY_BACK_COVER_ASPECT_RATIO,
                        "image_size": settings.STORY_BACK_COVER_IMAGE_SIZE,
                        "source_image_prompt": back_cover.get("image_prompt"),
                        "rendered_prompt": back_cover_prompt,
                        "provider_prompt_used": back_cover_bytes.prompt_used,
                        "used_character_reference": True,
                        "model": back_cover_bytes.model,
                        "provider": (back_cover_bytes.metadata or {}).get("provider"),
                        "usage": (back_cover_bytes.metadata or {}).get("usage"),
                    }
                )
                back_cover_image_bytes = self._crop_image_bytes_to_aspect_ratio(
                    back_cover_bytes.image_bytes,
                    settings.STORY_BACK_COVER_ASPECT_RATIO,
                )
                back_cover_url = await image_storage.save_story_image(
                    story.id,
                    back_cover_image_bytes,
                    "back_cover.png",
                    "",
                )
                await self.story_pages.upsert_page(
                    story.id,
                    page_number=back_cover_page_number,
                    page_type="back_cover",
                    text="",
                    image_prompt=back_cover.get("image_prompt"),
                    image_url=back_cover_url,
                )
                story_json["back_cover_image_url"] = back_cover_url

            # Store image generation results in response for audit trail
            step.response = {
                "images_generated": True,
                "message": "All images generated and saved successfully",
                "image_count": len(generated_image_prompts),
                "used_avatar_reference": True,
                "used_character_reference": True,
                "avatar_image_url": child.avatar_image_url,
                "character_image_url": child.character_image_url,
                "child_age_label": character_context["child_age_label"],
                "rendered_image_prompts": generated_image_prompts,
                "_workflow_usage": {
                    "provider": story.ai_provider or settings.AI_PROVIDER,
                    "image_model": story.image_model,
                    "reference_image_model": story.reference_image_model,
                    "image_items": len(generated_image_prompts),
                    "generated_image_count": len(
                        [item for item in generated_image_prompts if not item.get("skipped_existing")]
                    ),
                    "skipped_existing_image_count": len(
                        [item for item in generated_image_prompts if item.get("skipped_existing")]
                    ),
                    "models": sorted(
                        {
                            str(item.get("model"))
                            for item in generated_image_prompts
                            if item.get("model")
                        }
                    ),
                    "usage": [
                        item.get("usage")
                        for item in generated_image_prompts
                        if item.get("usage")
                    ],
                },
            }
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self._persist_story_content(story, story_json)
            await self.session.commit()

            logger.info(f"Story {story.id}: All images generated successfully")

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    @staticmethod
    def _crop_image_bytes_to_aspect_ratio(image_bytes: bytes, aspect_ratio: str) -> bytes:
        """Center-crop generated image bytes to an exact width:height ratio."""
        try:
            numerator_text, denominator_text = aspect_ratio.split(":", 1)
            numerator = int(numerator_text)
            denominator = int(denominator_text)
        except (AttributeError, ValueError) as exc:
            raise AppException(
                f"Invalid story image aspect ratio '{aspect_ratio}'",
                code="INVALID_IMAGE_ASPECT_RATIO",
            ) from exc

        if numerator <= 0 or denominator <= 0:
            raise AppException(
                f"Invalid story image aspect ratio '{aspect_ratio}'",
                code="INVALID_IMAGE_ASPECT_RATIO",
            )

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                width, height = image.size
                scale = min(width // numerator, height // denominator)
                if scale <= 0:
                    return image_bytes

                target_width = scale * numerator
                target_height = scale * denominator
                left = (width - target_width) // 2
                top = (height - target_height) // 2
                cropped = image.crop((left, top, left + target_width, top + target_height))
                output = BytesIO()
                cropped.save(output, format="PNG")
                return output.getvalue()
        except UnidentifiedImageError as exc:
            raise AppException("Generated story image is not a valid image", code="INVALID_GENERATED_IMAGE") from exc

    @staticmethod
    def _render_story_image_prompt(
        template: str,
        visual_bible: dict[str, Any],
        image_prompt: str,
        character_context: dict[str, str],
        page_type: str,
        target_aspect_ratio: str,
        page_data: dict[str, Any] | None = None,
    ) -> str:
        """Render the final story image prompt with consistency context."""
        return render_prompt(
            template,
            {
                "visual_bible": visual_bible,
                "page_data": page_data or {"image_prompt": image_prompt},
                "character_consistency_json": visual_bible,
                "character_reference_metadata": character_context["character_description"],
                "child_age_label": character_context["child_age_label"],
                "child_age_visual_guidance": character_context["child_age_visual_guidance"],
                "page_type": page_type,
                "target_aspect_ratio": target_aspect_ratio,
                "current_page_image_prompt": image_prompt,
            },
        )

    @staticmethod
    def _render_story_plan_prompt(
        template: str,
        *,
        story: Story,
        child: Any,
        source_inputs: dict[str, str],
        theme: str,
        hobby: str,
        pages: int,
        character_context: dict[str, str],
    ) -> str:
        """Render the story planner template for first attempt and retry."""
        character_profile = StoryService._build_story_planner_character_profile(child, character_context)
        return render_prompt(
            template,
            {
                "age_group": story.age_group.value,
                "first_name": child.first_name or "Child",
                "gender": child.gender or "neutral",
                "theme": _safe_prompt_value(theme),
                "hobby": hobby,
                "learning_goal": _safe_prompt_value(source_inputs["learning_goal"]),
                "story_context": _safe_prompt_value(source_inputs["context"], "none"),
                "moral": "kindness and courage",
                "pages": pages,
                "custom_character": False,
                "character_profile_json": character_profile,
                "character_profile": character_profile,
                "character_description": character_context["character_description"],
            },
        )

    @staticmethod
    def _build_story_planner_character_profile(child: Any, character_context: dict[str, str]) -> dict[str, Any]:
        """Build the character-profile input expected by the new planner prompt."""
        metadata = child.character_metadata if isinstance(child.character_metadata, dict) else {}
        return {
            "age": child.age,
            "gender": child.gender or "",
            "name": child.first_name or "Child",
            "profile_summary": character_context["character_description"],
            "child_age_label": character_context["child_age_label"],
            "age_visual_guidance": character_context["child_age_visual_guidance"],
            "generated_character": {
                "image_url": child.character_image_url or "",
                "description": metadata.get("description") or character_context["character_description"],
                "style": metadata.get("style") or "premium semi-realistic 3D storybook",
            },
        }

    @staticmethod
    def _build_story_generation_context(story_plan: dict[str, Any]) -> dict[str, Any]:
        """Build a compact story-plan context with only fields needed for narration."""
        def text(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""

        pages = []
        for page in story_plan.get("pages", []):
            if not isinstance(page, dict):
                continue

            characters_present = page.get("characters_present")
            if isinstance(characters_present, list):
                characters_present = [
                    character
                    for character in characters_present
                    if isinstance(character, str) and character.strip()
                ]
            else:
                characters_present = []

            pages.append(
                {
                    "page_number": page.get("page_number"),
                    "story_role": text(page.get("story_role")),
                    "scene_description": text(page.get("scene_description")),
                    "characters_present": characters_present,
                    "emotional_beat": text(page.get("emotional_beat")),
                    "learning_goal_integration": text(page.get("learning_goal_integration")),
                    "continuity_requirements": page.get("continuity_requirements")
                    if isinstance(page.get("continuity_requirements"), list)
                    else [],
                }
            )

        return {
            "title": text(story_plan.get("title")),
            "summary": text(story_plan.get("summary")),
            "theme": text(story_plan.get("theme")),
            "learning_goal": text(story_plan.get("learning_goal")),
            "moral_theme": text(story_plan.get("moral_theme")),
            "setting": text(story_plan.get("setting")),
            "tone": text(story_plan.get("tone")),
            "visual_bible": story_plan.get("visual_bible") if isinstance(story_plan.get("visual_bible"), dict) else {},
            "pages": pages,
        }

    @staticmethod
    def _build_image_plan_context(
        story_plan: dict[str, Any],
        story_json: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build a compact prompt context with only fields needed for image planning."""
        def text(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""

        compact_plan_pages = []
        for page in story_plan.get("pages", []):
            if not isinstance(page, dict):
                continue

            characters_present = page.get("characters_present")
            if isinstance(characters_present, list):
                characters_present = [
                    character
                    for character in characters_present
                    if isinstance(character, str) and character.strip()
                ]
            else:
                characters_present = []

            continuity_requirements = page.get("continuity_requirements")
            if not isinstance(continuity_requirements, list):
                continuity_requirements = []

            compact_plan_pages.append(
                {
                    "page_number": page.get("page_number"),
                    "story_role": text(page.get("story_role")),
                    "scene_description": text(page.get("scene_description")),
                    "characters_present": characters_present,
                    "child_action": text(page.get("child_action")),
                    "emotional_beat": text(page.get("emotional_beat")),
                    "continuity_requirements": continuity_requirements,
                }
            )

        compact_story_plan = {
            "title": text(story_plan.get("title")),
            "setting": text(story_plan.get("setting")),
            "tone": text(story_plan.get("tone")),
            "visual_bible": story_plan.get("visual_bible") if isinstance(story_plan.get("visual_bible"), dict) else {},
            "pages": compact_plan_pages,
        }

        compact_story_json = {
            "pages": [
                {
                    "page_number": page.get("page_number"),
                    "emotion": text(page.get("emotion")),
                    "text": text(page.get("text")),
                }
                for page in story_json.get("pages", [])
                if isinstance(page, dict)
            ],
        }

        return compact_story_plan, compact_story_json

    @staticmethod
    async def _load_image_as_base64(url_or_path: str) -> str:
        """Resolve a media image URL/path and return raw base64 image data."""
        image_bytes = await get_image_storage_service().get_image_bytes(url_or_path)
        if not image_bytes:
            raise AppException("Image file is empty", code="EMPTY_IMAGE")
        return base64.standard_b64encode(image_bytes).decode("utf-8")

    @staticmethod
    def _set_story_json_page_image_url(story_json: dict[str, Any], page_number: int, image_url: str) -> None:
        pages = story_json.get("pages")
        if not isinstance(pages, list):
            return
        for page in pages:
            if isinstance(page, dict) and page.get("page_number") == page_number:
                page["image_url"] = image_url
                return

    async def _create_pages_without_images(self, story: Story, story_json: dict[str, Any]) -> None:
        """Create story pages without images (for testing)."""
        pages = story_json.get("pages", [])
        for idx, page in enumerate(pages):
            await self.story_pages.upsert_page(
                story.id,
                page_number=idx + 1,
                page_type="page",
                text=page.get("text", ""),
                image_prompt=None,
                image_url=None,
            )

    @staticmethod
    def _get_age_group_from_dob(dob) -> str:
        """Calculate age group from date of birth."""
        if not dob:
            return "5-7"  # Default to early reader if no DOB

        from datetime import date
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        if age < 5:
            return "2-4"
        elif age < 8:
            return "5-7"
        else:
            return "8-12"

    @staticmethod
    def _get_page_count_for_age_group(age_group: str) -> int:
        """Get recommended page count for age group."""
        age_counts = {
            "2-4": 6,
            "5-7": 10,
            "8-12": 12,
        }
        return age_counts.get(age_group, 10)

    @staticmethod
    def _get_hobby_for_age_group(age_group: str) -> str:
        """Get age-appropriate hobby/interest suggestions."""
        hobbies = {
            "2-4": "playing with toys, exploring, drawing, singing",
            "5-7": "reading, drawing, building with blocks, playing games, riding bikes",
            "8-12": "reading, creating art, sports, music, science experiments, video games",
        }
        return hobbies.get(age_group, "creative hobbies and exploration")

    @staticmethod
    def _child_age_label(child) -> str:
        return f"{child.age} years old" if child and child.age is not None else "the child's profile age"

    @staticmethod
    def _age_visual_guidance(age: int | None) -> str:
        if age is None:
            return "age-appropriate child height, body build, hands, feet, limbs, and facial maturity"
        if age <= 4:
            return (
                "toddler/preschool-age proportions: short child height, soft round cheeks, small hands and feet, "
                "short child limbs, and very young child facial proportions"
            )
        if age <= 7:
            return (
                "early-reader child proportions: childlike height, rounded cheeks, natural child hands and feet, "
                "and young child facial proportions"
            )
        if age <= 12:
            return (
                "older child proportions: age-appropriate height, natural child build, childlike facial maturity, "
                "and no teenage features"
            )
        return "teen-appropriate but still child-safe proportions, matching the profile photo and not adult features"

    @staticmethod
    def _build_character_reference_context(child) -> dict[str, str]:
        return {
            "character_description": StoryService._extract_character_analysis(child),
            "child_age_label": StoryService._child_age_label(child),
            "child_age_visual_guidance": StoryService._age_visual_guidance(child.age if child else None),
        }

    @staticmethod
    def _extract_character_analysis(child) -> str:
        """Extract detailed character analysis from child profile for visual consistency."""
        age_str = f"{child.age} years old" if child.age else "child"

        if not child.character_metadata:
            return f"A friendly {age_str} child named {child.first_name} ready for adventure."

        metadata = child.character_metadata

        # Priority: Use analysis_text (detailed visual analysis) over simple description
        analysis_text = metadata.get("analysis_text", "")
        description = metadata.get("description", "")

        # Build comprehensive character profile for image anchor consistency
        parts = []

        # Add age and name as header
        parts.append(f"Age: {age_str}")
        parts.append(f"Name: {child.first_name}")
        parts.append(f"Age Appearance Guidance: {StoryService._age_visual_guidance(child.age)}")

        # Use analysis_text if available (detailed visual analysis from character generation)
        if analysis_text:
            parts.append(f"Visual Analysis:\n{analysis_text}")
        # Fall back to simple description if analysis_text not available
        elif description:
            parts.append(f"Description: {description}")

        # Add visual generation style if available
        if metadata.get("generation_model"):
            parts.append(f"Generated in: {metadata.get('generation_model')} style")

        # Add specific visual anchors for consistency
        parts.append(
            "\nVISUAL ANCHOR (keep consistent in EVERY page illustration):\n"
            "- Same face, facial proportions, hair color, hairstyle, eyes, nose, smile, cheeks, skin tone, and age appearance throughout the story\n"
            "- Same body proportions and developmental stage throughout the story\n"
            "- Clothing may change only once as a story outfit, then must remain identical across every image\n"
            "- Use the generated character_image_url as the master visual reference for the hero character"
        )

        return "\n".join(parts) if parts else f"A friendly {age_str} child named {child.first_name}."

    async def get_story(self, user_id: UUID, story_id: UUID) -> StoryResponse:
        """Retrieve story with ownership validation."""
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found")

        # Manually construct response to avoid lazy-loading pages relationship
        pages = [
            StoryPageResponse(
                id=page.id,
                page_number=page.page_number,
                page_type=page.page_type,
                text=page.text,
                image_prompt=page.image_prompt,
                image_url=page.image_url,
            )
            for page in story.pages
        ]

        return StoryResponse(
            id=story.id,
            title=story.title,
            moral=story.moral,
            summary=story.summary,
            status=story.status.value,
            current_step=story.current_step,
            generation_mode=story.generation_mode.value,
            age_group=story.age_group.value,
            category=story.category,
            learning_goal=story.learning_goal,
            context=story.context,
            pages=pages,
            created_at=story.created_at,
            updated_at=story.updated_at,
        )

    async def get_story_status(self, user_id: UUID, story_id: UUID) -> StoryStatusResponse:
        """Retrieve lightweight status fields from the stories table."""
        row = await self.stories.get_status_for_user(user_id, story_id)
        if row is None:
            raise NotFoundException("Story not found")

        status = row.status.value if hasattr(row.status, "value") else str(row.status)
        return StoryStatusResponse(
            story_id=row.id,
            status=status,
            current_step=row.current_step,
            error_message=row.error_message,
            updated_at=row.updated_at,
        )

    async def get_story_content(
        self,
        user_id: UUID,
        story_id: UUID,
        language: str = DEFAULT_STORY_LANGUAGE,
    ) -> StoryContentResponse:
        """Retrieve a custom story's language-specific story JSON."""
        normalized_language = language.strip().lower()
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found", "STORY_NOT_FOUND")

        content = await self.stories.get_content_by_story_and_language(
            story_id=story.id,
            language=normalized_language,
        )

        if content is None:
            raise NotFoundException("Story content not found", "STORY_CONTENT_NOT_FOUND")

        return StoryContentResponse(
            story_id=story.id,
            story_type="custom",
            language=str(content.language),
            story_json=content.story_json,
        )

    async def list_stories(
        self,
        user_id: UUID,
        child_id: UUID | None = None,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse[StoryResponse]:
        """List user's stories, optionally filtered by child."""
        stories, total = await self.stories.list_by_user_paginated(
            user_id,
            child_id,
            page=page,
            page_size=page_size,
        )
        results = []
        for story in stories:
            pages = [
                StoryPageResponse(
                    id=page.id,
                    page_number=page.page_number,
                    page_type=page.page_type,
                    text=page.text,
                    image_prompt=page.image_prompt,
                    image_url=page.image_url,
                )
                for page in story.pages
            ]
            results.append(
                StoryResponse(
                    id=story.id,
                    title=story.title,
                    moral=story.moral,
                    summary=story.summary,
                    status=story.status.value,
                    current_step=story.current_step,
                    generation_mode=story.generation_mode.value,
                    age_group=story.age_group.value,
                    category=story.category,
                    learning_goal=story.learning_goal,
                    context=story.context,
                    pages=pages,
                    created_at=story.created_at,
                    updated_at=story.updated_at,
                )
            )
        return PaginatedResponse[StoryResponse].create(
            items=results,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_story_steps(self, user_id: UUID, story_id: UUID) -> list[StoryStepResponse]:
        """Retrieve audit trail for story."""
        # Verify ownership
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found")

        steps = await self.story_steps.list_by_story(story_id)
        return [
            StoryStepResponse(
                id=step.id,
                step_name=step.step_name.value if hasattr(step.step_name, "value") else str(step.step_name),
                status=step.status.value if hasattr(step.status, "value") else str(step.status),
                retry_count=step.retry_count,
                error_message=step.error_message,
                usage=self._step_usage(step.response),
                started_at=step.started_at,
                completed_at=step.completed_at,
                created_at=step.created_at,
            )
            for step in steps
        ]

    @staticmethod
    def _step_usage(response: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(response, dict):
            return None
        usage = response.get("_workflow_usage")
        return usage if isinstance(usage, dict) else None
