from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    details: object | None = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    message: str
    data: T | None = None
    error: ErrorDetail | None = None


def success_response(data: T | None = None, message: str = "Operation successful") -> ApiResponse[T]:
    return ApiResponse(success=True, message=message, data=data, error=None)
