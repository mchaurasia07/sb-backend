from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import AuthException
from app.core.security import TokenType, decode_token
from app.entity.user import User
from app.repository.user_repository import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from a bearer access token."""
    if credentials is None:
        raise AuthException("Authentication required", status_code=401, code="AUTH_REQUIRED")
    payload = decode_token(credentials.credentials, TokenType.ACCESS)
    user_id = UUID(payload["sub"])
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthException("User is inactive or not found", status_code=401, code="INVALID_USER")
    return user
