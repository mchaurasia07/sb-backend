from uuid import UUID

from sqlalchemy import select
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

    async def get_by_story_page(self, story_id: UUID, page_number: int) -> StoryPage | None:
        result = await self.session.execute(
            select(StoryPage).where(
                StoryPage.story_id == story_id,
                StoryPage.page_number == page_number,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_page(
        self,
        story_id: UUID,
        page_number: int,
        page_type: str,
        text: str,
        image_prompt: str | None = None,
        image_url: str | None = None,
    ) -> StoryPage:
        page = await self.get_by_story_page(story_id, page_number)
        if page is None:
            return await self.create_page(
                story_id,
                page_number,
                page_type,
                text,
                image_prompt=image_prompt,
                image_url=image_url,
            )

        page.page_type = page_type
        page.text = text
        page.image_prompt = image_prompt
        page.image_url = image_url
        await self.session.flush()
        return page

    async def bulk_create_pages(self, story_id: UUID, pages_data: list[dict]) -> list[StoryPage]:
        """Create multiple pages in one operation."""
        pages = [StoryPage(story_id=story_id, **page_data) for page_data in pages_data]
        self.session.add_all(pages)
        await self.session.flush()
        return pages
