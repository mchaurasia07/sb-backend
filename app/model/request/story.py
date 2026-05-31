from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class StoryGenerationRequest(BaseModel):
    """Request to generate a story (supports input-driven and event-driven modes)."""

    child_id: UUID = Field(description="Child profile ID for character image reference")
    mode: Literal["INPUT_DRIVEN", "EVENT_DRIVEN"] = Field(description="Generation mode")

    # Input-driven mode fields
    category: str | None = Field(None, max_length=100, description="Story category (e.g., 'adventure')")
    learning_goal: str | None = Field(None, max_length=500, description="Educational objective")
    context: str | None = Field(None, max_length=2000, description="Additional context or preferences")

    # Event-driven mode fields
    event_description: str | None = Field(None, max_length=2000, description="User's event description to convert to story")

    # Testing flags
    skip_image_generation: bool = Field(False, description="Skip image generation for testing")
    skip_validation: bool = Field(False, description="Skip validation steps for testing")

    @field_validator("category", "learning_goal", "context", "event_description", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def validate_mode_inputs(self):
        if self.mode == "INPUT_DRIVEN" and not (self.category or self.learning_goal or self.context):
            raise ValueError("INPUT_DRIVEN stories require category, learning_goal, or context")
        if self.mode == "EVENT_DRIVEN" and not self.event_description:
            raise ValueError("EVENT_DRIVEN stories require event_description")
        return self
