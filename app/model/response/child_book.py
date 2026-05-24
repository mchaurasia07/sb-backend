from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ChildBookResponse(BaseModel):
    id: UUID
    child_id: UUID
    story_id: UUID
    story_type: str
    language: str
    title: str
    cover_image: str | None
    status: str
    last_page_read: int
    last_page_read_time: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
