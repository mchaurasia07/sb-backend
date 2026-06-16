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
    video_created: bool = False
    video_metadata: dict[str, Any] | None = None
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


class StoryBatchJobCancelResponse(BaseModel):
    """Result of a provider-backed story batch job cancellation."""

    story_id: UUID
    batch_job_id: UUID
    job_type: str
    status: str
    provider_job_name: str | None
    provider_state: str | None
    story_status: str
    message: str


class StoryBatchJobReconcileItemResponse(BaseModel):
    """Single batch-job reconciliation result."""

    story_id: UUID | None = None
    workflow_id: UUID | None = None
    batch_job_id: UUID
    job_type: str
    status: str
    provider_state: str | None = None
    action: str
    message: str | None = None


class StoryBatchJobReconcileResponse(BaseModel):
    """Manual reconciliation summary for provider-backed story batch jobs."""

    checked_count: int
    processed_count: int
    results: list[StoryBatchJobReconcileItemResponse]


class StoryVideoResponse(BaseModel):
    """Language-specific custom story video generation state."""

    story_id: UUID
    language: str
    status: str
    video_url: str | None = None
    local_video_path: str | None = None
    error_message: str | None = None
    requested_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None
    elapsed_seconds: float | None = None
    total_seconds: float | None = None
    queued_seconds: float | None = None
    timing: dict[str, Any] | None = None


class StoryWebPConversionResult(BaseModel):
    """Result for single story WebP conversion."""

    story_id: UUID
    status: str
    images_converted: int | None = None
    languages_updated: list[str] | None = None
    original_size_mb: float | None = None
    converted_size_mb: float | None = None
    compression_ratio: float | None = None
    error: str | None = None


class BatchWebPConversionResponse(BaseModel):
    """Batch WebP conversion response."""

    total_stories: int
    successful: int
    failed: int
    results: list[StoryWebPConversionResult]
