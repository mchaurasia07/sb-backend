from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.entity.generic_story_workflow import GenericStoryWorkflowStep


class GenericStoryWorkflowCreateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    actual_story: str = Field(min_length=20, max_length=50000)
    age_group: str = Field(min_length=1, max_length=32)
    language: str = Field(default="en", min_length=2, max_length=16)
    requested_pages: int | None = Field(default=None, ge=1, le=24)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    learning_goal: str | None = Field(default=None, max_length=500)
    status: Literal["active", "inactive"] = "inactive"

    @field_validator("title", "actual_story", "age_group", "language", "theme", "genre", "learning_goal", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None or not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None


class GenericStoryWorkflowExecuteRequest(BaseModel):
    step_name: GenericStoryWorkflowStep | Literal["ALL"] = "ALL"
    skip_image_generation: bool = False
    skip_narration_generation: bool = False
    publish_status: Literal["active", "inactive"] | None = None
