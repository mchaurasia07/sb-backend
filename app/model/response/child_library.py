from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ChildLibraryBookResponse(BaseModel):
    """Book metadata for the child dashboard, driven by child_books."""

    child_book_id: UUID
    child_id: UUID
    story_id: UUID
    story_type: Literal["generic", "custom"]
    language: str
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
    cover_image: str | None = None
    story_status: str
    book_status: str
    last_page_read: int
    last_page_read_time: datetime | None
    reading_started_at: datetime | None
    reading_completed_at: datetime | None
    reading_started_count: int
    reading_completed_count: int
    created_at: datetime
    updated_at: datetime
