from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.entity.user import AuthProvider, User


class UserRepository:
    """Persistence operations for users."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> User | None:
        result = await self.session.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    async def get_by_email_or_phone(self, identifier: str) -> User | None:
        result = await self.session.execute(select(User).where(or_(User.email == identifier.lower(), User.phone == identifier)))
        return result.scalar_one_or_none()

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        result = await self.session.execute(select(User).where(User.google_sub == google_sub))
        return result.scalar_one_or_none()

    async def create_local(self, email: str, phone: str, password_hash: str, first_name: str, last_name: str) -> User:
        user = User(
            email=email.lower(),
            phone=phone,
            password_hash=password_hash,
            first_name=first_name,
            last_name=last_name,
            auth_provider=AuthProvider.LOCAL,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def create_google(self, email: str, google_sub: str, first_name: str | None, last_name: str | None) -> User:
        user = User(
            email=email.lower(),
            google_sub=google_sub,
            first_name=first_name,
            last_name=last_name,
            auth_provider=AuthProvider.GOOGLE,
            is_email_verified=True,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def mark_email_verified(self, user: User) -> None:
        user.is_email_verified = True
        await self.session.flush()

    async def add_phone(self, user: User, phone: str) -> User:
        user.phone = phone
        user.is_phone_verified = False
        await self.session.flush()
        return user

    async def set_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.failed_login_attempts = 0
        user.locked_until = None
        await self.session.flush()

    async def register_failed_login(self, user: User) -> None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=settings.ACCOUNT_LOCK_MINUTES)
        await self.session.flush()

    async def clear_failed_logins(self, user: User) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        await self.session.flush()

    async def set_active_child_profile(self, user: User, child_profile_id: UUID) -> None:
        user.active_child_profile_id = child_profile_id
        await self.session.flush()

