from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class StoryPageResponse(BaseModel):
    """Story page with text and image."""

    id: UUID
    page_number: int
    page_type: str
    text: str
    image_prompt: str | None
    image_url: str | None

    model_config = {"from_attributes": True}


class StoryResponse(BaseModel):
    """Complete story with metadata and pages."""

    id: UUID
    title: str | None
    moral: str | None
    summary: str | None
    status: str
    current_step: str | None
    generation_mode: str
    age_group: str
    category: str | None = None
    learning_goal: str | None = None
    context: str | None = None
    pages: list[StoryPageResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StoryStatusResponse(BaseModel):
    """Lightweight story generation status."""

    story_id: UUID
    status: str
    current_step: str | None
    error_message: str | None
    updated_at: datetime


class StoryStepResponse(BaseModel):
    """Audit trail for workflow step."""

    id: UUID
    step_name: str
    status: str
    retry_count: int
    error_message: str | None
    usage: dict[str, Any] | None = None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
