import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.container import RequestContainer, get_request_container
from app.core.dependencies import get_current_user
from app.core.exceptions import AppException, AuthException
from app.entity.support import SupportQueryStatus
from app.entity.user import User
from app.model.request.support import AddSupportMessageRequest, CreateSupportQueryRequest
from app.model.response.support import (
    JugniSupportQueryListData,
    SupportDataResponse,
    SupportErrorResponse,
    SupportMessageResponse,
    SupportQueryClosed,
    SupportQueryCreated,
    SupportQueryDetail,
    SupportQueryListData,
    SupportSuccessResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "message": message},
    )


async def _server_error(
    container: RequestContainer,
    exc: SQLAlchemyError,
    message: str = "Unable to process support query. Please try again later.",
) -> JSONResponse:
    await container.session.rollback()
    logger.exception("support_database_error", exc_info=exc)
    return _error(
        message,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@router.post(
    "/query",
    response_model=SupportSuccessResponse[SupportQueryCreated],
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": SupportErrorResponse}, 500: {"model": SupportErrorResponse}},
)
async def create_support_query(
    payload: CreateSupportQueryRequest,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportSuccessResponse[SupportQueryCreated] | JSONResponse:
    try:
        data = await container.support.create_query(user_id=current_user.id, payload=payload)
    except ValueError as exc:
        return _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except SQLAlchemyError as exc:
        return await _server_error(
            container,
            exc,
            "Unable to create support query. Please try again later.",
        )
    return SupportSuccessResponse(
        message="Support query created successfully.",
        data=data,
    )


@router.get(
    "/jugni/queries",
    response_model=SupportDataResponse[JugniSupportQueryListData],
)
async def list_jugni_queries(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    pending_at_jugni: bool | None = Query(default=None),
    pending_at_user: bool | None = Query(default=None),
    query_status: SupportQueryStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportDataResponse[JugniSupportQueryListData] | JSONResponse:
    _ = current_user
    try:
        data = await container.support.list_jugni_queries(
            page=page,
            size=size,
            pending_at_jugni=pending_at_jugni,
            pending_at_user=pending_at_user,
            query_status=query_status,
        )
    except SQLAlchemyError as exc:
        return await _server_error(container, exc)
    return SupportDataResponse(data=data)


@router.get(
    "/queries",
    response_model=SupportDataResponse[SupportQueryListData],
)
async def list_support_queries(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportDataResponse[SupportQueryListData] | JSONResponse:
    try:
        data = await container.support.list_queries(user_id=current_user.id, page=page, size=size)
    except SQLAlchemyError as exc:
        return await _server_error(container, exc)
    return SupportDataResponse(data=data)


@router.get(
    "/queries/{query_id}",
    response_model=SupportDataResponse[SupportQueryDetail],
)
async def get_support_query(
    query_id: str,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportDataResponse[SupportQueryDetail] | JSONResponse:
    try:
        data = await container.support.get_query(user_id=current_user.id, query_id=query_id)
    except AppException as exc:
        return _error(exc.message, exc.status_code)
    except SQLAlchemyError as exc:
        return await _server_error(container, exc)
    return SupportDataResponse(data=data)


@router.put(
    "/jugni/queries/{query_id}/message",
    response_model=SupportSuccessResponse[SupportMessageResponse],
    responses={
        400: {"model": SupportErrorResponse},
        404: {"model": SupportErrorResponse},
        409: {"model": SupportErrorResponse},
    },
)
async def add_jugni_reply(
    query_id: str,
    payload: AddSupportMessageRequest,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportSuccessResponse[SupportMessageResponse] | JSONResponse:
    _ = current_user
    try:
        data = await container.support.add_jugni_reply(query_id=query_id, payload=payload)
    except ValueError as exc:
        return _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except AppException as exc:
        return _error(exc.message, exc.status_code)
    except SQLAlchemyError as exc:
        return await _server_error(container, exc)
    return SupportSuccessResponse(message="Jugni reply added successfully.", data=data)


@router.post(
    "/queries/{query_id}/message",
    response_model=SupportSuccessResponse[SupportMessageResponse],
    responses={400: {"model": SupportErrorResponse}, 409: {"model": SupportErrorResponse}},
)
async def add_support_message(
    query_id: str,
    payload: AddSupportMessageRequest,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportSuccessResponse[SupportMessageResponse] | JSONResponse:
    try:
        data = await container.support.add_message(
            user_id=current_user.id, query_id=query_id, payload=payload
        )
    except ValueError as exc:
        return _error(str(exc), status.HTTP_400_BAD_REQUEST)
    except AppException as exc:
        return _error(exc.message, exc.status_code)
    except SQLAlchemyError as exc:
        return await _server_error(container, exc)
    return SupportSuccessResponse(message="Message added successfully.", data=data)


@router.put(
    "/queries/{query_id}/close",
    response_model=SupportSuccessResponse[SupportQueryClosed],
)
async def close_support_query(
    query_id: str,
    current_user: User = Depends(get_current_user),
    container: RequestContainer = Depends(get_request_container),
) -> SupportSuccessResponse[SupportQueryClosed] | JSONResponse:
    try:
        data = await container.support.close_query(user_id=current_user.id, query_id=query_id)
    except AppException as exc:
        return _error(exc.message, exc.status_code)
    except SQLAlchemyError as exc:
        return await _server_error(container, exc)
    return SupportSuccessResponse(message="Support query closed successfully.", data=data)
