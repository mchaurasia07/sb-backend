"""Story narration generation service."""

from copy import deepcopy
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.story_repository import StoryRepository
from app.service.story_audio_storage_provider import get_story_audio_storage_service
from app.utils.google_tts_utils import GoogleTTSProvider
from app.utils.word_timestamps import generate_word_timestamps

logger = logging.getLogger(__name__)


class StoryNarrationService:
    """Orchestrates language-specific story narration generation workflow."""

    REMOVED_PAGE_FIELDS = {
        "audio_url",
        "tts_model",
        "tts_prompt",
        "tts_skipped",
        "tts_voice",
    }

    def __init__(self, session: AsyncSession):
        self.generic_stories = GenericStoryRepository(session)
        self.stories = StoryRepository(session)
        self.tts_provider = GoogleTTSProvider()
        self.audio_storage = get_story_audio_storage_service()

    async def generate_narration(
        self,
        story_id: UUID,
        language: str = "en",
        overwrite: bool = False,
    ) -> dict:
        return await self.generate_generic_story_narration(
            story_id=story_id,
            language=language,
            overwrite=overwrite,
        )

    async def generate_story_json_narration(
        self,
        story_json: dict,
        *,
        story_id: UUID,
        language: str = "en",
        overwrite: bool = False,
        source: str = "story",
    ) -> dict:
        """Generate narration directly into an in-memory story_json payload."""
        normalized_language = language.strip().lower()
        await self._generate_story_json_narration(
            story_json,
            story_id=story_id,
            language=normalized_language,
            overwrite=overwrite,
            source=source,
        )
        return story_json

    async def generate_generic_story_narration(
        self,
        story_id: UUID,
        language: str = "en",
        overwrite: bool = False,
    ) -> dict:
        """Generate narration for a generic story content row.

        The story text comes from generic_story_contents.story_json for the
        requested story_id + language. Narration timing is written back to that
        same language-specific JSON.
        """
        normalized_language = language.strip().lower()
        content = await self.generic_stories.get_content_by_story_and_language(
            generic_story_id=story_id,
            language=normalized_language,
        )
        if content is None:
            raise NotFoundException("Generic story content not found", "GENERIC_STORY_CONTENT_NOT_FOUND")

        if not content.story_json:
            logger.error("Generic story content has no story_json: story_id=%s language=%s", story_id, normalized_language)
            raise AppException("Generic story content does not have story_json")

        try:
            story_json = deepcopy(content.story_json)
            await self._generate_story_json_narration(
                story_json,
                story_id=story_id,
                language=normalized_language,
                overwrite=overwrite,
                source="generic_story",
            )

            content.story_json = story_json
            updated_content = await self.generic_stories.update_content(content)

            logger.info("Narration generation complete: story_id=%s language=%s", story_id, normalized_language)
            return updated_content.story_json

        except NotFoundException:
            raise
        except AppException:
            raise
        except Exception as e:
            logger.exception("Unexpected error in narration generation: story_id=%s language=%s", story_id, normalized_language)
            raise AppException(f"Failed to generate narration: {str(e)}")

    async def generate_story_table_narration(
        self,
        story_id: UUID,
        *,
        user_id: UUID | None = None,
        language: str = "en",
        overwrite: bool = False,
    ) -> dict:
        """Generate narration for a custom story content row.

        This is separate from the route-facing generic-story method so other
        backend flows can narrate custom/generated stories without touching
        generic_story_contents. Story JSON is read from and written back to
        story_contents for the requested language.
        """
        normalized_language = language.strip().lower()
        story = (
            await self.stories.get_for_user(user_id, story_id)
            if user_id is not None
            else await self.stories.get_by_id(story_id)
        )
        if story is None:
            raise NotFoundException("Story not found", "STORY_NOT_FOUND")

        content = await self.stories.get_content_by_story_and_language(
            story_id=story.id,
            language=normalized_language,
        )

        if content is None:
            logger.error("Story content not found: story_id=%s language=%s", story_id, normalized_language)
            raise NotFoundException("Story content not found", "STORY_CONTENT_NOT_FOUND")

        if not content.story_json:
            logger.error("Story content has no story_json: story_id=%s language=%s", story_id, normalized_language)
            raise AppException("Story content does not have story_json")

        try:
            story_json = deepcopy(content.story_json)
            await self._generate_story_json_narration(
                story_json,
                story_id=story_id,
                language=normalized_language,
                overwrite=overwrite,
                source="story",
            )

            content.story_json = story_json
            updated_content = await self.stories.update_content(content)

            logger.info("Story content narration generation complete: story_id=%s language=%s", story_id, normalized_language)
            return updated_content.story_json

        except NotFoundException:
            raise
        except AppException:
            raise
        except Exception as e:
            logger.exception("Unexpected error in story table narration generation: story_id=%s language=%s", story_id, normalized_language)
            raise AppException(f"Failed to generate story table narration: {str(e)}")

    async def _generate_story_json_narration(
        self,
        story_json: dict,
        *,
        story_id: UUID,
        language: str,
        overwrite: bool,
        source: str,
    ) -> None:
        pages = story_json.get("pages", [])
        moral = story_json.get("moral") if isinstance(story_json.get("moral"), dict) else {}
        default_speech_narration = (
            moral.get("speech_narration", {}) if isinstance(moral.get("speech_narration"), dict) else {}
        )

        if not pages:
            logger.warning("Story JSON has no pages: source=%s story_id=%s language=%s", source, story_id, language)
            return

        logger.info(
            "Starting narration generation: source=%s story_id=%s language=%s page_count=%s",
            source,
            story_id,
            language,
            len(pages),
        )

        for i, page in enumerate(pages):
            page_number = page.get("page_number", i + 1)
            text = page.get("text", "").strip()

            if not text:
                logger.info("Skipping empty page: source=%s story_id=%s language=%s page=%s", source, story_id, language, page_number)
                continue

            if (
                not settings.GOOGLE_TTS_SKIP_CALL
                and not overwrite
                and page.get("audio_url")
                and page.get("duration")
                and self._has_sentence_timestamps(page)
            ):
                logger.info("Narration timing exists, skipping: source=%s story_id=%s language=%s page=%s", source, story_id, language, page_number)
                continue

            page = self._remove_page_fields(page)
            pages[i] = page
            enriched_page = await self._generate_page_narration(
                page,
                story_id=story_id,
                language=language,
                default_speech_narration=default_speech_narration,
            )
            pages[i] = enriched_page
            logger.info(
                "Generated page narration: source=%s story_id=%s language=%s page=%s duration=%s",
                source,
                story_id,
                language,
                page_number,
                enriched_page.get("duration"),
            )

    async def _generate_page_narration(
        self,
        page_dict: dict,
        *,
        story_id: UUID,
        language: str,
        default_speech_narration: dict | None = None,
    ) -> dict:
        page_number = page_dict.get("page_number", 1)
        text = page_dict.get("text", "").strip()

        if not text:
            raise ValueError(f"Page text is empty for page {page_number}")

        speech_narration = page_dict.get("speech_narration") or default_speech_narration or {}
        pace = speech_narration.get("pace", "medium")
        voice_style = speech_narration.get("voice_style", "storybook narrator")
        tone = speech_narration.get("tone", "warm, magical, gentle")
        emotion = speech_narration.get("emotion", "wonder")

        logger.info(
            "Generating TTS: story_id=%s language=%s page=%s pace=%s text_length=%s",
            story_id,
            language,
            page_number,
            pace,
            len(text),
        )

        tts_prompt = self.tts_provider.build_prompt(
            text,
            pace=pace,
            language=language,
            voice_style=voice_style,
            tone=tone,
            emotion=emotion,
        )
        logger.debug("Built TTS prompt for story_id=%s language=%s page=%s", story_id, language, page_number)

        if settings.GOOGLE_TTS_SKIP_CALL:
            enriched_page = self._remove_page_fields(page_dict)
            enriched_page.pop("duration", None)
            enriched_page.pop("word_timestamps", None)
            enriched_page["tts_prompt"] = tts_prompt
            enriched_page["tts_skipped"] = True
            enriched_page["tts_model"] = settings.GOOGLE_TTS_MODEL
            enriched_page["tts_voice"] = settings.GOOGLE_TTS_VOICE
            logger.info("Skipped Gemini TTS call: story_id=%s language=%s page=%s", story_id, language, page_number)
            return enriched_page

        audio_bytes, duration = await self.tts_provider.generate_narration_audio(
            text,
            pace,
            language=language,
            voice_style=voice_style,
            tone=tone,
            emotion=emotion,
        )
        audio_url = await self.audio_storage.save_story_page_audio(
            story_id=story_id,
            language=language,
            page_number=page_number,
            audio_bytes=audio_bytes,
        )
        word_timestamps = generate_word_timestamps(text, duration)

        enriched_page = self._remove_page_fields(page_dict)
        enriched_page["tts_prompt"] = tts_prompt
        enriched_page["tts_skipped"] = False
        enriched_page["tts_model"] = settings.GOOGLE_TTS_MODEL
        enriched_page["tts_voice"] = settings.GOOGLE_TTS_VOICE
        enriched_page["audio_url"] = audio_url
        enriched_page["duration"] = round(duration, 2)
        enriched_page["word_timestamps"] = word_timestamps

        logger.info(
            "Page narration complete: language=%s page=%s file=%s duration=%.2fs timestamps=%s",
            language,
            page_number,
            audio_url,
            duration,
            len(word_timestamps),
        )
        return enriched_page

    def _remove_page_fields(self, page_dict: dict) -> dict:
        cleaned_page = dict(page_dict)
        for field in self.REMOVED_PAGE_FIELDS:
            cleaned_page.pop(field, None)
        return cleaned_page

    @staticmethod
    def _has_sentence_timestamps(page_dict: dict) -> bool:
        timestamps = page_dict.get("word_timestamps")
        text = page_dict.get("text", "")
        if not isinstance(timestamps, list) or not timestamps:
            return False
        if not isinstance(text, str) or not text.strip():
            return True

        word_count = len(text.split())
        timestamp_count = len(timestamps)
        return timestamp_count < max(2, word_count // 2)
