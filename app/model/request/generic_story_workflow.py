from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.age_groups import validate_age_group
from app.core.illustration_styles import DEFAULT_ILLUSTRATION_TYPE, normalize_illustration_type
from app.entity.custom_story_workflow import CustomStoryWorkflowStep


class GenericStoryWorkflowCreateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    actual_story: str = Field(min_length=20, max_length=50000)
    age_group: str = Field(min_length=1, max_length=32)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    languages: list[str] = Field(default_factory=lambda: ["en", "hi", "mr"], min_length=1, max_length=3)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    learning_goal: str | None = Field(default=None, max_length=500)
    illustration_type: str = Field(default=DEFAULT_ILLUSTRATION_TYPE, min_length=1, max_length=64)
    status: Literal["active", "inactive"] = "inactive"

    @model_validator(mode="before")
    @classmethod
    def apply_language_alias(cls, data):
        if isinstance(data, dict) and "languages" not in data and data.get("language"):
            data = dict(data)
            data["languages"] = [data["language"]]
        return data

    @field_validator("title", "actual_story", "age_group", "language", "theme", "genre", "learning_goal", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None or not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("languages", mode="before")
    @classmethod
    def normalize_languages(cls, value):
        if value is None:
            return ["en", "hi", "mr"]
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return value
        supported = {"en", "hi", "mr"}
        normalized: list[str] = []
        for item in value:
            lang = str(item or "").strip().lower()
            if not lang:
                continue
            if lang not in supported:
                raise ValueError("languages supports only: en, hi, mr")
            if lang not in normalized:
                normalized.append(lang)
        if not normalized:
            raise ValueError("At least one language is required")
        return normalized

    @model_validator(mode="after")
    def set_primary_language(self):
        self.language = self.languages[0]
        return self

    @field_validator("age_group")
    @classmethod
    def validate_age_group_value(cls, value):
        return validate_age_group(value)

    @field_validator("illustration_type", mode="before")
    @classmethod
    def normalize_illustration_type_value(cls, value):
        return normalize_illustration_type(value)


class GenericStoryWorkflowExecuteRequest(BaseModel):
    step_name: CustomStoryWorkflowStep | Literal["ALL"] = "ALL"
    skip_image_generation: bool = False
    skip_narration_generation: bool = True
    publish_status: Literal["active", "inactive"] | None = None


class GenericStoryWorkflowRetryRequest(BaseModel):
    skip_image_generation: bool = False
    skip_narration_generation: bool = True
    publish_status: Literal["active", "inactive"] | None = None
