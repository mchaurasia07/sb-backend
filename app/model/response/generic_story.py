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
    updated_languages: list[str]


class GenericStoryAudioUploadResponse(BaseModel):
    workflow_id: UUID
    generic_story_id: UUID
    language: str
    page_audio_urls: dict[int, str]
    updated_languages: list[str]


class GenericStoryBatchImageSubmitResponse(BaseModel):
    generic_story_id: UUID
    workflow_id: UUID
    batch_job_id: UUID | None = None
    job_type: str
    status: str
    provider_job_name: str | None = None
    provider_state: str | None = None
    expected_item_count: int
    submitted_item_count: int
    message: str


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
