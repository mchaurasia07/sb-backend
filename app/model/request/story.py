from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.core.age_groups import AGE_GROUP_0_3, AGE_GROUP_3_6, AGE_GROUP_6_9, validate_age_group
from app.entity.custom_story_workflow import CustomStoryWorkflowType


class ReaderCategory(str, Enum):
    INFANT_TODDLER = "Infant Toddler"
    EARLY_READER = "Early Reader"
    GROWING_READER = "Growing Reader"


READER_CATEGORY_AGE_GROUPS = {
    ReaderCategory.INFANT_TODDLER: AGE_GROUP_0_3,
    ReaderCategory.EARLY_READER: AGE_GROUP_3_6,
    ReaderCategory.GROWING_READER: AGE_GROUP_6_9,
}

AGE_GROUP_READER_CATEGORIES = {
    AGE_GROUP_0_3: ReaderCategory.INFANT_TODDLER,
    AGE_GROUP_3_6: ReaderCategory.EARLY_READER,
    AGE_GROUP_6_9: ReaderCategory.GROWING_READER,
}

READER_CATEGORY_ALIASES = {
    "infant toddler": ReaderCategory.INFANT_TODDLER,
    "toddler": ReaderCategory.INFANT_TODDLER,
    "infant toddler (0 3 years)": ReaderCategory.INFANT_TODDLER,
    "0 3": ReaderCategory.INFANT_TODDLER,
    "early reader": ReaderCategory.EARLY_READER,
    "early reader (3 6 years)": ReaderCategory.EARLY_READER,
    "3 6": ReaderCategory.EARLY_READER,
    "growing reader": ReaderCategory.GROWING_READER,
    "advanced": ReaderCategory.GROWING_READER,
    "growing reader (6 9 years)": ReaderCategory.GROWING_READER,
    "6 9": ReaderCategory.GROWING_READER,
}


def normalize_reader_category(value) -> ReaderCategory:
    raw = getattr(value, "value", value)
    if isinstance(raw, ReaderCategory):
        return raw
    if not isinstance(raw, str):
        return raw

    normalized = " ".join(raw.strip().replace("_", " ").replace("-", " ").split()).lower()
    if normalized in READER_CATEGORY_ALIASES:
        return READER_CATEGORY_ALIASES[normalized]
    return raw


def age_group_for_reader_category(value) -> str:
    reader_category = normalize_reader_category(value)
    if reader_category not in READER_CATEGORY_AGE_GROUPS:
        return reader_category
    return READER_CATEGORY_AGE_GROUPS[reader_category]


def reader_category_for_age_group(value) -> ReaderCategory:
    return AGE_GROUP_READER_CATEGORIES[validate_age_group(value)]


class StoryGenerationRequest(BaseModel):
    """Request to create a custom or generic story workflow."""

    story_type: CustomStoryWorkflowType = Field(
        default=CustomStoryWorkflowType.CUSTOM,
        description="Workflow/story target type. CUSTOM requires child_id; GENERIC publishes to the generic catalog.",
    )
    child_id: UUID | None = Field(default=None, description="Child profile ID. Required only when story_type is CUSTOM.")
    reader_category: ReaderCategory = Field(
        description="Reading band used to derive age_group: Infant Toddler, Early Reader, or Growing Reader",
    )
    age_group: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="Canonical age group. Optional when reader_category is provided.",
    )
    use_child_character: bool = Field(
        False,
        description="Use the selected child's generated character as the story hero. When false, AI creates the cast.",
    )

    # Input-driven mode fields
    category: str | None = Field(None, max_length=100, description="Story category (e.g., 'adventure')")
    learning_goal: str | None = Field(None, max_length=500, description="Educational objective")
    context: str | None = Field(None, max_length=2000, description="Additional context or preferences")
    title: str | None = Field(default=None, min_length=1, max_length=255, description="Optional title idea for generic workflows.")
    actual_story: str | None = Field(default=None, min_length=1, max_length=50000, description="Legacy generic workflow story idea alias.")
    theme: str | None = Field(default=None, max_length=100, description="Legacy generic workflow theme alias.")
    genre: str | None = Field(default=None, max_length=100, description="Legacy generic workflow genre alias.")
    status: Literal["active", "inactive"] = Field(default="inactive", description="Publish status used when a generic workflow publishes.")
    languages: list[str] = Field(
        default_factory=lambda: ["en"],
        min_length=1,
        max_length=3,
        description="Story content/narration languages. Supported: en, hi, mr.",
    )
    language: str | None = Field(
        default=None,
        exclude=True,
        description="Legacy single-language request alias; use languages for new clients.",
    )

    # Testing flags
    skip_image_generation: bool = Field(False, description="Skip image generation for testing")
    execute_image: bool | None = Field(None, description="Generate story images. When omitted, this is derived from skip_image_generation.")
    execute_narration: Annotated[bool, Field(
        True,
        validation_alias=AliasChoices("execute_narration", "execute_narration"),
        description="Generate page narration audio",
    )]
    skip_validation: bool = Field(False, description="Skip validation steps for testing")
    execute_workflow: bool = Field(False, description="Start background workflow execution after saving. Defaults to false so create only saves the workflow.")

    @model_validator(mode="before")
    @classmethod
    def apply_language_alias(cls, data):
        if isinstance(data, dict) and "languages" not in data and data.get("language"):
            data = dict(data)
            data["languages"] = [data["language"]]
        return data

    @field_validator("category", "learning_goal", "context", "language", "title", "actual_story", "theme", "genre", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("languages", mode="before")
    @classmethod
    def normalize_languages(cls, value):
        if value is None:
            return ["en"]
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

    @field_validator("age_group", mode="before")
    @classmethod
    def validate_age_group_value(cls, value):
        if value is None:
            return None
        return validate_age_group(value)

    @field_validator("story_type", mode="before")
    @classmethod
    def normalize_story_type(cls, value):
        if isinstance(value, CustomStoryWorkflowType):
            return value
        if value is None:
            return CustomStoryWorkflowType.CUSTOM
        return CustomStoryWorkflowType(str(value).strip().upper())

    @model_validator(mode="after")
    def validate_story_inputs(self):
        if self.execute_image is None:
            self.execute_image = not self.skip_image_generation
        else:
            self.skip_image_generation = not self.execute_image
        self.language = self.languages[0]

        if self.story_type == CustomStoryWorkflowType.CUSTOM:
            if self.child_id is None:
                raise ValueError("child_id is required when story_type is CUSTOM")
        else:
            self.child_id = None
            self.use_child_character = False
            if self.category is None:
                self.category = self.theme or self.genre
            if self.context is None:
                self.context = self.actual_story

        if not (self.category or self.learning_goal or self.context):
            raise ValueError("Story workflows require category, learning_goal, or context")
        if self.execute_workflow and not self.execute_image and not self.execute_narration:
            raise ValueError("Executing a story workflow requires image or narration execution")
        return self


class BatchWebPConversionRequest(BaseModel):
    """Batch PNG to WebP conversion request."""

    story_ids: list[UUID] = Field(min_length=1, max_length=100, description="Story IDs to convert")
    quality: int = Field(default=85, ge=1, le=100, description="WebP quality 1-100")
