from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ChildActivityResponse(BaseModel):
    id: UUID
    child_id: UUID
    activity_name: str
    activity_type: str
    occurred_at: datetime
    resource_name: str | None
    resource_id: UUID | None
    resource_type: str | None
    description: str | None
    metadata_json: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
