from pydantic import BaseModel, Field, HttpUrl


class ChildProfileCreateRequest(BaseModel):
    child_name: str = Field(min_length=1, max_length=120)
    age: int = Field(ge=0, le=18)
    gender: str | None = Field(default=None, max_length=32)
    avatar_image_url: HttpUrl | None = None


class ChildProfileUpdateRequest(BaseModel):
    child_name: str | None = Field(default=None, min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=0, le=18)
    gender: str | None = Field(default=None, max_length=32)
    avatar_image_url: HttpUrl | None = None
