from datetime import date

from pydantic import BaseModel, Field, HttpUrl


class ChildProfileCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    dob: date
    age: int = Field(ge=0, le=18)
    gender: str | None = Field(default=None, max_length=32)


class ChildProfileUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=60)
    last_name: str | None = Field(default=None, min_length=1, max_length=60)
    dob: date | None = None
    age: int | None = Field(default=None, ge=0, le=18)
    gender: str | None = Field(default=None, max_length=32)
    avatar_image_url: HttpUrl | None = None


class ChildUsernameUpdateRequest(BaseModel):
    child_user_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")


class ChildPasswordUpdateRequest(BaseModel):
    child_password: str = Field(min_length=4, max_length=128)


class ChildAccountStatusUpdateRequest(BaseModel):
    active: bool = False
