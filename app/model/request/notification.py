from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


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


class NotificationTargetRequest(BaseModel):
    type: Literal["all", "parents", "children", "parent_user", "child", "custom"]
    user_id: UUID | None = None
    child_id: UUID | None = None
    user_ids: list[UUID] = Field(default_factory=list)
    child_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target(self):
        if self.type == "parent_user" and self.user_id is None:
            raise ValueError("user_id is required for parent_user target")
        if self.type == "child" and self.child_id is None:
            raise ValueError("child_id is required for child target")
        if self.type == "custom" and not self.user_ids and not self.child_ids:
            raise ValueError("custom target requires at least one user_id or child_id")
        return self


class NotificationContentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=1000)
    event_type: str = Field(default="manual", min_length=1, max_length=100)
    route: str | None = Field(default=None, max_length=100)
    fallback_route: str | None = Field(default=None, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "body", "event_type", "route", "fallback_route", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None or not isinstance(value, str):
            return value
        return " ".join(value.split()) or None


class NotificationDeliveryOptionsRequest(BaseModel):
    channel_id: str | None = Field(default=None, max_length=100)
    priority: Literal["default", "normal", "high"] = "high"
    sound: str | None = Field(default="default", max_length=100)

    @field_validator("channel_id", "sound", mode="before")
    @classmethod
    def strip_optional_text(cls, value):
        if value is None or not isinstance(value, str):
            return value
        return value.strip() or None


class NotificationAsyncSendRequest(BaseModel):
    target: NotificationTargetRequest
    notification: NotificationContentRequest
    delivery: NotificationDeliveryOptionsRequest = Field(default_factory=NotificationDeliveryOptionsRequest)
