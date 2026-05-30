from typing import Literal

from pydantic import BaseModel, Field


class ChildBookStatusUpdateRequest(BaseModel):
    status: Literal["STARTED", "COMPLETED"]
    page_number: int | None = Field(default=None, ge=1)
    last_page_read: int | None = Field(default=None, ge=1)


class ChildBookProgressUpdateRequest(BaseModel):
    page_number: int = Field(ge=1)
