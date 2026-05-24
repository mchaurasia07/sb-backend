from datetime import datetime
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


class StoryStepResponse(BaseModel):
    """Audit trail for workflow step."""

    id: UUID
    step_name: str
    status: str
    retry_count: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
