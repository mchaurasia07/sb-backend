from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PushTokenResponse(BaseModel):
    id: UUID
    account_type: str
    user_id: UUID
    child_id: UUID | None
    expo_push_token: str
    device_id: str | None
    platform: str | None
    app_version: str | None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationSendResponse(BaseModel):
    notification_id: UUID
    status: str
    audience: str
    target_count: int
    sent_count: int
    failed_count: int
