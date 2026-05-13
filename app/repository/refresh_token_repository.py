from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.refresh_token import RefreshToken


class RefreshTokenRepository:
    """Persistence operations for refresh tokens."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: UUID, token_hash: str, expires_at: datetime) -> RefreshToken:
        refresh_token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(refresh_token)
        await self.session.flush()
        return refresh_token

    async def get_valid(self, token_hash: str) -> RefreshToken | None:
        result = await self.session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked.is_(False),
                RefreshToken.expires_at > datetime.now(UTC),
            )
        )
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken, replaced_by_token_hash: str | None = None) -> None:
        token.is_revoked = True
        token.replaced_by_token_hash = replaced_by_token_hash
        await self.session.flush()
