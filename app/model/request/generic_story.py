from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.entity.generic_story import GenericStoryLanguage


class GenericStoryContentRequest(BaseModel):
    language: GenericStoryLanguage = GenericStoryLanguage.EN
    story_json: dict[str, Any]


class GenericStoryCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    age_group: str = Field(min_length=1, max_length=32)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    moral: str | None = Field(default=None, max_length=255)
    learning_goal: str | None = Field(default=None, max_length=500)
    reading_time_minutes: int | None = Field(default=None, ge=0)
    character_type: str | None = Field(default=None, max_length=100)
    total_pages: int = Field(default=0, ge=0)
    cover_image: str | None = Field(default=None, max_length=1024)
    status: Literal["active", "inactive"] = "active"
    story_contents: list[GenericStoryContentRequest] = Field(default_factory=list)


class GenericStoryUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = None
    age_group: str | None = Field(default=None, min_length=1, max_length=32)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    moral: str | None = Field(default=None, max_length=255)
    learning_goal: str | None = Field(default=None, max_length=500)
    reading_time_minutes: int | None = Field(default=None, ge=0)
    character_type: str | None = Field(default=None, max_length=100)
    total_pages: int | None = Field(default=None, ge=0)
    cover_image: str | None = Field(default=None, max_length=1024)
    status: Literal["active", "inactive"] | None = None
    story_contents: list[GenericStoryContentRequest] | None = None


class AddGenericStoryToChildRequest(BaseModel):
    generic_story_id: UUID
    language: GenericStoryLanguage = GenericStoryLanguage.EN
