from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.age_groups import validate_age_group
from app.entity.generic_story import GenericStoryLanguage


class GenericStoryContentRequest(BaseModel):
    language: str = Field(default="en", min_length=2, max_length=16)
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

    @field_validator("age_group")
    @classmethod
    def validate_age_group_value(cls, value):
        return validate_age_group(value)


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

    @field_validator("age_group")
    @classmethod
    def validate_age_group_value(cls, value):
        if value is None:
            return value
        return validate_age_group(value)


class GenericStoryStatusUpdateRequest(BaseModel):
    status: Literal["active", "inactive"]


class AddGenericStoryToChildRequest(BaseModel):
    generic_story_id: UUID
    language: GenericStoryLanguage = GenericStoryLanguage.EN


class AddCustomStoryToChildRequest(BaseModel):
    story_id: UUID
    language: GenericStoryLanguage = GenericStoryLanguage.EN
