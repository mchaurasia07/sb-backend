from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class PushTokenRegisterRequest(BaseModel):
    expo_push_token: str = Field(min_length=10, max_length=255)
    device_id: str | None = Field(default=None, max_length=255)
    platform: Literal["ios", "android", "web", "unknown"] | None = "unknown"
    app_version: str | None = Field(default=None, max_length=64)

    @field_validator("expo_push_token", "device_id", "app_version", mode="before")
    @classmethod
    def strip_text(cls, value):
        if value is None or not isinstance(value, str):
            return value
        return value.strip() or None


class PushTokenUnregisterRequest(BaseModel):
    expo_push_token: str = Field(min_length=10, max_length=255)

    @field_validator("expo_push_token", mode="before")
    @classmethod
    def strip_token(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip()


class NotificationSendRequest(BaseModel):
    audience: Literal["all", "parents", "children", "parent_user", "child"]
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=1000)
    event_type: str = Field(default="manual", min_length=1, max_length=100)
    data: dict[str, Any] = Field(default_factory=dict)
    user_id: UUID | None = None
    child_id: UUID | None = None

    @field_validator("title", "body", "event_type", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if not isinstance(value, str):
            return value
        return " ".join(value.split())
