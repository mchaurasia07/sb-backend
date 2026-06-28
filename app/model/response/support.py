from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class SupportErrorResponse(BaseModel):
    success: Literal[False] = False
    message: str


class SupportSuccessResponse(BaseModel, Generic[T]):
    success: Literal[True] = True
    message: str
    data: T


class SupportDataResponse(BaseModel, Generic[T]):
    success: Literal[True] = True
    data: T


class SupportQueryCreated(BaseModel):
    query_id: str
    subject: str
    status: str
    pending_at_user: bool
    pending_at_jugni: bool
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "query_id": "QRY_1000123",
                "subject": "Unable to generate my story",
                "status": "OPEN",
                "pending_at_user": False,
                "pending_at_jugni": True,
                "created_at": "2026-06-28T15:20:00Z",
            }
        },
    )


class SupportQueryListItem(BaseModel):
    query_id: str
    subject: str
    status: str
    pending_at_user: bool
    pending_at_jugni: bool
    created_at: datetime
    last_updated_at: datetime


class SupportQueryListData(BaseModel):
    page: int
    size: int
    total_records: int
    total_pages: int
    items: list[SupportQueryListItem]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "size": 20,
                "total_records": 1,
                "total_pages": 1,
                "items": [
                    {
                        "query_id": "QRY_1000123",
                        "subject": "Unable to generate my story",
                        "status": "OPEN",
                        "pending_at_user": False,
                        "pending_at_jugni": True,
                        "created_at": "2026-06-28T15:20:00Z",
                        "last_updated_at": "2026-06-28T17:00:00Z",
                    }
                ],
            }
        }
    )


class SupportMessageResponse(BaseModel):
    message_id: str
    sender: str
    message: str
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "message_id": "MSG003",
                "sender": "JUGNI",
                "message": "Your story is ready now.",
                "created_at": "2026-06-28T18:10:00Z",
            }
        },
    )


class SupportQueryDetail(BaseModel):
    query_id: str
    subject: str
    status: str
    pending_at_user: bool
    pending_at_jugni: bool
    created_at: datetime
    messages: list[SupportMessageResponse]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query_id": "QRY_1000123",
                "subject": "Unable to generate my story",
                "status": "IN_PROGRESS",
                "pending_at_user": False,
                "pending_at_jugni": True,
                "created_at": "2026-06-28T15:20:00Z",
                "messages": [
                    {
                        "message_id": "MSG001",
                        "sender": "USER",
                        "message": "My story has been processing for over 10 hours.",
                        "created_at": "2026-06-28T15:20:00Z",
                    }
                ],
            }
        }
    )


class JugniSupportQueryListData(BaseModel):
    page: int
    size: int
    total_records: int
    total_pages: int
    items: list[SupportQueryDetail]


class SupportQueryClosed(BaseModel):
    query_id: str
    status: Literal["CLOSED"]
    pending_at_user: bool
    pending_at_jugni: bool
    closed_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query_id": "QRY_1000123",
                "status": "CLOSED",
                "pending_at_user": False,
                "pending_at_jugni": True,
                "closed_at": "2026-06-28T19:00:00Z",
            }
        }
    )
