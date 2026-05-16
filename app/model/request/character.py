from pydantic import BaseModel, Field


class CharacterGenerationRequest(BaseModel):
    """Request to generate AI character from child profile photo."""

    additional_context: str | None = Field(
        None,
        max_length=500,
        description="Optional context like hobbies, personality traits, or styling preferences",
    )
