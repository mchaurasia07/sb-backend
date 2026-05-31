from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GenericAudioResponse(BaseModel):
    id: UUID
    name: str
    language: str
    audio_url: str
    image_url: str | None
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
