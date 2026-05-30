from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import AuthException
from app.core.security import TokenType, decode_token
from app.entity.user import User
from app.entity.child_profile import ChildProfile
from app.repository.user_repository import UserRepository
from app.repository.child_repository import ChildRepository

bearer_scheme = HTTPBearer(auto_error=False)


class AuthContext:
    """Authentication context that can represent either a parent user or a child."""

    def __init__(
        self,
        token_payload: dict,
        user: User | None = None,
        child: ChildProfile | None = None,
    ):
        self.token_payload = token_payload
        self.user = user
        self.child = child
        self.is_child = token_payload.get("account_type") == "child"

    @property
    def user_id(self) -> UUID:
        """Get the parent user ID (works for both parent and child tokens)."""
        if self.is_child:
            return UUID(self.token_payload["parent_user_id"])
        return self.user.id if self.user else UUID(self.token_payload["sub"])

    @property
    def child_id(self) -> UUID | None:
        """Get the child ID if this is a child token."""
        if self.is_child:
            return UUID(self.token_payload["child_profile_id"])
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from a bearer access token (parent only)."""
    if credentials is None:
        raise AuthException("Authentication required", status_code=401, code="AUTH_REQUIRED")
    payload = decode_token(credentials.credentials, TokenType.ACCESS)

    # Reject child tokens
    if payload.get("account_type") == "child":
        raise AuthException("Parent user access required", status_code=403, code="PARENT_ACCESS_REQUIRED")

    user_id = UUID(payload["sub"])
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthException("User is inactive or not found", status_code=401, code="INVALID_USER")
    return user


async def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    """Resolve authentication context from either parent or child token."""
    if credentials is None:
        raise AuthException("Authentication required", status_code=401, code="AUTH_REQUIRED")

    payload = decode_token(credentials.credentials, TokenType.ACCESS)

    # Handle child token
    if payload.get("account_type") == "child":
        child_id = UUID(payload["child_profile_id"])
        parent_user_id = UUID(payload["parent_user_id"])

        child = await ChildRepository(session).get_for_user(parent_user_id, child_id)
        if child is None or not child.active:
            raise AuthException("Child profile is inactive or not found", status_code=401, code="INVALID_CHILD")

        return AuthContext(token_payload=payload, child=child)

    # Handle parent token
    user_id = UUID(payload["sub"])
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthException("User is inactive or not found", status_code=401, code="INVALID_USER")

    return AuthContext(token_payload=payload, user=user)
