from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class ChildProfileResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    dob: date | None
    age: int
    gender: str | None
    avatar_image_url: str | None
    character_image_url: str | None = None
    child_user_id: str
    child_password: str
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ActiveChildResponse(BaseModel):
    active_child_profile_id: UUID


class ChildUsernameAvailabilityResponse(BaseModel):
    child_user_id: str
    available: bool
    reason: str | None = None
