from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class GenericStoryWorkflowResponse(BaseModel):
    id: UUID
    workflow_name: str
    status: str
    current_step: str | None
    error_message: str | None
    generic_story_id: UUID | None
    actual_story: str
    age_group: str
    language: str
    languages: list[str] | None = None
    requested_pages: int | None
    title: str | None
    summary: str | None
    theme: str | None
    genre: str | None
    moral: str | None
    learning_goal: str | None
    cover_image: str | None
    character_analysis_json: dict[str, Any] | None
    scene_plan_json: dict[str, Any] | None
    visual_bible_json: dict[str, Any] | None
    story_json: dict[str, Any] | None
    image_plan_json: dict[str, Any] | None
    input_request: dict[str, Any] | None
    ai_provider: str
    text_model: str | None
    image_model: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class GenericStoryWorkflowListResponse(BaseModel):
    id: UUID
    user_id: UUID
    workflow_name: str
    status: str
    current_step: str | None
    error_message: str | None
    generic_story_id: UUID | None
    actual_story: str
    age_group: str
    language: str
    languages: list[str] | None = None
    requested_pages: int | None
    title: str | None
    summary: str | None
    theme: str | None
    genre: str | None
    moral: str | None
    learning_goal: str | None
    cover_image: str | None
    ai_provider: str
    text_model: str | None
    image_model: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class GenericStoryWorkflowStepDetailResponse(BaseModel):
    id: UUID | None = None
    workflow_id: UUID
    genric_story_id: str | None = None
    step_name: str
    status: str
    summary: dict[str, Any]
    input: dict[str, Any] | None = None
    prompt: str | None = None
    output: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
