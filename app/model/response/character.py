from pydantic import BaseModel


class CharacterGenerationResponse(BaseModel):
    """Response from character generation operation."""

    character_image_url: str
    character_description: str

    model_config = {"from_attributes": True}
