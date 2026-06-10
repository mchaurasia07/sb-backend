from enum import Enum
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.core.age_groups import AGE_GROUP_0_3, AGE_GROUP_3_6, AGE_GROUP_6_9, validate_age_group


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
    """Request to generate an input-driven custom story."""

    child_id: UUID = Field(description="Child profile ID for library ownership")
    reader_category: ReaderCategory = Field(
        description="Reading band used to derive age_group: Infant Toddler, Early Reader, or Growing Reader",
    )
    use_child_character: bool = Field(
        False,
        description="Use the selected child's generated character as the story hero. When false, AI creates the cast.",
    )

    # Input-driven mode fields
    category: str | None = Field(None, max_length=100, description="Story category (e.g., 'adventure')")
    learning_goal: str | None = Field(None, max_length=500, description="Educational objective")
    context: str | None = Field(None, max_length=2000, description="Additional context or preferences")

    # Testing flags
    skip_image_generation: bool = Field(False, description="Skip image generation for testing")
    execute_image: bool | None = Field(
        None,
        description="Generate story images. When omitted, this is derived from skip_image_generation.",
    )
    execute_narration: bool = Field(
        True,
        validation_alias=AliasChoices("execute_narration", "execute_narrration"),
        description="Generate page narration audio",
    )
    skip_validation: bool = Field(False, description="Skip validation steps for testing")
    execute_workflow: bool = Field(
        False,
        description="Start background workflow execution after saving. Defaults to false so create only saves the workflow.",
    )

    @field_validator("category", "learning_goal", "context", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("reader_category", mode="before")
    @classmethod
    def normalize_reader_category_value(cls, value):
        return normalize_reader_category(value)

    @model_validator(mode="after")
    def validate_story_inputs(self):
        if self.execute_image is None:
            self.execute_image = not self.skip_image_generation
        else:
            self.skip_image_generation = not self.execute_image

        if not (self.category or self.learning_goal or self.context):
            raise ValueError("Custom stories require category, learning_goal, or context")
        if not self.execute_image and not self.execute_narration:
            raise ValueError("Delayed custom stories require image or narration execution")
        return self
