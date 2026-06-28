import base64
from io import BytesIO
import copy
import json
import logging
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.age_groups import (
    AGE_GROUP_0_3,
    AGE_GROUP_3_6,
    AGE_GROUP_6_9,
    DEFAULT_AGE_GROUP,
    age_group_label,
    normalize_age_group,
    page_count_for_age_group,
)
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
from app.service.image_webp_converter import ImageWebPConverter
from app.service.plan_validator import PlanValidator
from app.service.image_plan_validator import ImagePlanValidator
from app.service.story_input_safety_service import StoryInputSafetyService
from app.service.story_completion_email_service import StoryCompletionEmailService
from app.service.story_narration_service import StoryNarrationService
from app.service.story_narration_profile import build_page_narration, normalize_page_emotion
from app.utils.prompt_loader import load_prompt, render_prompt

logger = logging.getLogger(__name__)

DEFAULT_STORY_LANGUAGE = "en"
SUPPORTED_STORY_LANGUAGES = ("en", "hi", "mr")
STORY_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
}
STORY_LANGUAGE_VARIANTS_KEY = "language_variants"


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


def _repair_json_from_llm(text: str) -> str:
    """Repair common JSON parsing errors from LLM output.

    Handles markdown wrapping, unterminated strings, unescaped quotes, and other issues.
    Uses multiple repair strategies to handle various malformations.
    """
    if not text or not isinstance(text, str):
        return text

    text = text.strip()
    if not text:
        return text

    # Step 1: Remove markdown code block wrappers
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    text = text.strip()

    # Step 2: Try to parse as-is first
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Step 3: Fix unescaped quotes within string values
    # Pattern: "key": "value with "internal" quotes"
    # Need to escape internal quotes
    result = []
    in_string = False
    i = 0

    while i < len(text):
        char = text[i]

        if char == '"':
            # Check if it's escaped
            if i > 0 and text[i-1] == '\\':
                # Already escaped
                result.append(char)
            else:
                # Check context to see if this should be escaped
                # If we're inside a string and encounter another unescaped quote
                if in_string:
                    # Look ahead to see if this looks like an unterminated quote
                    # (i.e., followed by non-quote characters, not a closing bracket/brace/comma)
                    remaining = text[i+1:].lstrip() if i+1 < len(text) else ""
                    if remaining and not remaining[0] in (',' , '}', ']', ':'):
                        # This looks like an unescaped quote inside a string - escape it
                        result.append('\\"')
                        i += 1
                        continue
                    else:
                        # This is probably a closing quote
                        in_string = False
                else:
                    # Opening quote
                    in_string = True

                result.append(char)
        else:
            result.append(char)

        i += 1

    text = ''.join(result)

    # Step 4: Ensure all arrays and objects are closed
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    if open_braces > 0:
        text += '}' * open_braces
    if open_brackets > 0:
        text += ']' * open_brackets

    # Step 5: Try parsing again
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Step 6: Find and close unterminated strings at the end
    # This is a last resort - find the last quote and see if it's closed
    if text.count('"') % 2 != 0:
        # Odd number of quotes
        # Find the position of the last unclosed quote
        last_open_quote = -1
        i = 0
        while i < len(text):
            if text[i] == '"' and (i == 0 or text[i-1] != '\\'):
                last_open_quote = i
            i += 1

        # If we found an unclosed quote, close it
        if last_open_quote != -1:
            # Check if there's content after the last quote
            after_quote = text[last_open_quote + 1:]
            # Find a good place to close the quote
            # Look for the next structural character or end of string
            close_pos = len(text)
            for j, char in enumerate(after_quote):
                if char in (',', ']', '}', '\n'):
                    close_pos = last_open_quote + 1 + j
                    break

            # Insert closing quote
            text = text[:close_pos] + '"' + text[close_pos:]

    return text


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


def _story_source_inputs(story: Any) -> dict[str, str]:
    """Canonical story-driving inputs used by plan, story, and image prompts."""
    return {
        "category": getattr(story, "category", None) or getattr(story, "event_description", None) or "adventure",
        "learning_goal": getattr(story, "learning_goal", None) or "personal growth",
        "context": getattr(story, "context", None) or "",
    }


PLANNING_PREFERENCE_KEYS = (
    "story_seed",
    "protagonist_preference",
    "protagonist_avoid",
    "setting_preference",
    "tone_preference",
    "conflict_preference",
    "moral_preference",
    "visual_preference",
    "title_preference",
    "cultural_context",
    "avoid_elements",
)


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


def _story_moral_page(raw_moral: Any, *, page_number: int, fallback: Any = None) -> dict[str, Any]:
    moral_text = raw_moral.get("text") if isinstance(raw_moral, dict) else raw_moral
    text = _first_non_empty(moral_text, fallback) or ""
    moral_page: dict[str, Any] = {"page_number": page_number, "text": text}
    if isinstance(raw_moral, dict):
        speech_narration = raw_moral.get("speech_narration")
        if isinstance(speech_narration, dict):
            moral_page["speech_narration"] = speech_narration
    return moral_page


def _normalize_story_languages(story: Any, *, default: list[str] | None = None) -> list[str]:
    raw: Any = getattr(story, "languages", None)
    if not raw:
        input_request = getattr(story, "input_request", None)
        if isinstance(input_request, dict):
            raw = input_request.get("languages") or input_request.get("language")
    if not raw:
        raw = getattr(story, "language", None)
    if not raw:
        raw = default or [DEFAULT_STORY_LANGUAGE]
    if isinstance(raw, str):
        raw = [raw]
    languages: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        language = str(item or "").strip().lower()
        if language in SUPPORTED_STORY_LANGUAGES and language not in languages:
            languages.append(language)
    return languages or list(default or [DEFAULT_STORY_LANGUAGE])


def _localized_text_map(
    value: Any,
    languages: list[str],
    *,
    label: str,
    fallback: Any = None,
    allow_empty_single_language: bool = False,
) -> dict[str, str]:
    source = value
    if isinstance(source, dict) and "text" in source:
        source = source.get("text")
    if isinstance(source, dict):
        localized: dict[str, str] = {}
        for language in languages:
            text = _first_non_empty(source.get(language))
            if not text:
                raise AppException(
                    f"Story generation missing {label} for language '{language}'",
                    code="STORY_LANGUAGE_TEXT_MISSING",
                )
            localized[language] = text
        return localized
    text = _first_non_empty(source, fallback)
    if text and len(languages) == 1:
        return {languages[0]: text}
    if allow_empty_single_language and len(languages) == 1:
        return {languages[0]: ""}
    raise AppException(
        f"Story generation must return localized {label} for languages: {', '.join(languages)}",
        code="STORY_LANGUAGE_TEXT_MISSING",
    )


def _validate_devanagari_language(language: str, text_values: list[str]) -> None:
    if language not in {"hi", "mr"}:
        return
    combined = " ".join(text_values)
    if not re.search(r"[\u0900-\u097F]", combined):
        raise AppException(
            f"Story generation returned {language} text without Devanagari script",
            code="STORY_LANGUAGE_SCRIPT_INVALID",
        )


def _normalize_story_output(raw_story_json: dict[str, Any], plan: dict[str, Any], story: Story) -> dict[str, Any]:
    """Coerce LLM story output into the canonical story_json contract."""
    source_inputs = plan.get("source_inputs") or _story_source_inputs(story)
    raw_moral = raw_story_json.get("moral")
    languages = _normalize_story_languages(story, default=plan.get("selected_languages") if isinstance(plan, dict) else None)
    primary_language = languages[0]
    expected_page_numbers = [
        int(page.get("page_number"))
        for page in (plan.get("pages") or [])
        if isinstance(page, dict) and page.get("page_number") is not None
    ]

    title_map = _localized_text_map(
        raw_story_json.get("title"),
        languages,
        label="title",
        fallback=plan.get("title") or "Untitled",
    )
    summary_map = _localized_text_map(
        raw_story_json.get("summary"),
        languages,
        label="summary",
        fallback=plan.get("summary") or "",
        allow_empty_single_language=True,
    )
    moral_map = _localized_text_map(
        raw_moral,
        languages,
        label="moral",
        fallback=_first_non_empty(raw_story_json.get("moral_theme"), plan.get("moral_theme")),
    )

    pages_by_language: dict[str, list[dict[str, Any]]] = {language: [] for language in languages}
    for idx, page in enumerate(raw_story_json.get("pages") or []):
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or (expected_page_numbers[idx] if idx < len(expected_page_numbers) else idx + 1))
        text_map = _localized_text_map(
            page.get("text"),
            languages,
            label=f"page {page_number} text",
            fallback=page.get("narration_sample"),
        )
        emotion = normalize_page_emotion(page.get("emotion"))
        for language in languages:
            pages_by_language[language].append(
                {
                    "page_number": page_number,
                    "text": text_map[language],
                    "emotion": emotion,
                    "narration": build_page_narration(emotion, story.age_group),
                }
            )

    pages = pages_by_language[primary_language]
    for language, language_pages in pages_by_language.items():
        if not language_pages:
            raise AppException("Story generation returned no valid pages", code="INVALID_STORY_JSON")
        _validate_devanagari_language(
            language,
            [title_map[language], summary_map[language], moral_map[language]]
            + [str(page.get("text") or "") for page in language_pages],
        )

    received_page_numbers = [int(page.get("page_number") or 0) for page in pages]
    expected_page_count = len(expected_page_numbers)
    if expected_page_count and len(pages) != expected_page_count:
        raise AppException(
            f"Story generation returned {len(pages)} pages; expected {expected_page_count}",
            code="STORY_PAGE_COUNT_MISMATCH",
        )
    if expected_page_numbers and received_page_numbers != expected_page_numbers:
        raise AppException(
            f"Story generation returned page numbers {received_page_numbers}; expected {expected_page_numbers}",
            code="STORY_PAGE_COUNT_MISMATCH",
        )

    theme = _first_non_empty(
        raw_story_json.get("theme"),
        plan.get("moral_theme"),
        source_inputs.get("category") if isinstance(source_inputs, dict) else None,
        story.category,
        "adventure",
    )
    art_style = _first_non_empty(
        raw_story_json.get("art_style"),
        plan.get("global_visual_style"),
        "",
    ) or ""
    variants: dict[str, dict[str, Any]] = {}
    for language in languages:
        language_pages = copy.deepcopy(pages_by_language[language])
        variants[language] = {
            "title": title_map[language],
            "theme": theme,
            "art_style": art_style,
            "summary": summary_map[language],
            "pages": language_pages,
            "moral": _story_moral_page(
                moral_map[language],
                page_number=len(language_pages) + 1,
                fallback=plan.get("moral_theme"),
            ),
            "language": language,
        }

    primary = copy.deepcopy(variants[primary_language])
    primary["languages"] = languages
    primary[STORY_LANGUAGE_VARIANTS_KEY] = variants
    return primary


def _story_json_language_variant(story_json: dict[str, Any], language: str) -> dict[str, Any]:
    variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
    if isinstance(variants, dict) and isinstance(variants.get(language), dict):
        return copy.deepcopy(variants[language])
    return copy.deepcopy({key: value for key, value in story_json.items() if key != STORY_LANGUAGE_VARIANTS_KEY})


def _set_story_json_language_variant(story_json: dict[str, Any], language: str, language_story_json: dict[str, Any]) -> None:
    variants = story_json.setdefault(STORY_LANGUAGE_VARIANTS_KEY, {})
    clean_variant = copy.deepcopy({key: value for key, value in language_story_json.items() if key != STORY_LANGUAGE_VARIANTS_KEY})
    clean_variant["language"] = language
    variants[language] = clean_variant
    primary_language = (story_json.get("languages") or [story_json.get("language") or DEFAULT_STORY_LANGUAGE])[0]
    if language == primary_language:
        for key, value in clean_variant.items():
            story_json[key] = copy.deepcopy(value)
        story_json[STORY_LANGUAGE_VARIANTS_KEY] = variants


def _sync_story_media_to_language_variants(story_json: dict[str, Any], *, include_audio: bool = False) -> dict[str, Any]:
    variants = story_json.get(STORY_LANGUAGE_VARIANTS_KEY)
    if not isinstance(variants, dict):
        return story_json
    shared_top_keys = ("cover_image_url", "back_cover_image_url")
    for language, variant in list(variants.items()):
        if not isinstance(variant, dict):
            continue
        for key in shared_top_keys:
            if story_json.get(key):
                variant[key] = story_json.get(key)
        base_pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        variant_pages = variant.get("pages") if isinstance(variant.get("pages"), list) else []
        by_number = {
            int(page.get("page_number") or idx + 1): page
            for idx, page in enumerate(variant_pages)
            if isinstance(page, dict)
        }
        for idx, base_page in enumerate(base_pages):
            if not isinstance(base_page, dict):
                continue
            page_number = int(base_page.get("page_number") or idx + 1)
            variant_page = by_number.get(page_number)
            if not isinstance(variant_page, dict):
                continue
            for key in ("image_url", "planned_image_prompt", "image_prompt"):
                if base_page.get(key):
                    variant_page[key] = base_page.get(key)
            if include_audio and language == (story_json.get("language") or DEFAULT_STORY_LANGUAGE):
                for key in ("tts_prompt", "tts_skipped", "tts_model", "tts_voice", "audio_url", "duration", "word_timestamps"):
                    if key in base_page:
                        variant_page[key] = copy.deepcopy(base_page[key])
    return story_json


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

    CAST_MODE_CHILD_HERO = "CHILD_HERO"
    CAST_MODE_IMAGINED = "IMAGINED_CAST"
    MAX_RETRIES = 3
    PLAN_MAX_TOKENS = 14000
    STORY_MAX_TOKENS_BY_AGE = {
        AGE_GROUP_0_3: 9000,
        AGE_GROUP_3_6: 10000,
        AGE_GROUP_6_9: 12000,
    }
    IMAGE_PLAN_MAX_TOKENS_BY_AGE = {
        AGE_GROUP_0_3: 16000,
        AGE_GROUP_3_6: 22000,
        AGE_GROUP_6_9: 28000,
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
        reference_image_model = story.reference_image_model
        if reference_image_model == "gemini-2.5-flash-image":
            reference_image_model = settings.GOOGLE_REFERENCE_IMAGE_MODEL
        if reference_image_model:
            kwargs["model"] = reference_image_model
            kwargs["reference_image_model"] = reference_image_model
        if story.image_model:
            kwargs["image_model"] = story.image_model
        return kwargs

    @classmethod
    def _story_max_tokens(cls, age_group: str) -> int:
        value = normalize_age_group(age_group)
        return cls.STORY_MAX_TOKENS_BY_AGE.get(value, cls.STORY_MAX_TOKENS_BY_AGE[DEFAULT_AGE_GROUP])

    @classmethod
    def _story_max_tokens_for_languages(cls, age_group: str, languages: list[str] | None = None) -> int:
        base = cls._story_max_tokens(age_group)
        multiplier = max(1, len(languages or [DEFAULT_STORY_LANGUAGE]))
        return min(30000, base * multiplier)

    @classmethod
    def _image_plan_max_tokens(cls, age_group: str) -> int:
        value = normalize_age_group(age_group)
        return cls.IMAGE_PLAN_MAX_TOKENS_BY_AGE.get(value, cls.IMAGE_PLAN_MAX_TOKENS_BY_AGE[DEFAULT_AGE_GROUP])

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
            generation_mode="INPUT_DRIVEN",
            age_group=age_group,
            category=payload.category,
            learning_goal=payload.learning_goal,
            context=payload.context,
            event_description=None,
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
            video_created=bool(story.video_created),
            video_metadata=story.video_metadata,
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
                input_request = story.input_request or {}
                story_json["use_child_character"] = input_request.get("use_child_character", False)
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

            if not flags.skip_image_generation:
                image_plan = await self._ensure_image_plan_character_references(story, image_plan)
                story.image_plan_json = image_plan
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
                age_group=story.age_group.value,
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
                getattr(story, "event_description", None),
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

        # Load the cast-mode-specific planner prompt so the LLM receives no contradictory hero rules.
        template = load_prompt(self._story_plan_prompt_path(story))

        # Prepare variables
        pages = self._get_page_count_for_age_group(story.age_group)
        source_inputs = _story_source_inputs(story)
        theme = source_inputs["category"]

        # Generate better hobby suggestions based on age group
        hobby = self._get_hobby_for_age_group(story.age_group)

        character_context = self._build_story_cast_context(story, child)

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
            try:
                result = await self.ai_provider.generate_text(
                    prompt,
                    max_tokens=self.PLAN_MAX_TOKENS,
                    temperature=0.4,
                    step_name=StoryStepName.STORY_PLAN_GENERATION.value,
                    response_format={"type": "json_object"},
                )
            except AppException as exc:
                if not self._is_google_prompt_safety_block(exc):
                    raise
                fallback_prompt = self._story_plan_fallback_prompt(
                    story=story,
                    child=child,
                    source_inputs=source_inputs,
                    pages=pages,
                    character_context=character_context,
                )
                step.prompt = fallback_prompt
                await self.story_steps.update(step)
                await self.session.commit()
                logger.warning(
                    "Story %s: Google blocked story plan prompt; retrying once with compact safe planner prompt.",
                    story.id,
                )
                result = await self.ai_provider.generate_text(
                    fallback_prompt,
                    max_tokens=self.PLAN_MAX_TOKENS,
                    temperature=0.35,
                    step_name=StoryStepName.STORY_PLAN_GENERATION.value,
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
        selected_child_name = await self._selected_child_name_for_plan_validation(story)

        for attempt in range(1, self.MAX_RETRIES + 1):
            step.retry_count = attempt - 1

            # Validate
            result = self.plan_validator.validate(
                plan,
                age_group=story.age_group,
                source_inputs=_story_source_inputs(story),
                cast_mode=self._cast_mode(story),
                selected_child_name=selected_child_name,
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
            cast_mode=self._cast_mode(story),
            selected_child_name=selected_child_name,
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
        template = load_prompt(self._story_plan_prompt_path(story))

        pages = self._get_page_count_for_age_group(story.age_group)
        source_inputs = _story_source_inputs(story)
        theme = source_inputs["category"]
        hobby = self._get_hobby_for_age_group(story.age_group)
        character_context = self._build_story_cast_context(story, child)

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
            step_name=StoryStepName.STORY_PLAN_GENERATION.value,
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

        template = load_prompt(self._story_generation_prompt_path(story))
        prompt_plan = self._build_story_generation_context(plan, languages=_normalize_story_languages(story))

        # Convert to compact JSON - this ensures proper escaping of special characters
        try:
            plan_json_str = _compact_json(prompt_plan)
        except (TypeError, ValueError) as e:
            logger.error("Failed to serialize story plan to JSON: %s", str(e))
            raise AppException(f"Failed to serialize story plan: {str(e)}", code="STORY_PLAN_SERIALIZATION_ERROR")

        prompt = template.replace("{story_plan_json}", plan_json_str)
        step.prompt = prompt
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            try:
                result = await self.ai_provider.generate_text(
                    prompt,
                    max_tokens=self._story_max_tokens_for_languages(story.age_group, _normalize_story_languages(story)),
                    temperature=0.7,
                    step_name=StoryStepName.STORY_GENERATION.value,
                    response_format={"type": "json_object"},
                )
            except AppException as exc:
                if not self._is_google_prompt_safety_block(exc):
                    raise
                fallback_prompt = self._story_generation_fallback_prompt(prompt_plan)
                step.prompt = fallback_prompt
                await self.story_steps.update(step)
                await self.session.commit()
                logger.warning(
                    "Story %s: Google blocked story generation prompt; retrying once with compact gentle prompt.",
                    story.id,
                )
                result = await self.ai_provider.generate_text(
                    fallback_prompt,
                    max_tokens=self._story_max_tokens_for_languages(story.age_group, _normalize_story_languages(story)),
                    temperature=0.45,
                    step_name=StoryStepName.STORY_GENERATION.value,
                    response_format={"type": "json_object"},
                )

            generation_result = result
            try:
                # Repair common JSON issues before parsing
                cleaned_json = _repair_json_from_llm(result.text)
                raw_story_json = json.loads(cleaned_json)
            except json.JSONDecodeError as e:
                repaired_json = await self._repair_story_generation_json_response(
                    result.text,
                    e,
                    story,
                    expected_page_count=len(plan.get("pages") or []),
                )
                if repaired_json is not None:
                    cleaned_json = repaired_json
                    raw_story_json = json.loads(cleaned_json)
                    logger.warning(
                        "Story %s: recovered malformed story generation JSON using repair LLM call",
                        story.id,
                    )
                else:
                    # Log detailed error information for debugging
                    logger.error(
                        "Failed to parse story JSON after repair attempt. Error: %s at line %s column %s",
                        str(e),
                        e.lineno,
                        e.colno,
                    )
                    logger.error(
                        "Original LLM response length: %s chars",
                        len(result.text),
                    )
                    logger.error(
                        "Original response (first 500 chars): %s",
                        result.text[:500],
                    )
                    logger.error(
                        "Cleaned response (first 500 chars): %s",
                        cleaned_json[:500],
                    )

                    # Try to show the problematic area
                    if hasattr(e, 'pos') and e.pos is not None:
                        start = max(0, e.pos - 50)
                        end = min(len(cleaned_json), e.pos + 50)
                        logger.error(
                            "Context around error (chars %s-%s): ...%s[ERROR HERE]%s...",
                            start,
                            end,
                            cleaned_json[start:e.pos],
                            cleaned_json[e.pos:end],
                        )

                    raise AppException(
                        f"Invalid JSON from story generation: {str(e)} at line {e.lineno}, column {e.colno}. "
                        f"The LLM response contains malformed JSON that couldn't be automatically repaired.",
                        code="INVALID_LLM_JSON",
                    )

            try:
                story_json = _normalize_story_output(raw_story_json, plan, story)
            except AppException as exc:
                if exc.code != "STORY_PAGE_COUNT_MISMATCH":
                    raise
                retry_result = await self._retry_story_generation_for_page_count(
                    prompt_plan,
                    story,
                    original_error=exc.message,
                )
                generation_result = retry_result
                raw_story_json = self._parse_story_generation_text(retry_result.text)
                story_json = _normalize_story_output(raw_story_json, plan, story)

            step.response = _with_workflow_usage(
                story_json,
                _workflow_usage(generation_result, output_text=generation_result.text),
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

    async def _repair_story_generation_json_response(
        self,
        malformed_json: str,
        parse_error: json.JSONDecodeError,
        story: Story,
        *,
        expected_page_count: int,
    ) -> str | None:
        if settings.STORY_MOCK_LLM_RESPONSES:
            return None
        selected_languages = _normalize_story_languages(story)
        localized_empty = {language: "" for language in selected_languages}

        prompt = f"""
You are a strict JSON repair tool.

Repair the malformed JSON below into one valid JSON object only.

Rules:
- Do not rewrite the story.
- Preserve exactly {expected_page_count} pages.
- Do not add, remove, collapse, summarize, or reorder pages.
- Do not change character names, plot events, wording, title, summary, or moral except the minimum required to make valid JSON.
- Escape quotes inside string values with backslash.
- Convert literal line breaks inside strings to \\n.
- Remove trailing commas if present.
- Close any incomplete string, object, or array only when required.
- Return ONLY the repaired JSON object. No markdown. No explanation.

Expected schema:
{{
  "title": {_compact_json(localized_empty)},
  "summary": {_compact_json(localized_empty)},
  "pages": [
    {{
      "page_number": 1,
      "emotion": "",
      "text": {_compact_json(localized_empty)}
    }}
  ],
  "moral": {_compact_json(localized_empty)}
}}

Original parse error:
line {parse_error.lineno}, column {parse_error.colno}: {parse_error.msg}

Malformed JSON:
{malformed_json}
""".strip()

        try:
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=self._story_max_tokens_for_languages(story.age_group, _normalize_story_languages(story)),
                temperature=0,
                step_name="STORY_GENERATION_JSON_REPAIR",
                response_format={"type": "json_object"},
                empty_response_retries=1,
            )
            repaired_json = _repair_json_from_llm(result.text)
            repaired_payload = json.loads(repaired_json)
            repaired_pages = repaired_payload.get("pages") if isinstance(repaired_payload, dict) else None
            repaired_page_count = len(repaired_pages) if isinstance(repaired_pages, list) else 0
            if expected_page_count and repaired_page_count != expected_page_count:
                logger.warning(
                    "Story %s: rejected repaired story JSON because page_count=%s expected=%s",
                    story.id,
                    repaired_page_count,
                    expected_page_count,
                )
                return None
            return repaired_json
        except Exception as exc:
            logger.warning(
                "Story %s: failed to repair malformed story generation JSON with LLM: %s",
                story.id,
                exc,
            )
            return None

    async def _retry_story_generation_for_page_count(
        self,
        prompt_plan: dict[str, Any],
        story: Story,
        *,
        original_error: str,
    ):
        expected_page_count = len(prompt_plan.get("pages") or [])
        selected_languages = [
            language
            for language in (prompt_plan.get("selected_languages") or [DEFAULT_STORY_LANGUAGE])
            if language in SUPPORTED_STORY_LANGUAGES
        ] or [DEFAULT_STORY_LANGUAGE]
        localized_empty = {language: "" for language in selected_languages}
        prompt = (
            "You are a children's picture-book writer. Regenerate the full story from the plan below.\n"
            f"The previous response failed validation: {original_error}.\n\n"
            f"Return valid JSON only with exactly {expected_page_count} page objects in pages.\n"
            "Do not return the schema example. Do not summarize pages. Do not collapse pages.\n"
            f"Every localized text object must contain exactly these language keys: {', '.join(selected_languages)}.\n"
            "Each page must have page_number, emotion, and localized text. Page numbers must follow the plan.\n"
            "Use the same title, hero, story spine, page count, setting, learning goal, and ending from the plan.\n\n"
            "Required JSON shape:\n"
            f'{{"title":{_compact_json(localized_empty)},"summary":{_compact_json(localized_empty)},'
            f'"pages":[{{"page_number":1,"emotion":"","text":{_compact_json(localized_empty)}}}],'
            f'"moral":{_compact_json(localized_empty)}}}\n\n'
            f"STORY PLAN JSON:\n{_compact_json(prompt_plan)}"
        )
        logger.warning(
            "Story %s: retrying story generation after page-count mismatch; expected_pages=%s",
            story.id,
            expected_page_count,
        )
        return await self.ai_provider.generate_text(
            prompt,
            max_tokens=self._story_max_tokens_for_languages(story.age_group, selected_languages),
            temperature=0.45,
            step_name=StoryStepName.STORY_GENERATION.value,
            response_format={"type": "json_object"},
        )

    @staticmethod
    def _parse_story_generation_text(text: str) -> dict[str, Any]:
        cleaned_json = _repair_json_from_llm(text)
        return json.loads(cleaned_json)

    @staticmethod
    def _is_google_prompt_safety_block(exc: AppException) -> bool:
        message = str(getattr(exc, "message", exc))
        return exc.code == "EMPTY_RESPONSE" and "PROHIBITED_CONTENT" in message

    @staticmethod
    def _story_generation_fallback_prompt(prompt_plan: dict[str, Any]) -> str:
        selected_languages = [
            language
            for language in (prompt_plan.get("selected_languages") or [DEFAULT_STORY_LANGUAGE])
            if language in SUPPORTED_STORY_LANGUAGES
        ] or [DEFAULT_STORY_LANGUAGE]
        localized_empty = {language: "" for language in selected_languages}
        return (
            "You are a warm children's picture-book writer.\n"
            "Write the story from the sanitized plan below. Keep the same title, hero, page count, setting, "
            "learning goal, and ending. Use gentle, practical community-care wording. Keep the concern meaningful "
            "but calm, hopeful, and restorative. Do not use intense consequence language.\n\n"
            f"Return only valid JSON with exactly these language keys: {', '.join(selected_languages)}.\n"
            "Use this exact shape:\n"
            f'{{"title":{_compact_json(localized_empty)},"summary":{_compact_json(localized_empty)},'
            f'"pages":[{{"page_number":1,"emotion":"","text":{_compact_json(localized_empty)}}}],'
            f'"moral":{_compact_json(localized_empty)}}}\n\n'
            f"SANITIZED PLAN JSON:\n{_compact_json(prompt_plan)}"
        )

    @staticmethod
    def _image_plan_generation_fallback_prompt(
        story_plan: dict[str, Any],
        story_json: dict[str, Any],
        character_context: dict[str, Any],
    ) -> str:
        use_child_character = bool(character_context.get("use_child_character", True))
        hero = story_plan.get("visual_bible", {}).get("hero", {}) if isinstance(story_plan, dict) else {}
        hero_id = "hero_child" if use_child_character else str(hero.get("character_id") or "").strip()
        hero_name = character_context.get("child_name", "Child")
        manifest = (
            [{"character_id": "hero_child", "name": "", "role": "hero_child"}]
            if use_child_character
            else []
        )
        mode_rule = (
            "Use hero_child as the selected child character_id whenever the hero appears."
            if use_child_character
            else "Do not use hero_child. Use the invented hero character_id from visual_bible.hero whenever the hero appears."
        )
        return (
            "You are a children's picture-book illustration planner. Return only valid JSON.\n"
            "Create a concise visual plan from the sanitized story inputs. Use warm, family-friendly, "
            "school-and-home-safe wording. Avoid sensitive negative phrasing; state positive visual requirements only.\n"
            "Keep character identities stable. If water play appears, choose modest covered family-friendly outfits.\n"
            "Story JSON is the source of truth for page count. Output pages.length must equal "
            "Story JSON expected_page_count exactly, and page_number values must match expected_page_numbers exactly. "
            "Do not skip, merge, collapse, add, or reorder pages.\n"
            f"{mode_rule}\n\n"
            "Return this exact JSON shape:\n"
            f'{{"visual_bible":{{"hero":{{"character_id":"{hero_id}","name":"","appearance":"","outfit":"","footwear":"","signature_item":""}},'
            '"companion":{"appearance":""},"recurring_characters":[]},'
            f'"character_reference_manifest":{_compact_json(manifest)},'
            '"cover":{"title_text":"","visual_focus":"","emotion":"","characters_present":[],"reference_character_ids":[],"image_prompt":""},'
            '"pages":[{"page_number":1,"story_role":"","visual_importance":"medium","emotion":"","scene_action":"","environment":"",'
            '"characters_present":[],"reference_character_ids":[],"image_prompt":""}],'
            '"back_cover":{"emotion":"","characters_present":[],"reference_character_ids":[],"image_prompt":""}}\n\n'
            f"Hero name: {hero_name}\n"
            f"Cast mode: {character_context.get('cast_mode', StoryService.CAST_MODE_CHILD_HERO)}\n"
            f"Character identity lock:\n{StoryService._format_prompt_character_identity_lock(character_context)}\n\n"
            f"STORY PLAN JSON:\n{_compact_json(story_plan)}\n\n"
            f"STORY JSON:\n{_compact_json(story_json)}"
        )

    @staticmethod
    def _is_custom_story_workflow_record(story: Any) -> bool:
        return story.__class__.__name__ == "CustomStoryWorkflow"

    @staticmethod
    def _custom_image_plan_identity_summary(character_context: dict[str, Any]) -> str:
        if not character_context.get("use_child_character", True):
            return (
                f"Cast mode: {StoryService.CAST_MODE_IMAGINED}\n"
                f"Hero name: {character_context.get('child_name', 'AI-created story hero')}\n"
                f"Age label: {character_context.get('child_age_label', '')}\n"
                "Plan stable names, roles, hair/head details, outfit, accessories, and color palette for every "
                "recurring character. The final image step will use the visual bible as the model sheet."
            )

        summary = str(character_context.get("identity_summary") or "").strip()
        if not summary:
            summary = str(character_context.get("character_description") or "").strip()
        summary = StoryService._story_planner_safe_profile_text(summary)
        summary = StoryService._replace_case_insensitive(summary, "skin tone", "overall coloring")
        summary = StoryService._replace_case_insensitive(summary, "body proportions", "age appearance")
        summary = StoryService._replace_case_insensitive(summary, "body", "appearance")
        return (
            f"Hero child name: {character_context.get('child_name', 'Child')}\n"
            f"Character id to use when visible: hero_child\n"
            f"Age label: {character_context.get('child_age_label', '')}\n"
            "Identity note for planning only: use broad face/head, eye, and hairstyle cues. The generated "
            "character portrait will be attached later as the visual identity reference for final images.\n"
            f"Safe identity summary: {summary[:800]}"
        )

    @staticmethod
    def _custom_safe_image_plan_prompt(
        story_plan: dict[str, Any],
        story_json: dict[str, Any],
        character_context: dict[str, Any],
    ) -> str:
        story_plan = StoryService._custom_safe_image_plan_context(story_plan)
        story_json = StoryService._custom_safe_image_plan_context(story_json)
        use_child_character = bool(character_context.get("use_child_character", True))
        plan_visual_bible = story_plan.get("visual_bible") if isinstance(story_plan.get("visual_bible"), dict) else {}
        plan_hero = plan_visual_bible.get("hero") if isinstance(plan_visual_bible.get("hero"), dict) else {}
        hero_id = "hero_child" if use_child_character else str(plan_hero.get("character_id") or "").strip()
        manifest = (
            [{"character_id": "hero_child", "name": "", "role": "hero_child"}]
            if use_child_character
            else []
        )
        cast_rules = (
            "- Use hero_child in reference_character_ids whenever the selected child appears.\n"
            "- The final image step will attach the selected child's character reference image when the child hero is visible.\n"
            if use_child_character
            else "- Do not use hero_child or the selected child profile.\n"
            "- Use the invented hero character_id from visual_bible.hero whenever the hero appears.\n"
            "- The final image step will use the Visual Bible as the model sheet for the invented cast.\n"
        )
        schema = {
            "visual_bible": {
                "hero": {
                    "character_id": hero_id,
                    "name": "",
                    "appearance": "",
                    "outfit": "",
                    "footwear": "",
                    "outfit_lock": "",
                    "body_scale_lock": "",
                    "relative_size": "",
                    "signature_item": "",
                },
                "companion": {"appearance": ""},
                "recurring_characters": [
                    {
                        "character_id": "",
                        "name": "",
                        "role": "",
                        "appearance": "",
                        "outfit": "",
                        "footwear": "",
                        "hair_lock": "",
                        "outfit_lock": "",
                        "body_scale_lock": "",
                        "relative_size": "",
                        "signature_item": "",
                    }
                ],
            },
            "character_reference_manifest": manifest,
            "cover": {
                "title_text": "",
                "visual_focus": "",
                "emotion": "",
                "characters_present": [],
                "reference_character_ids": [],
                "image_prompt": "",
            },
            "pages": [
                {
                    "page_number": 1,
                    "story_role": "",
                    "visual_importance": "low | medium | high | climax",
                    "emotion": "",
                    "scene_action": "",
                    "environment": "",
                    "characters_present": [],
                    "reference_character_ids": [],
                    "image_prompt": "",
                }
            ],
            "back_cover": {
                "emotion": "",
                "characters_present": [],
                "reference_character_ids": [],
                "image_prompt": "",
            },
        }
        return (
            "# ROLE\n"
            "You are a professional children's storybook illustration planner. Return STRICT VALID JSON only.\n\n"
            "## SAFE INPUTS\n"
            f"Story Plan JSON:\n{_compact_json(story_plan)}\n\n"
            f"Story JSON:\n{_compact_json(story_json)}\n\n"
            "Safe Character Planning Summary:\n"
            f"{StoryService._custom_image_plan_identity_summary(character_context)}\n\n"
            f"Hero Child Name: {character_context.get('child_name', 'Child')}\n"
            f"Cast Mode: {character_context.get('cast_mode', StoryService.CAST_MODE_CHILD_HERO)}\n\n"
            "## PLANNING RULES\n"
            "- Create a visual plan for cover, each page, and back cover.\n"
            "- Story JSON is the source of truth for page count.\n"
            "- Output pages.length must equal Story JSON expected_page_count exactly.\n"
            "- Output page_number values must match Story JSON expected_page_numbers exactly, in the same order.\n"
            "- Create exactly one image-plan page for each Story JSON page. Do not skip, merge, collapse, add, or reorder pages.\n"
            f"{cast_rules}"
            "- Identify recurring side characters and assign stable lowercase snake_case character_id values.\n"
            "- Include only visible recurring characters in characters_present and reference_character_ids.\n"
            "- Keep character names, face/head/body shape, hairstyles or body patterns, outfits, footwear, accessories, "
            "body scale, and color palettes stable across pages.\n"
            "- For every recurring character, fill visual_bible locks: appearance, outfit/body covering, footwear or "
            "anatomy-safe equivalent, hair/fur/body lock, outfit_lock, body_scale_lock, relative_size, and signature_item.\n"
            "- For every animal recurring character, include a stable anatomy lock in appearance/body_scale_lock: "
            "one head, one body, correct legs/wings/ears/horns, and exactly one tail for a tailed animal.\n"
            "- For the hero, fill visual_bible.hero outfit_lock, body_scale_lock, relative_size, and signature_item.\n"
            "- Lock exact footwear for the hero in visual_bible.hero.footwear and include it in visual_bible.hero.outfit.\n"
            "- Never leave shoes/footwear implied; choose one concrete footwear state and repeat it in every image_prompt.\n"
            "- If clothing has a motif, patch, logo, star, heart, flower, embroidery, or printed design, lock exact "
            'count and placement, for example "one red star centered on the chest".\n'
            "- For repeating fabric patterns such as dots, stripes, or polka dots, describe the stable garment-wide "
            'pattern, for example "small blue polka dots evenly scattered across the yellow dress".\n'
            "- If no motif is needed, explicitly use plain fabric with no motifs, logos, patches, or prints.\n"
            "- Use modest, family-friendly outfits. For water-play scenes, use covered play clothing and water shoes.\n"
            "- Keep scenes warm, calm, age-appropriate, expressive, and easy for children to understand.\n"
            "- Avoid intense, scary, violent, or unsafe visual wording. State positive visual requirements only.\n"
            "- Each image_prompt should be about 90-150 words and include action, setting, emotion, lighting, "
            "composition, and premium semi-realistic 3D children's storybook style.\n"
            "- Do not include negative-prompt wording.\n\n"
            "## OUTPUT JSON SHAPE\n"
            f"{_compact_json(schema)}\n\n"
            "Return ONLY valid JSON."
        )

    @staticmethod
    def _custom_safe_image_plan_context(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: StoryService._custom_safe_image_plan_context(item) for key, item in value.items()}
        if isinstance(value, list):
            return [StoryService._custom_safe_image_plan_context(item) for item in value]
        if not isinstance(value, str):
            return value
        safe = StoryService._story_planner_safe_profile_text(value)
        replacements = (
            ("skin tone", "overall coloring"),
            ("skin/body color", "overall color palette"),
            ("body proportions", "age appearance"),
            ("upper body", "covered top"),
            ("rash guard", "covered swim shirt"),
            ("swim shorts", "water-play shorts"),
            ("leggings", "covered play bottoms"),
            ("horror", "intense"),
            ("aggressive", "tense"),
            ("frightening", "unsettling"),
        )
        for risky, neutral in replacements:
            safe = StoryService._replace_case_insensitive(safe, risky, neutral)
        return safe

    @staticmethod
    def _prompt_safety_diagnostics(prompt: str) -> dict[str, Any]:
        keywords = (
            "skin tone",
            "body proportions",
            "upper body",
            "rash guard",
            "swim shorts",
            "leggings",
            "horror",
            "aggressive",
            "frightening",
            "mouth",
            "lips",
            "teeth",
        )
        lowered = prompt.lower()
        section_names = re.findall(r"^#{1,6}\s+(.+)$", prompt, flags=re.MULTILINE)
        return {
            "prompt_length": len(prompt),
            "section_names": section_names[:20],
            "keyword_counts": {keyword: lowered.count(keyword) for keyword in keywords if lowered.count(keyword)},
        }

    def _log_custom_image_plan_safety_block(self, story: Any, prompt: str) -> None:
        provider = self.ai_provider
        logger.warning(
            "Custom story %s: safe image plan prompt blocked by Google safety filter. diagnostics=%s model=%s",
            getattr(story, "id", None),
            self._prompt_safety_diagnostics(prompt),
            getattr(provider, "text_model", None),
        )

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
        character_context = self._build_story_cast_context(story, child, story_plan=story_plan)
        compact_story_plan, compact_story_json = self._build_image_plan_context(story_plan, story_json)
        is_custom_story_workflow = self._is_custom_story_workflow_record(story)

        # Populate all placeholders in template
        if is_custom_story_workflow:
            prompt = self._custom_safe_image_plan_prompt(compact_story_plan, compact_story_json, character_context)
        else:
            prompt = template.replace("{story_plan_json}", _compact_json(compact_story_plan))
            prompt = prompt.replace("{story_json}", _compact_json(compact_story_json))
            prompt = prompt.replace("{character_description}", character_context["character_description"])
            prompt = prompt.replace("{character_profile}", character_context["character_description"])
            prompt = prompt.replace("{character_identity_lock}", self._format_prompt_character_identity_lock(character_context))
            prompt = prompt.replace("{child_name}", character_context["child_name"])
            prompt = prompt.replace("{child_age_label}", character_context["child_age_label"])
            prompt = prompt.replace("{child_age_visual_guidance}", character_context["child_age_visual_guidance"])
            prompt = prompt.replace("{cast_mode}", character_context["cast_mode"])
            prompt = prompt.replace("{cast_mode_instructions}", character_context["cast_mode_instructions"])
            prompt = prompt.replace("{character_reference_mode}", character_context["character_reference_mode"])
        step.prompt = prompt
        await self.story_steps.update(step)
        await self.session.commit()

        try:
            try:
                result = await self.ai_provider.generate_text(
                    prompt,
                    max_tokens=self._image_plan_max_tokens(story.age_group),
                    temperature=0.2,
                    step_name=StoryStepName.IMAGE_PLAN_GENERATION.value,
                    response_format={"type": "json_object"},
                )
            except AppException as exc:
                if not self._is_google_prompt_safety_block(exc):
                    raise
                if is_custom_story_workflow:
                    self._log_custom_image_plan_safety_block(story, prompt)
                    raise
                fallback_prompt = self._image_plan_generation_fallback_prompt(
                    compact_story_plan,
                    compact_story_json,
                    character_context,
                )
                logger.warning(
                    "Story %s: image plan prompt blocked by Google safety filter; retrying with compact fallback prompt",
                    story.id,
                )
                step.prompt = fallback_prompt
                await self.story_steps.update(step)
                await self.session.commit()
                result = await self.ai_provider.generate_text(
                    fallback_prompt,
                    max_tokens=self._image_plan_max_tokens(story.age_group),
                    temperature=0.1,
                    step_name=StoryStepName.IMAGE_PLAN_GENERATION.value,
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

            self._validate_image_plan_page_contract(image_plan, story_json)
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
        result = self.image_plan_validator.validate(
            image_plan,
            story_json=story_json,
            skip_footwear_validation=True,
        )

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
        visual_bible = image_plan.get("visual_bible") if isinstance(image_plan.get("visual_bible"), dict) else None
        hero = visual_bible.get("hero") if isinstance(visual_bible, dict) and isinstance(visual_bible.get("hero"), dict) else None
        if not isinstance(hero, dict):
            return image_plan

        outfit = str(hero.get("outfit") or "").strip()
        footwear = str(hero.get("footwear") or "").strip()
        if not footwear:
            footwear = StoryService._image_plan_footwear_from_text(outfit)
        if not footwear:
            footwear = StoryService._default_image_plan_footwear(image_plan)
        hero["footwear"] = footwear

        if outfit and not StoryService._text_has_footwear_lock(outfit):
            hero["outfit"] = f"{outfit}, with {footwear}"
        elif not outfit:
            hero["outfit"] = f"Locked story outfit with {footwear}"

        if not str(hero.get("outfit_lock") or "").strip():
            hero["outfit_lock"] = str(hero.get("outfit") or "").strip()

        StoryService._normalize_image_plan_outfit_motifs(visual_bible)
        StoryService._append_hero_footwear_to_image_prompts(image_plan, hero["footwear"], hero)
        return image_plan

    @staticmethod
    def _normalize_image_plan_outfit_motifs(visual_bible: dict[str, Any]) -> None:
        hero = visual_bible.get("hero")
        if isinstance(hero, dict):
            StoryService._normalize_character_outfit_motif(hero)

        recurring = visual_bible.get("recurring_characters")
        if not isinstance(recurring, list):
            return
        for character in recurring:
            if isinstance(character, dict):
                StoryService._normalize_character_outfit_motif(character)

    @staticmethod
    def _normalize_character_outfit_motif(character: dict[str, Any]) -> None:
        for field in ("outfit", "outfit_lock"):
            value = str(character.get(field) or "").strip()
            if value:
                character[field] = StoryService._normalize_plain_outfit_absence_terms(value)

        combined = f"{character.get('outfit') or ''} {character.get('outfit_lock') or ''}".lower()
        if not combined.strip() or not StoryService._image_plan_outfit_mentions_motif(combined):
            return
        if StoryService._image_plan_outfit_has_plain_or_valid_motif_lock(combined):
            return

        motif = StoryService._image_plan_outfit_motif_label(combined)
        motif_lock = f"Motif lock: one {motif} centered on the front."
        for field in ("outfit", "outfit_lock"):
            value = str(character.get(field) or "").strip()
            if value:
                if motif_lock.lower() not in value.lower():
                    character[field] = f"{value}; {motif_lock}"
            elif field == "outfit_lock":
                character[field] = motif_lock

    @staticmethod
    def _normalize_plain_outfit_absence_terms(text: str) -> str:
        normalized = re.sub(
            r"plain fabric\s*(?:with|,)?\s*no motifs?,\s*logos?,\s*patches?,?\s*(?:or\s*)?(?:prints?|printed designs?)",
            "plain fabric with no print",
            text,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"no motifs?,\s*logos?,\s*patches?,?\s*(?:or\s*)?(?:prints?|printed designs?)",
            "no print",
            normalized,
            flags=re.IGNORECASE,
        )
        return normalized

    @staticmethod
    def _image_plan_outfit_mentions_motif(text: str) -> bool:
        motif_terms = (
            "star",
            "heart",
            "flower",
            "moon",
            "sun",
            "badge",
            "logo",
            "motif",
            "stripe",
            "dot",
            "polka",
            "embroidery",
            "patch",
            "printed",
        )
        return any(term in text for term in motif_terms)

    @staticmethod
    def _image_plan_outfit_has_plain_or_valid_motif_lock(text: str) -> bool:
        plain_terms = (
            "plain fabric",
            "plain",
            "solid color",
            "solid colour",
            "no motif",
            "no motifs",
            "no print",
            "no prints",
            "no logo",
            "no logos",
            "no patch",
            "no patches",
        )
        specific_motif_terms = (
            "star",
            "heart",
            "flower",
            "moon",
            "sun",
            "badge",
            "logo",
            "stripe",
            "dot",
            "polka",
            "embroidery",
            "patch",
            "printed",
        )
        plain_only = any(term in text for term in plain_terms) and not any(term in text for term in specific_motif_terms)
        if plain_only:
            return True
        if StoryService._image_plan_has_repeating_garment_pattern(text):
            return True

        has_count = bool(re.search(r"\b(one|two|three|four|five|single|double|\d+)\b", text))
        placement_terms = ("center", "centred", "chest", "left", "right", "front", "back", "sleeve", "collar", "hem", "pocket")
        has_placement = any(term in text for term in placement_terms)
        return has_count and has_placement

    @staticmethod
    def _image_plan_has_repeating_garment_pattern(text: str) -> bool:
        repeating_pattern_terms = (
            "polka dot",
            "polka dots",
            "dotted",
            "stripes",
            "striped",
            "all-over",
            "all over",
            "evenly scattered",
            "repeating pattern",
            "floral",
            "floral print",
            "geometric",
            "checkered",
            "plaid",
            "paisley",
            "damask",
            "brocade",
            "tasseled",
            "embroidered",
        )
        garment_terms = (
            "dress",
            "shirt",
            "t-shirt",
            "tee",
            "skirt",
            "shorts",
            "pants",
            "trousers",
            "overalls",
            "cardigan",
            "jacket",
            "sweater",
            "fabric",
            "sleeves",
        )
        return any(term in text for term in repeating_pattern_terms) and any(term in text for term in garment_terms)

    @staticmethod
    def _image_plan_outfit_motif_label(text: str) -> str:
        motif_labels = (
            ("star", "star"),
            ("heart", "heart"),
            ("flower", "flower"),
            ("moon", "moon"),
            ("sun", "sun"),
            ("badge", "badge"),
            ("logo", "logo"),
            ("embroidery", "embroidered motif"),
            ("patch", "patch"),
            ("printed", "printed motif"),
            ("stripe", "stripe motif"),
            ("dot", "dot motif"),
            ("polka", "polka-dot motif"),
            ("motif", "motif"),
        )
        for term, label in motif_labels:
            if term in text:
                return label
        return "motif"

    @staticmethod
    def _validate_image_plan_page_contract(image_plan: dict[str, Any], story_json: dict[str, Any]) -> None:
        story_pages = story_json.get("pages") if isinstance(story_json, dict) else None
        if not isinstance(story_pages, list) or not story_pages:
            raise AppException(
                "Story JSON must include pages before image plan generation.",
                code="IMAGE_PLAN_PAGE_CONTRACT_INVALID",
            )

        expected_page_numbers: list[int] = []
        for index, page in enumerate(story_pages):
            if not isinstance(page, dict):
                raise AppException(
                    f"Story JSON page at index {index} is not an object.",
                    code="IMAGE_PLAN_PAGE_CONTRACT_INVALID",
                )
            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                raise AppException(
                    f"Story JSON page at index {index} has invalid page_number.",
                    code="IMAGE_PLAN_PAGE_CONTRACT_INVALID",
                )
            expected_page_numbers.append(page_number)

        image_pages = image_plan.get("pages") if isinstance(image_plan, dict) else None
        if not isinstance(image_pages, list):
            raise AppException(
                f"Image plan returned no pages; expected {len(expected_page_numbers)} pages.",
                code="IMAGE_PLAN_PAGE_COUNT_MISMATCH",
                details={"expected_page_numbers": expected_page_numbers, "received_page_numbers": []},
            )

        received_page_numbers: list[Any] = [
            page.get("page_number") if isinstance(page, dict) else None for page in image_pages
        ]
        if len(image_pages) != len(expected_page_numbers) or received_page_numbers != expected_page_numbers:
            raise AppException(
                "Image plan pages must match story pages exactly: "
                f"expected {expected_page_numbers}, received {received_page_numbers}.",
                code="IMAGE_PLAN_PAGE_COUNT_MISMATCH",
                details={
                    "expected_page_count": len(expected_page_numbers),
                    "received_page_count": len(image_pages),
                    "expected_page_numbers": expected_page_numbers,
                    "received_page_numbers": received_page_numbers,
                },
            )

    _FOOTWEAR_PATTERN = re.compile(
        r"\b("
        r"shoe|shoes|sneaker|sneakers|sandal|sandals|boot|boots|slipper|slippers|"
        r"sock|socks|footwear|barefoot|bare feet|water shoes|flip-flops|flip flops"
        r")\b",
        re.IGNORECASE,
    )

    @classmethod
    def _text_has_footwear_lock(cls, text: str) -> bool:
        return bool(cls._FOOTWEAR_PATTERN.search(text or ""))

    @classmethod
    def _image_plan_footwear_from_text(cls, text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            return ""
        lowered = text.lower()
        options = (
            "water shoes",
            "white sneakers",
            "brown sandals",
            "red rain boots",
            "brown boots",
            "ankle boots",
            "soft socks",
            "bare feet",
            "sneakers",
            "sandals",
            "shoes",
            "boots",
            "slippers",
            "socks",
            "barefoot",
        )
        for option in options:
            if option in lowered:
                return "bare feet" if option == "barefoot" else option
        match = cls._FOOTWEAR_PATTERN.search(text)
        return match.group(0) if match else ""

    @classmethod
    def _default_image_plan_footwear(cls, image_plan: dict[str, Any]) -> str:
        text = json.dumps(image_plan, ensure_ascii=False).lower()
        if any(term in text for term in ("water park", "pool", "beach", "splash pad", "water-play", "water play")):
            return "blue water shoes"
        if any(term in text for term in ("rain", "mud", "puddle")):
            return "yellow rain boots"
        return "closed-toe brown story shoes"

    @classmethod
    def _append_hero_footwear_to_image_prompts(
        cls,
        image_plan: dict[str, Any],
        footwear: str,
        hero: dict[str, Any],
    ) -> None:
        footwear = str(footwear or "").strip()
        if not footwear:
            return
        hero_name = StoryService._character_reference_name_key(str(hero.get("name") or ""))
        hero_id = str(hero.get("character_id") or "hero_child").strip()

        def hero_visible(node: dict[str, Any]) -> bool:
            reference_ids = {
                str(value).strip()
                for value in node.get("reference_character_ids") or []
                if isinstance(value, str) and value.strip()
            }
            character_names = {
                StoryService._character_reference_name_key(value)
                for value in node.get("characters_present") or []
                if isinstance(value, str) and value.strip()
            }
            if hero_id in reference_ids or (hero_name and hero_name in character_names):
                return True
            prompt = str(node.get("image_prompt") or "")
            return bool(hero_name and hero_name in StoryService._character_reference_name_key(prompt))

        def append_lock(node: Any) -> None:
            if not isinstance(node, dict) or not hero_visible(node):
                return
            prompt = str(node.get("image_prompt") or "").strip()
            if not prompt or cls._text_has_footwear_lock(prompt):
                return
            node["image_prompt"] = f"{prompt} The hero wears the locked footwear: {footwear}."

        append_lock(image_plan.get("cover"))
        for page in image_plan.get("pages") or []:
            append_lock(page)
        append_lock(image_plan.get("back_cover"))

    @classmethod
    def _character_reference_id(cls, name: str, *, fallback: str = "character") -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
        return slug or fallback

    @staticmethod
    def _character_reference_name_key(name: str | None) -> str:
        return re.sub(r"\s+", " ", (name or "").strip().lower())

    @classmethod
    def _character_reference_manifest(cls, image_plan: dict[str, Any]) -> list[dict[str, Any]]:
        manifest = image_plan.get("character_reference_manifest")
        if isinstance(manifest, list):
            manifest[:] = [item for item in manifest if isinstance(item, dict)]
            return manifest
        if isinstance(manifest, dict):
            characters = manifest.get("characters")
            if isinstance(characters, list):
                characters[:] = [item for item in characters if isinstance(item, dict)]
                image_plan["character_reference_manifest"] = characters
                return characters
        manifest = []
        image_plan["character_reference_manifest"] = manifest
        return manifest

    @classmethod
    def _upsert_character_reference_manifest_item(
        cls,
        image_plan: dict[str, Any],
        item: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = cls._character_reference_manifest(image_plan)
        character_id = str(item.get("character_id") or "").strip()
        name_key = cls._character_reference_name_key(str(item.get("name") or ""))
        for existing in manifest:
            if character_id and existing.get("character_id") == character_id:
                existing.update({key: value for key, value in item.items() if value is not None})
                return existing
            if name_key and cls._character_reference_name_key(existing.get("name")) == name_key:
                existing.update({key: value for key, value in item.items() if value is not None})
                return existing
        manifest.append(item)
        return item

    @classmethod
    def _visual_bible_recurring_characters(cls, image_plan: dict[str, Any]) -> list[dict[str, Any]]:
        visual_bible = image_plan.get("visual_bible") if isinstance(image_plan.get("visual_bible"), dict) else {}
        recurring = visual_bible.get("recurring_characters")
        if not isinstance(recurring, list):
            return []
        return [character for character in recurring if isinstance(character, dict)]

    @classmethod
    def _visual_bible_hero_character(cls, image_plan: dict[str, Any]) -> dict[str, Any] | None:
        visual_bible = image_plan.get("visual_bible") if isinstance(image_plan.get("visual_bible"), dict) else {}
        hero = visual_bible.get("hero")
        return hero if isinstance(hero, dict) else None

    @classmethod
    def _character_reference_prompt(
        cls,
        *,
        story_title: str,
        character: dict[str, Any],
        visual_bible: dict[str, Any],
        source: str,
    ) -> str:
        name = str(character.get("name") or "Recurring character").strip()
        role = str(character.get("role") or "recurring supporting character").strip()
        appearance = str(character.get("appearance") or "").strip()
        outfit = str(character.get("outfit") or character.get("outfit_lock") or "").strip()
        footwear = str(character.get("footwear") or "").strip()
        hair_lock = str(character.get("hair_lock") or "").strip()
        outfit_lock = str(character.get("outfit_lock") or outfit).strip()
        body_scale = str(character.get("body_scale_lock") or character.get("relative_size") or "").strip()
        relative_size = str(character.get("relative_size") or "").strip()
        signature_item = str(character.get("signature_item") or "").strip()
        forbidden = character.get("forbidden_variations")
        forbidden_text = ", ".join(str(value) for value in forbidden if value) if isinstance(forbidden, list) else ""
        style = str(
            visual_bible.get("style")
            or "Premium semi-realistic 3D children's storybook illustration, cinematic lighting, soft shadows, warm family-film quality."
        )
        source_label = "MAIN HERO" if source == "visual_bible.hero" else "RECURRING CHARACTER"
        parts = [
            f"Create one reusable full-body character reference portrait/model sheet for the {source_label} of a children's storybook.",
            f"Story title: {story_title or 'Untitled story'}.",
            f"Character name: {name}. Role: {role}.",
            "Show this character alone, centered, in a clean full-body or three-quarter standing view with readable face/head, hair/body pattern, complete outfit/body covering, footwear/anatomy, accessories, and body scale.",
            "Use a plain light studio background.",
        ]
        if appearance:
            parts.append(f"Locked appearance, face/head/body design: {appearance}.")
        if hair_lock:
            parts.append(f"Locked hair/fur/feather/body pattern: {hair_lock}.")
        if outfit:
            parts.append(f"Locked outfit/body covering: {outfit}.")
        if outfit_lock and outfit_lock != outfit:
            parts.append(f"Locked outfit pattern and motif placement: {outfit_lock}.")
        if footwear:
            parts.append(f"Locked footwear/anatomy-safe equivalent: {footwear}.")
        if body_scale:
            parts.append(f"Locked body scale/build/proportions: {body_scale}.")
        if relative_size and relative_size != body_scale:
            parts.append(f"Locked relative size: {relative_size}.")
        if signature_item:
            parts.append(f"Locked signature item/accessory: {signature_item}.")
        if forbidden_text:
            parts.append(f"Forbidden variations: {forbidden_text}.")
        parts.extend(
            [
                "This image will be reused as an identity reference for future cover and page illustrations.",
                "Preserve exact face/head shape, facial proportions, eye shape/color, hair/fur/body pattern, skin/body color, age appearance, height/build/proportions, outfit colors, motif count and placement, footwear, accessories, signature item, and distinctive features.",
                "Do not include action poses, scene props, text, labels, logos, watermarks, borders, extra characters, alternate outfits, alternate hairstyles, alternate body scale, or alternate ages.",
                f"Visual style: {style}",
            ]
        )
        return " ".join(parts)

    @classmethod
    def _side_character_reference_prompt(
        cls,
        *,
        story_title: str,
        character: dict[str, Any],
        visual_bible: dict[str, Any],
    ) -> str:
        return cls._character_reference_prompt(
            story_title=story_title,
            character=character,
            visual_bible=visual_bible,
            source="visual_bible.recurring_characters",
        )

    async def _ensure_visual_bible_character_reference(
        self,
        *,
        story: Story,
        image_plan: dict[str, Any],
        visual_bible: dict[str, Any],
        image_storage: Any,
        image_model_kwargs: dict[str, str],
        story_title: str,
        character: dict[str, Any],
        source: str,
        fallback_id: str,
        priority: int,
    ) -> None:
        name = str(character.get("name") or "").strip()
        appearance = str(character.get("appearance") or "").strip()
        outfit = str(character.get("outfit") or character.get("outfit_lock") or "").strip()
        if not name or not (appearance or outfit):
            return

        character_id = str(
            character.get("character_id") or self._character_reference_id(name, fallback=fallback_id)
        ).strip()
        if not character_id:
            return
        character["character_id"] = character_id

        existing_url = str(character.get("reference_image_url") or "").strip()
        if not existing_url:
            for manifest_item in self._character_reference_manifest(image_plan):
                if manifest_item.get("character_id") == character_id and manifest_item.get("reference_image_url"):
                    existing_url = str(manifest_item.get("reference_image_url") or "").strip()
                    break

        if existing_url:
            character["reference_image_url"] = existing_url
            self._upsert_character_reference_manifest_item(
                image_plan,
                {
                    "character_id": character_id,
                    "name": name,
                    "role": character.get("role") or ("hero" if source == "visual_bible.hero" else "recurring_character"),
                    "source": source,
                    "reference_image_url": existing_url,
                    "appearance": appearance,
                    "outfit": outfit,
                    "footwear": character.get("footwear"),
                    "hair_lock": character.get("hair_lock"),
                    "outfit_lock": character.get("outfit_lock"),
                    "body_scale_lock": character.get("body_scale_lock"),
                    "relative_size": character.get("relative_size"),
                    "signature_item": character.get("signature_item"),
                    "priority": priority,
                },
            )
            return

        prompt = self._character_reference_prompt(
            story_title=story_title,
            character=character,
            visual_bible=visual_bible,
            source=source,
        )
        logger.info(
            "Story %s: generating character reference source=%s character_id=%s name=%s",
            story.id,
            source,
            character_id,
            name,
        )
        result = await self.ai_provider.generate_image(
            prompt,
            **image_model_kwargs,
            size=settings.STORY_PAGE_IMAGE_SIZE,
            quality=settings.STORY_IMAGE_QUALITY,
            aspect_ratio="1:1",
        )
        image_bytes = self._crop_image_bytes_to_aspect_ratio(result.image_bytes, "1:1")
        webp_bytes = ImageWebPConverter.convert_to_webp(image_bytes, quality=85)
        image_url = await image_storage.save_story_image(
            story.id,
            webp_bytes,
            f"character_ref_{character_id}.webp",
            "",
        )
        generated_at = datetime.now(UTC).isoformat()
        character["reference_image_url"] = image_url
        character["reference_image_prompt"] = prompt
        character["reference_generated_at"] = generated_at
        self._upsert_character_reference_manifest_item(
            image_plan,
            {
                "character_id": character_id,
                "name": name,
                "role": character.get("role") or ("hero" if source == "visual_bible.hero" else "recurring_character"),
                "source": source,
                "reference_image_url": image_url,
                "reference_image_prompt": prompt,
                "reference_generated_at": generated_at,
                "appearance": appearance,
                "outfit": outfit,
                "footwear": character.get("footwear"),
                "hair_lock": character.get("hair_lock"),
                "outfit_lock": character.get("outfit_lock"),
                "body_scale_lock": character.get("body_scale_lock"),
                "relative_size": character.get("relative_size"),
                "signature_item": character.get("signature_item"),
                "provider": (result.metadata or {}).get("provider"),
                "model": result.model,
                "prompt_used": result.prompt_used,
                "priority": priority,
            },
        )

    async def _ensure_image_plan_character_references(
        self,
        story: Story,
        image_plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Ensure image_plan_json carries reusable reference URLs for recurring characters."""
        if not isinstance(image_plan, dict):
            return image_plan

        visual_bible = image_plan.get("visual_bible") if isinstance(image_plan.get("visual_bible"), dict) else {}
        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        story_title = getattr(story, "title", None) or cover_plan.get("title_text") or ""

        if self._use_child_character(story):
            child = await self.children.get_for_user(story.user_id, story.child_id)
            if child is None:
                raise NotFoundException("Child profile not found during character reference preparation")
            if not child.character_image_url:
                raise AppException(
                    "Generated character image is required for story image generation",
                    code="NO_CHARACTER_IMAGE",
                )
            hero_id = "hero_child"
            hero_name = child.first_name or "Child"
            self._upsert_character_reference_manifest_item(
                image_plan,
                {
                    "character_id": hero_id,
                    "name": hero_name,
                    "role": "hero_child",
                    "source": "child.character_image_url",
                    "reference_image_url": child.character_image_url,
                    "identity_source": "generated_child_character_portrait",
                    "priority": 0,
                },
            )
            hero = visual_bible.get("hero") if isinstance(visual_bible.get("hero"), dict) else None
            if hero is not None:
                hero.setdefault("character_id", hero_id)
                hero.setdefault("reference_image_url", child.character_image_url)

        if (getattr(story, "ai_provider", None) or settings.AI_PROVIDER).strip().lower() != "google":
            return image_plan

        image_storage = get_image_storage_service()
        image_model_kwargs = self._story_image_model_kwargs(story)
        if not self._use_child_character(story):
            hero = self._visual_bible_hero_character(image_plan)
            if hero is not None:
                await self._ensure_visual_bible_character_reference(
                    story=story,
                    image_plan=image_plan,
                    visual_bible=visual_bible,
                    image_storage=image_storage,
                    image_model_kwargs=image_model_kwargs,
                    story_title=story_title,
                    character=hero,
                    source="visual_bible.hero",
                    fallback_id="hero",
                    priority=0,
                )

        recurring = self._visual_bible_recurring_characters(image_plan)
        for index, character in enumerate(recurring, start=1):
            await self._ensure_visual_bible_character_reference(
                story=story,
                image_plan=image_plan,
                visual_bible=visual_bible,
                image_storage=image_storage,
                image_model_kwargs=image_model_kwargs,
                story_title=story_title,
                character=character,
                source="visual_bible.recurring_characters",
                fallback_id=f"side_{index}",
                priority=index,
            )
        return image_plan

    @staticmethod
    def _max_character_references_for_model(model: str | None) -> int:
        normalized = (model or "").lower()
        return 5 if "pro" in normalized and "image" in normalized else 4

    @classmethod
    def _page_reference_text_pool(cls, page_data: dict[str, Any], image_prompt: str | None = None) -> str:
        values = [
            image_prompt or "",
            str(page_data.get("visual_focus") or ""),
            str(page_data.get("scene_action") or ""),
            str(page_data.get("environment") or ""),
            str(page_data.get("image_prompt") or ""),
        ]
        characters_present = page_data.get("characters_present")
        if isinstance(characters_present, list):
            values.extend(str(value) for value in characters_present if isinstance(value, str))
        return " ".join(values).lower()

    async def _story_image_reference_inputs(
        self,
        story: Story,
        image_plan: dict[str, Any],
        page_data: dict[str, Any],
        *,
        image_prompt: str | None,
    ) -> list[dict[str, Any]]:
        manifest = self._character_reference_manifest(image_plan)
        if not manifest:
            return []

        model = story.reference_image_model or settings.GOOGLE_REFERENCE_IMAGE_MODEL
        max_refs = self._max_character_references_for_model(model)
        explicit_ids = {
            str(value).strip()
            for value in page_data.get("reference_character_ids") or []
            if isinstance(value, str) and value.strip()
        }
        characters_present = {
            self._character_reference_name_key(value)
            for value in page_data.get("characters_present") or []
            if isinstance(value, str)
        }
        text_pool = self._page_reference_text_pool(page_data, image_prompt)
        selected: list[dict[str, Any]] = []
        for item in sorted(manifest, key=lambda value: int(value.get("priority") or 100)):
            character_id = str(item.get("character_id") or "").strip()
            name = str(item.get("name") or character_id)
            name_key = self._character_reference_name_key(name)
            is_hero = character_id == "hero_child"
            should_include = (
                is_hero
                or character_id in explicit_ids
                or name_key in characters_present
                or (name and name.lower() in text_pool)
            )
            if not should_include:
                continue
            image_url = str(item.get("reference_image_url") or item.get("image_url") or "").strip()
            if not image_url:
                continue
            selected.append(
                {
                    "character_id": character_id,
                    "name": name,
                    "role": item.get("role") or "character_reference",
                    "image_url": image_url,
                    "image_base64": await self._load_image_as_base64(image_url),
                }
            )
            if len(selected) >= max_refs:
                break
        return selected

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
            if not child.character_image_url:
                raise AppException(
                    "Generated character image is required for story image generation",
                    code="NO_CHARACTER_IMAGE",
                )

            image_storage = get_image_storage_service()
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
                    story_title=story_json.get("title") or story.title or "",
                )
                cover_references = await self._story_image_reference_inputs(
                    story,
                    image_plan,
                    cover,
                    image_prompt=cover.get("image_prompt"),
                )
                cover_bytes = await self.ai_provider.create_story_image(
                    cover_prompt,
                    reference_image_base64=character_image_base64,
                    reference_images_base64=cover_references,
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
                        "reference_character_ids_used": [
                            reference["character_id"] for reference in cover_references
                        ],
                        "reference_image_urls_used": [
                            reference["image_url"] for reference in cover_references
                        ],
                        "model": cover_bytes.model,
                        "provider": (cover_bytes.metadata or {}).get("provider"),
                        "usage": (cover_bytes.metadata or {}).get("usage"),
                    }
                )
                cover_image_bytes = self._crop_image_bytes_to_aspect_ratio(
                    cover_bytes.image_bytes,
                    settings.STORY_COVER_ASPECT_RATIO,
                )
                webp_cover_bytes = ImageWebPConverter.convert_to_webp(cover_image_bytes, quality=85)
                cover_url = await image_storage.save_story_image(
                    story.id,
                    webp_cover_bytes,
                    "cover.webp",
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
                        story_title=story_json.get("title") or story.title or "",
                    )
                    page_references = await self._story_image_reference_inputs(
                        story,
                        image_plan,
                        img_page,
                        image_prompt=img_page.get("image_prompt"),
                    )
                    image_bytes = await self.ai_provider.create_story_image(
                        page_prompt,
                        reference_image_base64=character_image_base64,
                        reference_images_base64=page_references,
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
                            "reference_character_ids_used": [
                                reference["character_id"] for reference in page_references
                            ],
                            "reference_image_urls_used": [
                                reference["image_url"] for reference in page_references
                            ],
                            "model": image_bytes.model,
                            "provider": (image_bytes.metadata or {}).get("provider"),
                            "usage": (image_bytes.metadata or {}).get("usage"),
                        }
                    )
                    page_image_bytes = self._crop_image_bytes_to_aspect_ratio(
                        image_bytes.image_bytes,
                        settings.STORY_PAGE_ASPECT_RATIO,
                    )
                    webp_page_bytes = ImageWebPConverter.convert_to_webp(page_image_bytes, quality=85)
                    image_url = await image_storage.save_story_image(
                        story.id,
                        webp_page_bytes,
                        f"page_{page_num}.webp",
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
                    story_title=story_json.get("title") or story.title or "",
                )
                back_cover_references = await self._story_image_reference_inputs(
                    story,
                    image_plan,
                    back_cover,
                    image_prompt=back_cover.get("image_prompt"),
                )
                back_cover_bytes = await self.ai_provider.create_story_image(
                    back_cover_prompt,
                    reference_image_base64=character_image_base64,
                    reference_images_base64=back_cover_references,
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
                        "reference_character_ids_used": [
                            reference["character_id"] for reference in back_cover_references
                        ],
                        "reference_image_urls_used": [
                            reference["image_url"] for reference in back_cover_references
                        ],
                        "model": back_cover_bytes.model,
                        "provider": (back_cover_bytes.metadata or {}).get("provider"),
                        "usage": (back_cover_bytes.metadata or {}).get("usage"),
                    }
                )
                back_cover_image_bytes = self._crop_image_bytes_to_aspect_ratio(
                    back_cover_bytes.image_bytes,
                    settings.STORY_BACK_COVER_ASPECT_RATIO,
                )
                webp_back_cover_bytes = ImageWebPConverter.convert_to_webp(back_cover_image_bytes, quality=85)
                back_cover_url = await image_storage.save_story_image(
                    story.id,
                    webp_back_cover_bytes,
                    "back_cover.webp",
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
        """Fit generated image bytes onto an exact width:height canvas without cropping."""
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
                image = image.convert("RGBA")
                width, height = image.size
                scale = max(math.ceil(width / numerator), math.ceil(height / denominator))
                if scale <= 0:
                    return image_bytes

                target_width = scale * numerator
                target_height = scale * denominator
                fitted = image.copy()
                fitted.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (target_width, target_height), StoryService._image_padding_color(image))
                left = (target_width - fitted.width) // 2
                top = (target_height - fitted.height) // 2
                canvas.alpha_composite(fitted, (left, top))
                output = BytesIO()
                canvas.convert("RGB").save(output, format="PNG")
                return output.getvalue()
        except UnidentifiedImageError as exc:
            raise AppException("Generated story image is not a valid image", code="INVALID_GENERATED_IMAGE") from exc

    @staticmethod
    def _image_padding_color(image: Image.Image) -> tuple[int, int, int, int]:
        """Pick a quiet padding color from the image corners."""
        rgba = image.convert("RGBA")
        width, height = rgba.size
        pixels = [
            rgba.getpixel((0, 0)),
            rgba.getpixel((max(0, width - 1), 0)),
            rgba.getpixel((0, max(0, height - 1))),
            rgba.getpixel((max(0, width - 1), max(0, height - 1))),
        ]
        return tuple(round(sum(pixel[channel] for pixel in pixels) / len(pixels)) for channel in range(4))

    @staticmethod
    def _render_story_image_prompt(
        template: str,
        visual_bible: dict[str, Any],
        image_prompt: str,
        character_context: dict[str, str],
        page_type: str,
        target_aspect_ratio: str,
        page_data: dict[str, Any] | None = None,
        story_title: str = "",
    ) -> str:
        """Render the final story image prompt with consistency context."""
        rendered_page_data = dict(page_data or {"image_prompt": image_prompt})
        if page_type == "cover" and story_title:
            rendered_page_data.setdefault("title_text", story_title)

        # Format face lock constraints for the prompt
        face_lock_constraints = character_context.get("face_lock_constraints", {})
        face_lock_text = StoryService._format_face_lock_constraints(face_lock_constraints)

        return render_prompt(
            template,
            {
                "visual_bible": visual_bible,
                "page_data": rendered_page_data,
                "character_consistency_json": visual_bible,
                "character_reference_metadata": character_context["character_description"],
                "identity_summary": character_context.get("identity_summary") or character_context["character_description"],
                "character_identity_lock": StoryService._format_prompt_character_identity_lock(character_context),
                "face_lock_constraints": face_lock_text,
                "child_name": character_context.get("child_name", "Child"),
                "story_title": story_title,
                "child_age_label": character_context["child_age_label"],
                "child_age_visual_guidance": character_context["child_age_visual_guidance"],
                "cast_mode": character_context.get("cast_mode", StoryService.CAST_MODE_CHILD_HERO),
                "cast_mode_instructions": character_context.get(
                    "cast_mode_instructions",
                    "CHILD_HERO: preserve the selected child as the story hero.",
                ),
                "character_reference_mode": character_context.get(
                    "character_reference_mode",
                    "A generated Master Character Reference Portrait is attached for the hero child.",
                ),
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
        safe_source_inputs = StoryService._story_plan_prompt_source_inputs(source_inputs)
        first_name = child.first_name or "Child"
        gender = child.gender or "neutral"
        if not character_context.get("use_child_character", True):
            first_name = "AI-created story hero"
            gender = "chosen by the story plan"
        return render_prompt(
            template,
            {
                "age_group": age_group_label(story.age_group),
                "first_name": first_name,
                "gender": gender,
                "theme": _safe_prompt_value(
                    StoryService._story_plan_safe_user_text(theme or safe_source_inputs["category"])
                ),
                "hobby": hobby,
                "learning_goal": _safe_prompt_value(safe_source_inputs["learning_goal"]),
                "story_context": _safe_prompt_value(safe_source_inputs["context"], "none"),
                "moral": "kindness and courage",
                "pages": pages,
                "selected_languages": ", ".join(_normalize_story_languages(story)),
                "selected_language_names": ", ".join(
                    STORY_LANGUAGE_NAMES.get(language, language)
                    for language in _normalize_story_languages(story)
                ),
                "custom_character": character_context.get("use_child_character", True),
                "cast_mode": character_context.get("cast_mode", StoryService.CAST_MODE_CHILD_HERO),
                "cast_mode_instructions": character_context.get(
                    "cast_mode_instructions",
                    "CHILD_HERO: preserve the selected child as the story hero.",
                ),
                "character_reference_mode": character_context.get(
                    "character_reference_mode",
                    "A generated Master Character Reference Portrait is attached for the hero child.",
                ),
                "character_profile_json": character_profile,
                "character_profile": character_profile,
                "character_description": character_context["character_description"],
                "planning_preferences_json": _compact_json(
                    StoryService._story_planning_preferences(story, safe_source_inputs)
                ),
            },
        )

    @staticmethod
    def _story_planning_preferences(story: Any, source_inputs: dict[str, str]) -> dict[str, str]:
        """Build optional creative planning controls without changing the request contract."""
        input_request = getattr(story, "input_request", None)
        if not isinstance(input_request, dict):
            input_request = {}

        preferences: dict[str, str] = {}
        for key in PLANNING_PREFERENCE_KEYS:
            value = input_request.get(key)
            if key == "story_seed":
                value = _first_non_empty(value, source_inputs.get("context"))
            preferences[key] = StoryService._story_plan_safe_user_text(str(value or ""))
        return preferences

    @staticmethod
    def _story_plan_prompt_source_inputs(source_inputs: dict[str, str]) -> dict[str, str]:
        """Return provider-facing story inputs rewritten into neutral child-safe wording."""
        return {
            "category": StoryService._story_plan_safe_user_text(source_inputs.get("category") or "adventure"),
            "learning_goal": StoryService._story_plan_safe_user_text(
                source_inputs.get("learning_goal") or "personal growth"
            ),
            "context": StoryService._story_plan_safe_user_text(source_inputs.get("context") or ""),
        }

    @staticmethod
    def _story_plan_safe_user_text(text: str) -> str:
        safe = (text or "").strip()
        replacements = (
            (
                "if someone says no she should respect it",
                "practice listening when someone asks for space and accepting another person's choice kindly",
            ),
            (
                "if someone says no he should respect it",
                "practice listening when someone asks for space and accepting another person's choice kindly",
            ),
            (
                "if someone says no they should respect it",
                "practice listening when someone asks for space and accepting another person's choice kindly",
            ),
            ("someone says no", "someone asks for space"),
            ("says no", "asks for space"),
            ("say no", "ask for space"),
            ("respect it", "accept another person's choice kindly"),
            (
                "personal hygiene",
                "daily self-care routines such as brushing teeth and washing hands",
            ),
            ("hygiene", "self-care routines"),
        )
        for risky, neutral in replacements:
            safe = StoryService._replace_case_insensitive(safe, risky, neutral)
        return StoryService._soften_child_safety_language(safe)

    @staticmethod
    def _story_planner_age_visual_guidance(character_context: dict[str, str]) -> str:
        label = character_context.get("child_age_label") or "the reader age group"
        return f"age-appropriate look for {label}; keep a friendly childlike picture-book appearance"

    @staticmethod
    def _story_plan_fallback_prompt(
        *,
        story: Story,
        child: Any,
        source_inputs: dict[str, str],
        pages: int,
        character_context: dict[str, str],
    ) -> str:
        safe_inputs = StoryService._story_plan_prompt_source_inputs(source_inputs)
        character_profile = StoryService._build_story_planner_character_profile(child, character_context)
        use_child_character = bool(character_context.get("use_child_character", True))
        payload = {
            "child_name": (child.first_name or "Child") if use_child_character else None,
            "age_group": age_group_label(story.age_group),
            "gender": (child.gender or "neutral") if use_child_character else "chosen by story",
            "page_count": pages,
            "theme": safe_inputs["category"],
            "learning_goal": safe_inputs["learning_goal"],
            "story_context": safe_inputs["context"] or "none",
            "cast_mode": character_context.get("cast_mode", StoryService.CAST_MODE_CHILD_HERO),
            "character_profile": character_profile,
            "planning_preferences": StoryService._story_planning_preferences(story, safe_inputs),
        }
        schema = {
            "title": "",
            "summary": "",
            "theme": "",
            "learning_goal": "",
            "moral_theme": "",
            "setting": "",
            "tone": "",
            "central_problem": "",
            "hero_want": "",
            "emotional_need": "",
            "stakes": "",
            "climax_choice": "",
            "resolution_payoff": "",
            "moral_explanation": "",
            "content_anchors": {
                "required_names": [],
                "required_facts": [],
                "age_safe_explanations": [],
            },
            "visual_bible": {
                "style": "",
                "hero": {
                    "character_id": "",
                    "name": "",
                    "role": "main hero",
                    "appearance": "",
                    "outfit": "",
                    "footwear": "",
                    "hair_lock": "",
                    "outfit_lock": "",
                    "body_scale_lock": "",
                    "relative_size": "",
                    "signature_item": "",
                },
                "companion": {
                    "name": "",
                    "character_id": "",
                    "role": "",
                    "appearance": "",
                    "outfit": "",
                    "hair_lock": "",
                    "outfit_lock": "",
                    "body_scale_lock": "",
                    "relative_size": "",
                    "signature_item": "",
                },
                "father": {"appearance": ""},
                "mother": {"appearance": ""},
                "recurring_characters": [
                    {
                        "character_id": "",
                        "name": "",
                        "role": "",
                        "appearance": "",
                        "outfit": "",
                        "hair_lock": "",
                        "outfit_lock": "",
                        "body_scale_lock": "",
                        "relative_size": "",
                        "signature_item": "",
                    }
                ],
            },
            "pages": [
                {
                    "page_number": 1,
                    "story_role": "",
                    "scene_description": "",
                    "characters_present": [],
                    "child_action": "",
                    "emotional_beat": "",
                    "learning_goal_integration": "",
                    "growth_step": "",
                    "domain_detail": "",
                    "page_turn_hook": "",
                    "continuity_requirements": [],
                }
            ],
        }
        return (
            "You are a professional children's picture-book planning engine.\n"
            "The parent request has been rewritten into neutral child-safe wording. Create a warm, gentle, "
            "age-appropriate story blueprint with the exact requested page count. Keep the same story quality: "
            "clear hero want, gentle central problem, try-fail-try-better growth, meaningful climax choice, "
            "emotional payoff, concrete theme details, and stable visual bible. Use family-friendly language. "
            "Keep all conflict calm, practical, hopeful, and suitable for a children's picture book.\n\n"
            f"Cast mode: {character_context.get('cast_mode', StoryService.CAST_MODE_CHILD_HERO)}. "
            f"{character_context.get('cast_mode_instructions', '')}\n\n"
            "Return STRICT VALID JSON ONLY in this schema shape:\n"
            f"{_compact_json(schema)}\n\n"
            f"SAFE STORY REQUEST JSON:\n{_compact_json(payload)}"
        )

    @staticmethod
    def _build_story_planner_character_profile(child: Any, character_context: dict[str, str]) -> dict[str, Any]:
        """Build the character-profile input expected by the new planner prompt."""
        if not character_context.get("use_child_character", True):
            return {
                "cast_mode": StoryService.CAST_MODE_IMAGINED,
                "hero_source": "AI must invent the story hero and all recurring characters from the story inputs.",
                "profile_summary": character_context["character_description"],
                "child_age_label": character_context["child_age_label"],
                "age_visual_guidance": StoryService._story_planner_age_visual_guidance(character_context),
                "generated_character": None,
            }
        metadata = child.character_metadata if isinstance(child.character_metadata, dict) else {}
        identity_profile = StoryService._story_planner_identity_profile(metadata)
        return {
            "cast_mode": StoryService.CAST_MODE_CHILD_HERO,
            "age": child.age,
            "gender": child.gender or "",
            "name": child.first_name or "Child",
            "profile_summary": StoryService._story_planner_profile_summary(child, metadata, character_context),
            "child_age_label": character_context["child_age_label"],
            "age_visual_guidance": StoryService._story_planner_age_visual_guidance(character_context),
            "generated_character": {
                "identity_profile": identity_profile,
                "style": metadata.get("style") or "premium semi-realistic 3D storybook",
            },
        }

    @staticmethod
    def _story_planner_profile_summary(
        child: Any,
        metadata: dict[str, Any],
        character_context: dict[str, str],
    ) -> str:
        identity_profile = StoryService._story_planner_identity_profile(metadata)
        if not identity_profile:
            description = str(metadata.get("description") or character_context["character_description"] or "").strip()
            if description:
                return StoryService._story_planner_safe_profile_text(description)
            name = child.first_name or "Child"
            return f"{name} is {character_context['child_age_label']}."

        parts = [
            f"{child.first_name or 'Child'} is {character_context['child_age_label']}.",
            StoryService._format_character_identity_profile(identity_profile)
            .replace("- ", "")
            .replace("\n", "; "),
        ]
        return " ".join(part for part in parts if part.strip())

    @staticmethod
    def _story_planner_identity_profile(metadata: dict[str, Any]) -> dict[str, Any]:
        identity_profile = metadata.get("identity_profile")
        if not isinstance(identity_profile, dict):
            return {}
        allowed_for_story_planning = (
            "face_shape",
            "skin_tone",
            "eye_color",
            "eye_shape",
            "hair_color",
            "hair_style",
            "hair_length",
            "hair_texture",
            "hair_direction",
            "age_appearance",
            "distinctive_features",
        )
        result: dict[str, Any] = {}
        for key in allowed_for_story_planning:
            value = identity_profile.get(key)
            if key == "distinctive_features" and isinstance(value, list):
                clean_features = [
                    item
                    for item in value
                    if isinstance(item, str)
                    and item.strip()
                    and not re.search(r"\b(mouth|lip|tooth|teeth|body)\b", item, flags=re.IGNORECASE)
                ]
                if clean_features:
                    result[key] = clean_features
            elif isinstance(value, str) and value.strip():
                result[key] = value.strip()
        return result

    @staticmethod
    def _story_planner_safe_profile_text(text: str) -> str:
        safe = text
        replacements = (
            ("closed-mouth", "gentle"),
            ("mouth shape", "smile shape"),
            ("mouth", "smile"),
            ("lips", "smile details"),
            ("lip", "smile detail"),
            ("tooth gap", "bright smile"),
            ("teeth", "smile"),
            ("body proportions", "age-appropriate proportions"),
            ("body", "appearance"),
        )
        for risky, neutral in replacements:
            safe = StoryService._replace_case_insensitive(safe, risky, neutral)
        return safe

    @staticmethod
    def _format_prompt_character_identity_lock(character_context: dict[str, str]) -> str:
        if not character_context.get("use_child_character", True):
            return (
                f"Cast mode: {StoryService.CAST_MODE_IMAGINED}\n"
                "Reference image role: No external character reference image is attached.\n"
                "Identity source: Use the Visual Bible as the complete model sheet for the hero, companions, "
                "side characters, outfits, colors, face/head details, body scale, recurring objects, and style.\n"
                f"Hero name: {character_context.get('child_name', 'AI-created story hero')}\n"
                f"Age/body guidance: {character_context['child_age_visual_guidance']}\n"
                f"Consistency instruction: {character_context['character_description']}"
            )
        identity_summary = character_context.get("identity_summary") or character_context["character_description"]
        return (
            f"Hero child name: {character_context.get('child_name', 'Child')}\n"
            "Reference image role: Master Character Reference Portrait. Use the generated character_image_url "
            "as the only visual reference image.\n"
            f"Identity summary: {identity_summary}\n"
            f"Identity profile:\n{character_context['character_description']}\n"
            f"Child age: {character_context['child_age_label']}\n"
            f"Age/body guidance: {character_context['child_age_visual_guidance']}"
        )

    @staticmethod
    def _build_story_generation_context(
        story_plan: dict[str, Any],
        *,
        languages: list[str] | None = None,
    ) -> dict[str, Any]:
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
                    "child_action": text(page.get("child_action")),
                    "emotional_beat": text(page.get("emotional_beat")),
                    "learning_goal_integration": text(page.get("learning_goal_integration")),
                    "growth_step": text(page.get("growth_step")),
                    "domain_detail": text(page.get("domain_detail")),
                    "page_turn_hook": text(page.get("page_turn_hook")),
                    "continuity_requirements": page.get("continuity_requirements")
                    if isinstance(page.get("continuity_requirements"), list)
                    else [],
                }
            )

        selected_languages = [
            language
            for language in (languages or story_plan.get("selected_languages") or [])
            if language in SUPPORTED_STORY_LANGUAGES
        ]
        compact_plan = {
            "title": text(story_plan.get("title")),
            "summary": text(story_plan.get("summary")),
            "theme": text(story_plan.get("theme")),
            "learning_goal": text(story_plan.get("learning_goal")),
            "moral_theme": text(story_plan.get("moral_theme")),
            "setting": text(story_plan.get("setting")),
            "tone": text(story_plan.get("tone")),
            "central_problem": text(story_plan.get("central_problem")),
            "hero_want": text(story_plan.get("hero_want")),
            "emotional_need": text(story_plan.get("emotional_need")),
            "stakes": text(story_plan.get("stakes")),
            "climax_choice": text(story_plan.get("climax_choice")),
            "resolution_payoff": text(story_plan.get("resolution_payoff")),
            "moral_explanation": text(story_plan.get("moral_explanation")),
            "story_spine": story_plan.get("story_spine") if isinstance(story_plan.get("story_spine"), dict) else {},
            "language_profile": story_plan.get("language_profile")
            if isinstance(story_plan.get("language_profile"), dict)
            else {},
            "content_anchors": story_plan.get("content_anchors")
            if isinstance(story_plan.get("content_anchors"), dict)
            else {},
            "visual_bible": story_plan.get("visual_bible") if isinstance(story_plan.get("visual_bible"), dict) else {},
            "pages": pages,
            "expected_page_count": len(pages),
            "expected_page_numbers": [
                page.get("page_number")
                for page in pages
                if page.get("page_number") is not None
            ],
        }
        if selected_languages:
            compact_plan["selected_languages"] = selected_languages
            compact_plan["selected_language_names"] = {
                language: STORY_LANGUAGE_NAMES.get(language, language)
                for language in selected_languages
            }
        return StoryService._child_safe_story_context(compact_plan)

    @staticmethod
    def _child_safe_story_context(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: StoryService._child_safe_story_context(item) for key, item in value.items()}
        if isinstance(value, list):
            return [StoryService._child_safe_story_context(item) for item in value]
        if not isinstance(value, str):
            return value
        return StoryService._soften_child_safety_language(value)

    @staticmethod
    def _soften_child_safety_language(text: str) -> str:
        replacements = (
            ("people fall ill", "people cannot enjoy the place"),
            ("people falling ill", "people having trouble enjoying the place"),
            ("people get sick", "people cannot enjoy playing there"),
            ("people getting sick", "people being unable to enjoy playing there"),
            ("people and animals sick", "people and animals staying away"),
            ("animals get sick", "animals stay away"),
            ("animals getting sick", "animals staying away"),
            ("making people sick", "making the place hard to enjoy"),
            ("make people sick", "make the place hard to enjoy"),
            ("health crisis", "community worry"),
            ("health risk", "reason to clean things up"),
            ("unhealthy", "unclean"),
            ("widespread pollution", "litter and clutter"),
            ("air pollution", "dusty air"),
            ("water pollution", "cloudy water"),
            ("land pollution", "litter on the ground"),
            ("polluted", "messy"),
            ("pollution", "litter and mess"),
            ("poisoned air", "dusty air"),
            ("poisoned water", "cloudy water"),
            ("poisoning", "polluting"),
            ("disease", "mess"),
            ("suffering", "discouraged"),
        )
        softened = text
        for risky, safe in replacements:
            softened = StoryService._replace_case_insensitive(softened, risky, safe)
        return softened

    @staticmethod
    def _replace_case_insensitive(text: str, old: str, new: str) -> str:
        lower_text = text.lower()
        lower_old = old.lower()
        start = 0
        parts: list[str] = []
        while True:
            index = lower_text.find(lower_old, start)
            if index == -1:
                parts.append(text[start:])
                return "".join(parts)
            parts.append(text[start:index])
            parts.append(new)
            start = index + len(old)

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
                    "domain_detail": text(page.get("domain_detail")),
                    "continuity_requirements": continuity_requirements,
                }
            )

        compact_story_plan = {
            "title": text(story_plan.get("title")),
            "setting": text(story_plan.get("setting")),
            "tone": text(story_plan.get("tone")),
            "content_anchors": story_plan.get("content_anchors")
            if isinstance(story_plan.get("content_anchors"), dict)
            else {},
            "visual_bible": story_plan.get("visual_bible") if isinstance(story_plan.get("visual_bible"), dict) else {},
            "pages": compact_plan_pages,
        }

        compact_story_pages = [
            {
                "page_number": page.get("page_number"),
                "emotion": text(page.get("emotion")),
                "text": text(page.get("text")),
            }
            for page in story_json.get("pages", [])
            if isinstance(page, dict)
        ]
        expected_page_numbers = [
            page.get("page_number")
            for page in compact_story_pages
            if isinstance(page.get("page_number"), int)
        ]
        compact_story_json = {
            "expected_page_count": len(compact_story_pages),
            "expected_page_numbers": expected_page_numbers,
            "pages": compact_story_pages,
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
            return DEFAULT_AGE_GROUP

        from datetime import date
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        if age < 3:
            return AGE_GROUP_0_3
        if age < 6:
            return AGE_GROUP_3_6
        return AGE_GROUP_6_9

    @staticmethod
    def _get_page_count_for_age_group(age_group: str) -> int:
        """Get recommended page count for age group."""
        return page_count_for_age_group(age_group)

    @staticmethod
    def _get_hobby_for_age_group(age_group: str) -> str:
        """Get age-appropriate hobby/interest suggestions."""
        hobbies = {
            AGE_GROUP_0_3: "peekaboo, soft toys, music, stacking, sensory play, exploring, drawing, singing",
            AGE_GROUP_3_6: "picture books, drawing, building with blocks, pretend play, simple games",
            AGE_GROUP_6_9: "reading, creating art, sports, music, science experiments, building games",
        }
        value = normalize_age_group(age_group)
        return hobbies.get(str(value), hobbies[DEFAULT_AGE_GROUP])

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

    @classmethod
    def _cast_mode(cls, story: Any) -> str:
        if hasattr(story, "use_child_character"):
            return cls.CAST_MODE_CHILD_HERO if bool(getattr(story, "use_child_character")) else cls.CAST_MODE_IMAGINED
        request = getattr(story, "input_request", None)
        if not isinstance(request, dict):
            return cls.CAST_MODE_CHILD_HERO
        cast_mode = request.get("cast_mode")
        if isinstance(cast_mode, str) and cast_mode.strip():
            normalized = cast_mode.strip().upper()
            if normalized in {cls.CAST_MODE_CHILD_HERO, cls.CAST_MODE_IMAGINED}:
                return normalized
        if "use_child_character" in request:
            return cls.CAST_MODE_CHILD_HERO if bool(request.get("use_child_character")) else cls.CAST_MODE_IMAGINED
        return cls.CAST_MODE_CHILD_HERO

    @classmethod
    def _use_child_character(cls, story: Any) -> bool:
        return cls._cast_mode(story) == cls.CAST_MODE_CHILD_HERO

    @classmethod
    def _story_plan_prompt_path(cls, story: Any) -> str:
        if cls._use_child_character(story):
            return "prompts/story/story_plan_child_hero_prompt.txt"
        return "prompts/story/story_plan_imagined_cast_prompt.txt"

    @classmethod
    def _story_generation_prompt_path(cls, story: Any) -> str:
        if cls._use_child_character(story):
            return "prompts/story/story_generation_child_hero_prompt.txt"
        return "prompts/story/story_generation_imagined_cast_prompt.txt"

    async def _selected_child_name_for_plan_validation(self, story: Any) -> str | None:
        child_name = getattr(story, "child_name", None)
        if isinstance(child_name, str) and child_name.strip():
            return child_name.strip()
        child = getattr(story, "child", None)
        child_name = getattr(child, "first_name", None)
        if isinstance(child_name, str) and child_name.strip():
            return child_name.strip()
        user_id = getattr(story, "user_id", None)
        child_id = getattr(story, "child_id", None)
        if user_id is None or child_id is None:
            return None
        try:
            child = await self.children.get_for_user(user_id, child_id)
        except Exception:
            logger.warning("Story %s: unable to load child name for plan validation", getattr(story, "id", None))
            return None
        child_name = getattr(child, "first_name", None)
        return child_name.strip() if isinstance(child_name, str) and child_name.strip() else None

    @classmethod
    def _build_story_cast_context(
        cls,
        story: Any,
        child: Any,
        *,
        story_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if cls._use_child_character(story):
            return cls._build_character_reference_context(child)
        return cls._build_imagined_cast_context(story, story_plan=story_plan)

    @classmethod
    def _build_imagined_cast_context(
        cls,
        story: Any,
        *,
        story_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        visual_bible = story_plan.get("visual_bible") if isinstance(story_plan, dict) else None
        hero = visual_bible.get("hero") if isinstance(visual_bible, dict) else None
        hero_name = hero.get("name") if isinstance(hero, dict) and isinstance(hero.get("name"), str) else ""
        hero_name = hero_name.strip() or "AI-created story hero"
        age_label = age_group_label(getattr(story, "age_group", None))
        character_description = (
            "Imaginative cast mode. Do not use the child profile as the story hero. "
            "Invent the hero and all recurring characters from the category, learning goal, and story idea. "
            "Lock every recurring character in the Visual Bible with stable face/head shape, hair or fur, eyes, "
            "skin or body color, outfit, shoes, accessories, size, distinctive features, and a single story style. "
            "The Visual Bible is the only character consistency source for image generation."
        )
        return {
            "cast_mode": cls.CAST_MODE_IMAGINED,
            "use_child_character": False,
            "child_name": hero_name,
            "character_description": character_description,
            "identity_summary": "AI-created cast; use the Visual Bible as the locked character model sheet.",
            "child_age_label": age_label,
            "child_age_visual_guidance": (
                "age-appropriate proportions for the reader band; preserve each invented character's locked age, "
                "body scale, outfit, colors, and style from the Visual Bible"
            ),
            "character_reference_mode": (
                "No external character reference image is attached. Use Visual Bible text locks only."
            ),
            "cast_mode_instructions": (
                "IMAGINED_CAST: create a named hero and complete recurring cast from the story inputs. "
                "Do not use the child profile as a character. Every recurring character must have detailed, "
                "stable visual locks suitable for consistent batch image generation."
            ),
        }

    @staticmethod
    def _build_character_reference_context(child) -> dict[str, Any]:
        character_description = StoryService._extract_character_analysis(child)
        face_lock_constraints = StoryService._extract_face_lock_constraints(child)
        return {
            "cast_mode": StoryService.CAST_MODE_CHILD_HERO,
            "use_child_character": True,
            "child_name": (child.first_name or "Child") if child else "Child",
            "character_description": character_description,
            "identity_summary": StoryService._extract_identity_summary(child) or character_description,
            "face_lock_constraints": face_lock_constraints,
            "child_age_label": StoryService._child_age_label(child),
            "child_age_visual_guidance": StoryService._age_visual_guidance(child.age if child else None),
            "character_reference_mode": (
                "A generated Master Character Reference Portrait is attached for the hero child."
            ),
            "cast_mode_instructions": (
                "CHILD_HERO: use the selected child as the story hero. Preserve the child identity lock and "
                "use the Visual Bible for the single story outfit, companions, side characters, objects, and style."
            ),
        }

    @staticmethod
    def _extract_identity_summary(child) -> str:
        if not child or not isinstance(child.character_metadata, dict):
            return ""
        metadata_summary = child.character_metadata.get("identity_summary")
        if isinstance(metadata_summary, str) and metadata_summary.strip():
            return metadata_summary.strip()

        identity_profile = child.character_metadata.get("identity_profile")
        if not isinstance(identity_profile, dict):
            return ""
        value = identity_profile.get("identity_summary")
        return value.strip() if isinstance(value, str) and value.strip() else ""

    @staticmethod
    def _extract_face_lock_constraints(child) -> dict[str, Any]:
        """Extract structured face lock constraints from child profile identity analysis.

        Returns a dict with all facial identity features that must be preserved exactly
        across all story page generations.
        """
        if not child or not isinstance(child.character_metadata, dict):
            return {}

        identity_profile = child.character_metadata.get("identity_profile")
        if not isinstance(identity_profile, dict):
            return {}

        # Map of identity_profile keys to their display names
        face_structure_fields = {
            "face_shape": "Face shape",
            "cheek_shape": "Cheek shape",
            "jawline_shape": "Jawline shape",
            "chin_shape": "Chin shape",
        }

        eye_fields = {
            "eye_color": "Eye color",
            "eye_shape": "Eye shape",
            "eye_size": "Eye size",
            "eyebrow_shape": "Eyebrow shape",
            "eyebrow_thickness": "Eyebrow thickness",
        }

        hair_fields = {
            "hair_color": "Hair color",
            "hair_length": "Hair length",
            "hair_texture": "Hair texture",
            "hair_style": "Hair style",
            "hair_direction": "Hair direction",
        }

        other_fields = {
            "nose_shape": "Nose shape",
            "mouth_shape": "Mouth shape",
            "smile_characteristics": "Smile characteristics",
            "skin_tone": "Skin tone",
        }

        constraints = {}

        # Face structure lock
        face_structure = {}
        for key, label in face_structure_fields.items():
            value = identity_profile.get(key)
            if isinstance(value, str) and value.strip():
                face_structure[label] = value.strip()
        if face_structure:
            constraints["face_structure"] = face_structure

        # Eyes lock
        eyes = {}
        for key, label in eye_fields.items():
            value = identity_profile.get(key)
            if isinstance(value, str) and value.strip():
                eyes[label] = value.strip()
        if eyes:
            constraints["eyes"] = eyes

        # Hair lock
        hair = {}
        for key, label in hair_fields.items():
            value = identity_profile.get(key)
            if isinstance(value, str) and value.strip():
                hair[label] = value.strip()
        if hair:
            constraints["hair"] = hair

        # Other features
        other = {}
        for key, label in other_fields.items():
            value = identity_profile.get(key)
            if isinstance(value, str) and value.strip():
                other[label] = value.strip()
        if other:
            constraints["other_features"] = other

        # Distinctive features (must appear in every page)
        distinctive_features = identity_profile.get("distinctive_features")
        if isinstance(distinctive_features, list):
            clean_features = [f.strip() for f in distinctive_features if isinstance(f, str) and f.strip()]
            if clean_features:
                constraints["distinctive_features"] = clean_features

        return constraints

    @staticmethod
    def _extract_character_analysis(child) -> str:
        """Extract detailed character analysis from child profile for visual consistency."""
        age_str = f"{child.age} years old" if child.age else "child"

        if not child.character_metadata:
            return f"A friendly {age_str} child named {child.first_name} ready for adventure."

        metadata = child.character_metadata

        # Use the structured identity profile generated from the master portrait.
        # Legacy free-form visual descriptions are not used in story prompts.
        identity_profile = metadata.get("identity_profile")
        description = metadata.get("description", "")

        # Build comprehensive character profile for image anchor consistency
        parts = []

        # Add age and name as header
        parts.append(f"Age: {age_str}")
        parts.append(f"Name: {child.first_name}")
        parts.append(f"Age Appearance Guidance: {StoryService._age_visual_guidance(child.age)}")

        if isinstance(identity_profile, dict) and identity_profile:
            parts.append("Stable Visual Identity:\n" + StoryService._format_character_identity_profile(identity_profile))
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

    @staticmethod
    def _format_face_lock_constraints(constraints: dict[str, Any]) -> str:
        """Format face lock constraints from identity analysis for use in image generation prompt.

        This creates an explicit constraint section that locks all facial identity features
        so they remain consistent across all story page generations.
        """
        if not constraints:
            return ""

        lines = []

        # Face structure lock
        face_structure = constraints.get("face_structure", {})
        if face_structure:
            lines.append("## FACE STRUCTURE LOCK (EXACT - do not vary)")
            for key, value in face_structure.items():
                lines.append(f"- {key}: {value}")
            lines.append("")

        # Eyes lock
        eyes = constraints.get("eyes", {})
        if eyes:
            lines.append("## EYES LOCK (EXACT - do not vary)")
            for key, value in eyes.items():
                lines.append(f"- {key}: {value}")
            lines.append("")

        # Hair lock
        hair = constraints.get("hair", {})
        if hair:
            lines.append("## HAIR LOCK (EXACT - do not vary)")
            for key, value in hair.items():
                lines.append(f"- {key}: {value}")
            lines.append("")

        # Other features lock
        other_features = constraints.get("other_features", {})
        if other_features:
            lines.append("## OTHER FEATURES LOCK (EXACT - do not vary)")
            for key, value in other_features.items():
                lines.append(f"- {key}: {value}")
            lines.append("")

        # Distinctive features (MUST include in every page)
        distinctive_features = constraints.get("distinctive_features", [])
        if distinctive_features:
            lines.append("## DISTINCTIVE FEATURES (MUST APPEAR in every page - do not remove)")
            lines.append("These specific features MUST be visible in the character across all pages:")
            for feature in distinctive_features:
                lines.append(f"- {feature}")
            lines.append("")

        # Add rendering instruction
        lines.append("## RENDERING INSTRUCTION")
        lines.append(
            "These face lock constraints are extracted from the Master Character Reference Portrait. "
            "Do not interpret them as guidelines or suggestions. "
            "Render the character matching EVERY detail above exactly. "
            "Do not add, remove, or modify any facial features between pages. "
            "Distinctive features MUST be visible in this illustration."
        )

        return "\n".join(lines)

    @staticmethod
    def _format_character_identity_profile(profile: dict[str, Any]) -> str:
        lines = []
        labels = {
            "face_shape": "Face shape",
            "skin_tone": "Skin tone",
            "eye_color": "Eye color",
            "eye_shape": "Eye shape",
            "eyebrow_shape": "Eyebrow shape",
            "nose_shape": "Nose shape",
            "mouth_shape": "Mouth shape",
            "smile_characteristics": "Smile characteristics",
            "smile_type": "Smile type",
            "mouth_description": "Mouth",
            "cheek_shape": "Cheek shape",
            "jawline_shape": "Jawline shape",
            "chin_shape": "Chin shape",
            "hair_color": "Hair color",
            "hair_style": "Hair style",
            "hair_length": "Hair length",
            "hair_texture": "Hair texture",
            "hair_direction": "Hair direction",
            "eye_size": "Eye size",
            "eyebrow_thickness": "Eyebrow thickness",
            "ear_visibility": "Ear visibility",
            "age_appearance": "Age appearance",
            "identity_summary": "Identity summary",
        }
        for key, label in labels.items():
            value = profile.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"- {label}: {value.strip()}")

        features = profile.get("distinctive_features")
        if isinstance(features, list):
            clean_features = [item.strip() for item in features if isinstance(item, str) and item.strip()]
            if clean_features:
                lines.append("- Distinctive features: " + ", ".join(clean_features))

        return "\n".join(lines)

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
            video_created=bool(story.video_created),
            video_metadata=story.video_metadata,
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
                    video_created=bool(story.video_created),
                    video_metadata=story.video_metadata,
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
