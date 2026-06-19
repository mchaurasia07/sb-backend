from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.age_groups import validate_age_group
from app.core.illustration_styles import DEFAULT_ILLUSTRATION_TYPE, normalize_illustration_type
from app.entity.custom_story_workflow import CustomStoryWorkflowStep
from app.model.request.story import ReaderCategory, age_group_for_reader_category, normalize_reader_category


class GenericStoryWorkflowCreateRequest(BaseModel):
    child_id: UUID | None = Field(default=None, description="Accepted for payload parity with custom stories; ignored.")
    reader_category: ReaderCategory | None = Field(
        default=None,
        description="Reading band used to derive age_group, matching custom story workflow payloads.",
    )
    use_child_character: bool = Field(
        default=False,
        description="Accepted for payload parity. Generic stories always use imagined-cast prompts.",
    )
    category: str | None = Field(default=None, max_length=100)
    learning_goal: str | None = Field(default=None, max_length=500)
    context: str | None = Field(default=None, max_length=2000)

    # Backward-compatible generic fields. New clients should prefer category/context.
    title: str | None = Field(default=None, min_length=1, max_length=255)
    actual_story: str | None = Field(default=None, min_length=20, max_length=50000)
    age_group: str | None = Field(default=None, min_length=1, max_length=32)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    languages: list[str] = Field(default_factory=lambda: ["en", "hi", "mr"], min_length=1, max_length=3)
    theme: str | None = Field(default=None, max_length=100)
    genre: str | None = Field(default=None, max_length=100)
    illustration_type: str = Field(default=DEFAULT_ILLUSTRATION_TYPE, min_length=1, max_length=64)
    status: Literal["active", "inactive"] = "inactive"
    skip_image_generation: bool = Field(False, description="Skip image generation for testing")
    execute_image: bool | None = Field(
        None,
        description="Generate story images. When omitted, this is derived from skip_image_generation.",
    )
    execute_narration: bool = Field(True, description="Generate page narration audio")
    skip_validation: bool = Field(False, description="Skip validation steps for testing")
    execute_workflow: bool = Field(True, description="Start workflow execution after creating the generic workflow.")

    @model_validator(mode="before")
    @classmethod
    def apply_language_alias(cls, data):
        if isinstance(data, dict) and "languages" not in data and data.get("language"):
            data = dict(data)
            data["languages"] = [data["language"]]
        return data

    @field_validator(
        "title",
        "actual_story",
        "age_group",
        "language",
        "category",
        "learning_goal",
        "context",
        "theme",
        "genre",
        mode="before",
    )
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

    @field_validator("reader_category", mode="before")
    @classmethod
    def normalize_reader_category_value(cls, value):
        return normalize_reader_category(value)

    @field_validator("illustration_type", mode="before")
    @classmethod
    def normalize_illustration_type_value(cls, value):
        return normalize_illustration_type(value)

    @model_validator(mode="after")
    def validate_story_inputs(self):
        if self.execute_image is None:
            self.execute_image = not self.skip_image_generation
        else:
            self.skip_image_generation = not self.execute_image

        self.language = self.languages[0]
        self.use_child_character = False

        if self.reader_category is not None:
            self.age_group = age_group_for_reader_category(self.reader_category)
        elif self.age_group is not None:
            self.age_group = validate_age_group(self.age_group)
        else:
            raise ValueError("Generic story workflows require reader_category or age_group")

        if self.category is None:
            self.category = self.theme or self.genre
        if self.context is None:
            self.context = self.actual_story

        if not (self.category or self.learning_goal or self.context):
            raise ValueError("Generic stories require category, learning_goal, or context")
        if not self.execute_image and not self.execute_narration:
            raise ValueError("Delayed generic stories require image or narration execution")
        return self


class GenericStoryWorkflowExecuteRequest(BaseModel):
    step_name: CustomStoryWorkflowStep | Literal["ALL"] = "ALL"
    skip_image_generation: bool = False
    skip_narration_generation: bool = True
    publish_status: Literal["active", "inactive"] | None = None


class GenericStoryWorkflowRetryRequest(BaseModel):
    skip_image_generation: bool = False
    skip_narration_generation: bool = True
    publish_status: Literal["active", "inactive"] | None = None
