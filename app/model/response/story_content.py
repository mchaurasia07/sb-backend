from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SpeechNarrationResponse(BaseModel):
    tone: str | None = None
    pace: str | None = None
    emotion: str | None = None
    voice_style: str | None = None


class WordTimestampResponse(BaseModel):
    word: str
    start: float
    end: float


class StoryPageContentResponse(BaseModel):
    page_number: int | None = None
    speech_narration: SpeechNarrationResponse | None = None
    text: str | None = None
    tts_prompt: str | None = None
    tts_skipped: bool | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    audio_url: str | None = None
    duration: float | None = None
    word_timestamps: list[WordTimestampResponse] = Field(default_factory=list)

    @field_validator("word_timestamps", mode="before")
    @classmethod
    def _normalize_word_timestamps(cls, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []


class StoryMoralContentResponse(BaseModel):
    page_number: int | None = None
    speech_narration: SpeechNarrationResponse | None = None
    text: str | None = None


class StoryJsonContentResponse(BaseModel):
    title: str | None = None
    summary: str | None = None
    theme: str | None = None
    art_style: str | None = None
    pages: list[StoryPageContentResponse] = Field(default_factory=list)
    moral: StoryMoralContentResponse | None = None

    @field_validator("pages", mode="before")
    @classmethod
    def _normalize_pages(cls, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @field_validator("moral", mode="before")
    @classmethod
    def _normalize_moral(cls, value: Any) -> Any:
        if isinstance(value, dict) or value is None:
            return value
        return {"text": str(value)}


class StoryContentResponse(BaseModel):
    """Language-specific story JSON shared by generic and custom story content APIs."""

    story_id: UUID
    story_type: Literal["generic", "custom"]
    language: str
    story_json: StoryJsonContentResponse
