from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class GenericStoryResponse(BaseModel):
    id: UUID
    title: str
    summary: str | None
    age_group: str
    theme: str | None
    genre: str | None
    language: str
    moral: str | None
    learning_goal: str | None
    reading_time_minutes: int | None
    character_type: str | None
    total_pages: int
    cover_image: str | None
    story_json: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
