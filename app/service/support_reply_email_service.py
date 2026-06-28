import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.support import SupportQuery
from app.repository.user_repository import UserRepository
from app.utils.email import email_client

logger = logging.getLogger(__name__)


class SupportReplyEmailService:
    """Sends best-effort email notifications for Jugni support replies."""

    def __init__(self, session: AsyncSession):
        self.users = UserRepository(session)

    async def send_jugni_reply(
        self,
        *,
        query: SupportQuery,
        reply_message: str,
    ) -> None:
        try:
            user = await self.users.get_by_id(query.user_id)
            if user is None or not user.email:
                logger.warning(
                    "support_reply_email_skipped_no_user",
                    extra={
                        "query_id": query.query_id,
                        "user_id": str(query.user_id),
                    },
                )
                return
            await email_client.send_support_reply_email(
                user.email,
                query_id=query.query_id,
                query_subject=query.subject,
                reply_message=reply_message,
            )
        except Exception:
            logger.exception(
                "support_reply_email_failed",
                extra={
                    "query_id": query.query_id,
                    "user_id": str(query.user_id),
                },
            )
