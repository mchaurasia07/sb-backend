from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class CustomStoryWorkflowResponse(BaseModel):
    workflow_id: UUID
    request_number: int
    story_id: UUID | None
    child_id: UUID
    status: str
    current_step: str | None
    error_message: str | None = None
    generation_mode: str
    processing_mode: str
    reader_category: str | None = None
    age_group: str | None = None
    category: str | None = None
    learning_goal: str | None = None
    context: str | None = None
    event_description: str | None = None
    title: str | None = None
    summary: str | None = None
    moral: str | None = None
    input_request: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class CustomStoryWorkflowStepResponse(BaseModel):
    id: UUID
    workflow_id: UUID
    step_name: str
    status: str
    input: dict[str, Any] | None = None
    prompt: str | None = None
    output: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
