from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
    available_languages: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GenericStoryListResponse(BaseModel):
    id: UUID
    title: str
    summary: str | None
    age_group: str
    theme: str | None
    genre: str | None
    moral: str | None
    learning_goal: str | None
    reading_time_minutes: int | None
    character_type: str | None
    total_pages: int
    cover_image: str | None
    available_languages: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GenericStoryImageUploadResponse(BaseModel):
    workflow_id: UUID
    generic_story_id: UUID
    cover_image_url: str
    page_image_urls: dict[int, str]
    reduced_cover_image_url: str | None = None
    reduced_page_image_urls: dict[int, str] = Field(default_factory=dict)
    updated_languages: list[str]


class GenericStoryAudioUploadResponse(BaseModel):
    workflow_id: UUID
    generic_story_id: UUID
    language: str
    page_audio_urls: dict[int, str]
    updated_languages: list[str]


class GenericStoryBatchJobResponse(BaseModel):
    id: UUID
    generic_story_id: UUID | None
    workflow_id: UUID
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


class GenericStoryBatchJobCancelResponse(BaseModel):
    generic_story_id: UUID
    workflow_id: UUID
    batch_job_id: UUID
    job_type: str
    status: str
    provider_job_name: str | None
    provider_state: str | None
    workflow_status: str
    message: str
