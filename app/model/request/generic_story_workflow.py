from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.age_groups import validate_age_group
from app.core.illustration_styles import DEFAULT_ILLUSTRATION_TYPE, normalize_illustration_type
from app.entity.generic_story_workflow import GenericStoryWorkflowStep


class GenericStoryWorkflowCreateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    actual_story: str = Field(min_length=20, max_length=50000)
    age_group: str = Field(min_length=1, max_length=32)
    language: str = Field(default="en", min_length=2, max_length=16)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    learning_goal: str | None = Field(default=None, max_length=500)
    illustration_type: str = Field(default=DEFAULT_ILLUSTRATION_TYPE, min_length=1, max_length=64)
    status: Literal["active", "inactive"] = "inactive"

    @field_validator("title", "actual_story", "age_group", "language", "theme", "genre", "learning_goal", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None or not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("age_group")
    @classmethod
    def validate_age_group_value(cls, value):
        return validate_age_group(value)

    @field_validator("illustration_type", mode="before")
    @classmethod
    def normalize_illustration_type_value(cls, value):
        return normalize_illustration_type(value)


class GenericStoryWorkflowExecuteRequest(BaseModel):
    step_name: GenericStoryWorkflowStep | Literal["ALL"] = "ALL"
    skip_image_generation: bool = False
    skip_narration_generation: bool = True
    publish_status: Literal["active", "inactive"] | None = None


class GenericStoryWorkflowRetryRequest(BaseModel):
    skip_image_generation: bool = False
    skip_narration_generation: bool = True
    publish_status: Literal["active", "inactive"] | None = None
