from uuid import UUID

from pydantic import BaseModel, Field


class StoryGenerationRequest(BaseModel):
    """Request to generate a story (supports input-driven and event-driven modes)."""

    child_id: UUID = Field(description="Child profile ID for character image reference")
    mode: str = Field(description="Generation mode: 'INPUT_DRIVEN' or 'EVENT_DRIVEN'")

    # Input-driven mode fields
    category: str | None = Field(None, max_length=100, description="Story category (e.g., 'adventure')")
    learning_goal: str | None = Field(None, max_length=500, description="Educational objective")
    context: str | None = Field(None, description="Additional context or preferences")

    # Event-driven mode fields
    event_description: str | None = Field(None, description="User's event description to convert to story")

    # Testing flags
    skip_image_generation: bool = Field(False, description="Skip image generation for testing")
    skip_validation: bool = Field(False, description="Skip validation steps for testing")
