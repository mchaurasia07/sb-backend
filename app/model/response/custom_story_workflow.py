from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class CustomStoryWorkflowResponse(BaseModel):
    workflow_id: UUID
    request_number: int
    story_type: str = "CUSTOM"
    story_id: UUID | None
    generic_story_id: UUID | None = None
    child_id: UUID | None
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
    language: str | None = None
    languages: list[str] | None = None
    genre: str | None = None
    publish_status: str | None = None
    source_title: str | None = None
    input_request: dict[str, Any] | None = None
    use_child_character: bool = False
    execute_image: bool = True
    execute_narration: bool = True
    skip_validation: bool = False
    execute_workflow: bool = True
    title: str | None = None
    summary: str | None = None
    moral: str | None = None
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


class CustomStoryWorkflowBatchJobResponse(BaseModel):
    id: UUID
    workflow_id: UUID
    story_id: UUID | None
    generic_story_id: UUID | None = None
    job_type: str
    status: str
    provider: str
    provider_job_name: str | None
    provider_model: str | None
    provider_state: str | None
    attempt: int
    expected_item_count: int
    completed_item_count: int
    failed_item_count: int
    request_keys: list[str] | None
    missing_keys: list[str] | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class CustomStoryWorkflowBatchJobCancelResponse(BaseModel):
    workflow_id: UUID
    batch_job_id: UUID
    job_type: str
    status: str
    provider_job_name: str | None
    provider_state: str | None
    workflow_status: str
    message: str
