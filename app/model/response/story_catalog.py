from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class StoryCatalogResponse(BaseModel):
    """Story metadata for catalog APIs without full story_json content."""

    id: UUID
    story_type: Literal["generic", "custom"]
    title: str | None
    summary: str | None
    age_group: str | None
    theme: str | None
    genre: str | None = None
    moral: str | None
    learning_goal: str | None
    reading_time_minutes: int | None = None
    character_type: str | None = None
    total_pages: int | None = None
    available_languages: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
