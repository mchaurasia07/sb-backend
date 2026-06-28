import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.story import Story
from app.repository.user_repository import UserRepository
from app.service.notification_service import NotificationService
from app.utils.email import email_client

logger = logging.getLogger(__name__)


class StoryCompletionEmailService:
    """Sends best-effort story completion emails."""

    def __init__(self, session: AsyncSession):
        self.users = UserRepository(session)

    async def send_story_completed(self, story: Story, story_json: dict[str, Any] | None = None) -> None:
        user = await self.users.get_by_id(story.user_id)
        if user is None or not user.email:
            logger.warning("story_completion_email_skipped_no_user", extra={"story_id": str(story.id)})
            return

        title = self._first_non_empty(
            story.title,
            story_json.get("title") if isinstance(story_json, dict) else None,
            "Your new story",
        )
        summary = self._first_non_empty(
            story.summary,
            story_json.get("summary") if isinstance(story_json, dict) else None,
        )

        try:
            await email_client.send_story_completed_email(
                user.email,
                story_title=title,
                story_summary=summary,
                story_input=getattr(story, "input_request", None),
            )
        except Exception:
            logger.exception(
                "story_completion_email_failed",
                extra={"story_id": str(story.id), "user_id": str(story.user_id), "email": user.email},
            )

        await NotificationService(self.users.session).send_story_completed_to_parent(
            user_id=story.user_id,
            story_id=story.id,
            story_title=title,
        )

    @staticmethod
    def _first_non_empty(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
