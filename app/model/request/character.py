from pydantic import BaseModel, Field


class CharacterGenerationRequest(BaseModel):
    """Request to generate AI character from child profile photo."""

    ai_provider: str | None = Field(
        None,
        description="Optional AI provider override for character generation. Defaults to AI_PROVIDER from environment.",
        pattern="^(openai|google)$",
    )
    additional_context: str | None = Field(
        None,
        max_length=500,
        description="Optional context like hobbies, personality traits, or styling preferences",
    )
