from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class GenericStoryCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    age_group: str = Field(min_length=1, max_length=32)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    language: str = Field(default="en", min_length=1, max_length=50)
    moral: str | None = Field(default=None, max_length=255)
    learning_goal: str | None = Field(default=None, max_length=500)
    reading_time_minutes: int | None = Field(default=None, ge=0)
    character_type: str | None = Field(default=None, max_length=100)
    total_pages: int = Field(default=0, ge=0)
    cover_image: str | None = Field(default=None, max_length=1024)
    story_json: dict[str, Any]
    status: Literal["active", "inactive"] = "active"


class GenericStoryUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = None
    age_group: str | None = Field(default=None, min_length=1, max_length=32)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, min_length=1, max_length=50)
    moral: str | None = Field(default=None, max_length=255)
    learning_goal: str | None = Field(default=None, max_length=500)
    reading_time_minutes: int | None = Field(default=None, ge=0)
    character_type: str | None = Field(default=None, max_length=100)
    total_pages: int | None = Field(default=None, ge=0)
    cover_image: str | None = Field(default=None, max_length=1024)
    story_json: dict[str, Any] | None = None
    status: Literal["active", "inactive"] | None = None


class AddGenericStoryToChildRequest(BaseModel):
    generic_story_id: UUID
