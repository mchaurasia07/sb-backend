"""Request DTOs for story narration endpoints."""

from pydantic import BaseModel, Field


class GenerateNarrationRequest(BaseModel):
    """Request to generate narration for a story."""

    overwrite: bool = Field(
        default=False, description="If true, regenerate narration even if audio already exists"
    )

    model_config = {"from_attributes": True}
