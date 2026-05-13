from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, HttpUrl


class ChildProfileResponse(BaseModel):
    id: UUID
    child_name: str
    age: int
    gender: str | None
    avatar_image_url: HttpUrl | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ActiveChildResponse(BaseModel):
    active_child_profile_id: UUID
