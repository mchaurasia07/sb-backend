from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.entity.story_page import StoryPage


class StoryPageRepository:
    """Persistence operations for story pages."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_page(
        self,
        story_id: UUID,
        page_number: int,
        page_type: str,
        text: str,
        image_prompt: str | None = None,
        image_url: str | None = None,
    ) -> StoryPage:
        """Create a single story page."""
        page = StoryPage(
            story_id=story_id,
            page_number=page_number,
            page_type=page_type,
            text=text,
            image_prompt=image_prompt,
            image_url=image_url,
        )
        self.session.add(page)
        await self.session.flush()
        return page

    async def bulk_create_pages(self, story_id: UUID, pages_data: list[dict]) -> list[StoryPage]:
        """Create multiple pages in one operation."""
        pages = [StoryPage(story_id=story_id, **page_data) for page_data in pages_data]
        self.session.add_all(pages)
        await self.session.flush()
        return pages
