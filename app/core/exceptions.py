from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError

from app.core.logger import get_logger
from app.model.response.common import ApiResponse, ErrorDetail

logger = get_logger(__name__)


class AppException(Exception):
    """Base application exception with API-safe metadata."""

    def __init__(self, message: str, status_code: int = status.HTTP_400_BAD_REQUEST, code: str = "APP_ERROR"):
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class AuthException(AppException):
    """Authentication or authorization failure."""


class NotFoundException(AppException):
    """Requested resource does not exist."""

    def __init__(self, message: str, code: str = "NOT_FOUND"):
        super().__init__(message, status_code=status.HTTP_404_NOT_FOUND, code=code)


class ConflictException(AppException):
    """Requested operation conflicts with existing state."""


def error_response(message: str, status_code: int, code: str, details: Any = None) -> JSONResponse:
    payload = ApiResponse[None](
        success=False,
        message=message,
        data=None,
        error=ErrorDetail(code=code, details=details),
    ).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=payload)


async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
    return error_response(exc.message, exc.status_code, exc.code)


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response("Validation failed", status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", exc.errors())


async def database_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("database_error", path=request.url.path, exc_info=exc)
    return error_response("Database operation failed", status.HTTP_500_INTERNAL_SERVER_ERROR, "DATABASE_ERROR")


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path, exc_info=exc)
    return error_response("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_SERVER_ERROR")


async def rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    return error_response("Rate limit exceeded", status.HTTP_429_TOO_MANY_REQUESTS, "RATE_LIMIT_EXCEEDED", str(exc.detail))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, database_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
