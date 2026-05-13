from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie"}


class AuthenticationContextMiddleware(BaseHTTPMiddleware):
    """Prepare sanitized request auth context for downstream tooling."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        request.state.safe_headers = {
            key: value for key, value in request.headers.items() if key.lower() not in SENSITIVE_HEADERS
        }
        return await call_next(request)
