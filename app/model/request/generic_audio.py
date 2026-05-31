from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.entity.generic_audio import GenericAudioLanguage


class GenericAudioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    language: GenericAudioLanguage = GenericAudioLanguage.EN
    audio_url: str = Field(min_length=1, max_length=1024)
    image_url: str | None = Field(default=None, max_length=1024)
    description: str | None = None
    status: Literal["active", "inactive"] = "active"


class GenericAudioUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    language: GenericAudioLanguage | None = None
    audio_url: str | None = Field(default=None, min_length=1, max_length=1024)
    image_url: str | None = Field(default=None, max_length=1024)
    description: str | None = None
    status: Literal["active", "inactive"] | None = None


class AddGenericAudioToChildRequest(BaseModel):
    audio_id: UUID
