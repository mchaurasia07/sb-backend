from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.age_groups import age_group_label
from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.entity.generic_story import GenericStory, GenericStoryContent
from app.entity.generic_story_workflow import GenericStoryWorkflow
from app.repository.generic_story_repository import GenericStoryRepository
from app.repository.generic_story_workflow_repository import GenericStoryWorkflowRepository
from app.service.ai.google_provider import DEFAULT_GEMINI_IMAGE_MODEL, GoogleProvider
from app.service.generic_story_workflow_service import GenericStoryWorkflowService
from app.service.image_storage_provider import get_image_storage_service
from app.service.visual_bible_prompt_context import compact_visual_bible_json_for_image_prompt
from app.utils.prompt_loader import load_and_render_prompt


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class MultiImageStoryItem:
    item_id: str
    page_type: str
    page_number: int | None
    filename: str
    aspect_ratio: str
    page_image_plan: dict[str, Any]
    story_page: dict[str, Any]


class GenericStoryMultiImageTestService:
    """Test-only generic story image generation using Gemini interleaved output."""

    PROMPT_PATH = "prompts/generic_story/multi_image_generation_prompt.txt"

    def __init__(
        self,
        session: AsyncSession,
        *,
        ai_provider: GoogleProvider | None = None,
        image_storage: Any | None = None,
    ):
        self.session = session
        self.generic_stories = GenericStoryRepository(session)
        self.workflows = GenericStoryWorkflowRepository(session)
        if ai_provider is None and not settings.GOOGLE_API_KEY:
            raise AppException(
                "GOOGLE_API_KEY is required for generic story multi-image test generation.",
                code="GENERIC_MULTI_IMAGE_GOOGLE_API_KEY_MISSING",
            )
        self.ai_provider = ai_provider or GoogleProvider(
            api_key=settings.GOOGLE_API_KEY,
            image_model=settings.GOOGLE_IMAGE_MODEL,
            text_model=settings.GOOGLE_TEXT_MODEL,
            reference_image_model=settings.GOOGLE_REFERENCE_IMAGE_MODEL,
        )
        self.image_storage = image_storage or get_image_storage_service()

    async def generate(
        self,
        user_id: UUID,
        generic_story_id: UUID,
        *,
        language: str = "en",
        public_base_url: str = "",
        model: str = DEFAULT_GEMINI_IMAGE_MODEL,
    ) -> dict[str, Any]:
        generic_story = await self.generic_stories.get_by_id(generic_story_id)
        if generic_story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        workflow = await self.workflows.latest_for_user_generic_story(user_id, generic_story_id)
        if workflow is None:
            raise AppException(
                "No workflow checkpoint found for this generic story.",
                code="GENERIC_MULTI_IMAGE_WORKFLOW_MISSING",
                details={"generic_story_id": str(generic_story_id)},
            )

        image_plan = self._require_image_plan(workflow)
        visual_bible = self._require_visual_bible(workflow)
        source_content = self._select_source_content(generic_story, language)
        story_json = deepcopy(source_content.story_json)
        story_pages = self._require_story_pages(story_json)

        items = self._build_image_items(
            generic_story=generic_story,
            workflow=workflow,
            story_json=story_json,
            image_plan=image_plan,
            story_pages=story_pages,
        )
        grouped_items = self._group_items_by_aspect_ratio(items)

        saved_urls: dict[str, str] = {}
        call_summaries: list[dict[str, Any]] = []
        for group_name, group_items in grouped_items.items():
            aspect_ratio = group_items[0].aspect_ratio
            prompt = self._render_group_prompt(
                group_name=group_name,
                items=group_items,
                story_title=self._story_title(generic_story, workflow, story_json),
                age_group=age_group_label(generic_story.age_group or workflow.age_group),
                visual_bible=visual_bible,
            )
            result = await self.ai_provider.generate_interleaved_images(
                prompt,
                expected_count=len(group_items),
                aspect_ratio=aspect_ratio,
                model=model,
            )
            if len(result.images) != len(group_items):
                raise AppException(
                    f"Gemini returned {len(result.images)} images for {group_name}; expected {len(group_items)}.",
                    code="GENERIC_MULTI_IMAGE_COUNT_MISMATCH",
                    details={
                        "group": group_name,
                        "expected_count": len(group_items),
                        "received_count": len(result.images),
                    },
                )

            saved_items: list[dict[str, Any]] = []
            for item, image in zip(group_items, result.images, strict=True):
                image_url = await self.image_storage.save_story_image(
                    generic_story.id,
                    image.image_bytes,
                    item.filename,
                    public_base_url,
                )
                saved_urls[item.item_id] = image_url
                saved_items.append(
                    {
                        "item_id": item.item_id,
                        "page_type": item.page_type,
                        "page_number": item.page_number,
                        "filename": item.filename,
                        "image_url": image_url,
                        "mime_type": image.mime_type,
                        "marker_text": image.preceding_text,
                    }
                )

            metadata = result.metadata or {}
            call_summaries.append(
                {
                    "group": group_name,
                    "aspect_ratio": aspect_ratio,
                    "requested_count": len(group_items),
                    "received_count": len(result.images),
                    "items": saved_items,
                    "prompt": prompt,
                    "usage": metadata.get("usage"),
                    "response_text": metadata.get("image_response_text"),
                }
            )

        await self._apply_saved_urls_to_all_contents(generic_story, saved_urls)

        return {
            "generic_story_id": str(generic_story.id),
            "workflow_id": str(workflow.id),
            "model": model,
            "story_json_updated": True,
            "groups": call_summaries,
        }

    @classmethod
    def _require_image_plan(cls, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        image_plan = workflow.image_plan_json if isinstance(workflow.image_plan_json, dict) else None
        pages = image_plan.get("pages") if isinstance(image_plan, dict) else None
        if not isinstance(image_plan, dict) or not isinstance(pages, list) or not pages:
            raise AppException(
                "Latest workflow has no usable image_plan_json pages.",
                code="GENERIC_MULTI_IMAGE_IMAGE_PLAN_MISSING",
                details={"workflow_id": str(getattr(workflow, "id", ""))},
            )
        return image_plan

    @classmethod
    def _require_visual_bible(cls, workflow: GenericStoryWorkflow) -> dict[str, Any]:
        visual_bible = workflow.visual_bible_json if isinstance(workflow.visual_bible_json, dict) else None
        if not isinstance(visual_bible, dict) or not visual_bible:
            image_plan = workflow.image_plan_json if isinstance(workflow.image_plan_json, dict) else {}
            visual_bible = image_plan.get("visual_bible") if isinstance(image_plan.get("visual_bible"), dict) else None
        if not isinstance(visual_bible, dict) or not visual_bible:
            scene_plan = workflow.scene_plan_json if isinstance(workflow.scene_plan_json, dict) else {}
            visual_bible = scene_plan.get("visual_bible") if isinstance(scene_plan.get("visual_bible"), dict) else None
        if not isinstance(visual_bible, dict) or not visual_bible:
            raise AppException(
                "Latest workflow has no usable visual_bible_json.",
                code="GENERIC_MULTI_IMAGE_VISUAL_BIBLE_MISSING",
                details={"workflow_id": str(getattr(workflow, "id", ""))},
            )
        return visual_bible

    @staticmethod
    def _select_source_content(generic_story: GenericStory, language: str) -> GenericStoryContent:
        normalized_language = (language or "en").strip().lower()
        contents = list(getattr(generic_story, "contents", []) or [])
        content = next((item for item in contents if str(item.language).lower() == normalized_language), None)
        if content is None:
            content = next((item for item in contents if str(item.language).lower() == "en"), None)
        if content is None and contents:
            content = contents[0]
        if content is None:
            raise AppException(
                "Generic story has no language content.",
                code="GENERIC_MULTI_IMAGE_CONTENT_MISSING",
            )
        return content

    @staticmethod
    def _require_story_pages(story_json: dict[str, Any]) -> list[dict[str, Any]]:
        pages = story_json.get("pages") if isinstance(story_json, dict) else None
        if not isinstance(pages, list) or not pages:
            raise AppException(
                "Generic story content has no pages array.",
                code="GENERIC_MULTI_IMAGE_STORY_PAGES_MISSING",
            )
        normalized_pages = [page for page in pages if isinstance(page, dict)]
        if len(normalized_pages) != len(pages):
            raise AppException(
                "Generic story content pages must be JSON objects.",
                code="GENERIC_MULTI_IMAGE_STORY_PAGES_INVALID",
            )
        return normalized_pages

    @classmethod
    def _build_image_items(
        cls,
        *,
        generic_story: GenericStory,
        workflow: GenericStoryWorkflow,
        story_json: dict[str, Any],
        image_plan: dict[str, Any],
        story_pages: list[dict[str, Any]],
    ) -> list[MultiImageStoryItem]:
        page_plans_by_number = cls._page_plans_by_number(image_plan)
        title = cls._story_title(generic_story, workflow, story_json)
        cover_plan = cls._cover_plan(image_plan, title)
        items: list[MultiImageStoryItem] = [
            MultiImageStoryItem(
                item_id="cover",
                page_type="cover",
                page_number=None,
                filename="cover.png",
                aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                page_image_plan=cover_plan,
                story_page={},
            )
        ]

        for fallback_page_number, story_page in enumerate(story_pages, start=1):
            page_number = cls._page_number(story_page, fallback=fallback_page_number)
            page_plan = page_plans_by_number.get(page_number)
            if page_plan is None:
                raise AppException(
                    f"Image plan is missing page {page_number}.",
                    code="GENERIC_MULTI_IMAGE_PLAN_PAGE_MISSING",
                    details={"page_number": page_number},
                )
            items.append(
                MultiImageStoryItem(
                    item_id=f"page_{page_number}",
                    page_type="story_page",
                    page_number=page_number,
                    filename=f"page_{page_number}.png",
                    aspect_ratio=settings.STORY_PAGE_ASPECT_RATIO,
                    page_image_plan=page_plan,
                    story_page=story_page,
                )
            )

        items.append(
            MultiImageStoryItem(
                item_id="back_cover",
                page_type="back_cover",
                page_number=None,
                filename="back_cover.png",
                aspect_ratio=settings.STORY_COVER_ASPECT_RATIO,
                page_image_plan=cls._back_cover_plan(image_plan, story_pages[-1]),
                story_page=story_pages[-1],
            )
        )
        return items

    @staticmethod
    def _page_plans_by_number(image_plan: dict[str, Any]) -> dict[int, dict[str, Any]]:
        pages_by_number: dict[int, dict[str, Any]] = {}
        for fallback, page in enumerate(image_plan.get("pages") or [], start=1):
            if not isinstance(page, dict):
                continue
            page_number = GenericStoryWorkflowService._image_plan_page_number(page)
            if page_number is None:
                page_number = GenericStoryMultiImageTestService._page_number(page, fallback=fallback)
            pages_by_number[page_number] = page
        return pages_by_number

    @staticmethod
    def _page_number(page: dict[str, Any], *, fallback: int) -> int:
        raw_page_number = page.get("page_number", page.get("page", fallback))
        if isinstance(raw_page_number, bool):
            return fallback
        try:
            page_number = int(raw_page_number)
        except (TypeError, ValueError):
            return fallback
        return page_number if page_number > 0 else fallback

    @staticmethod
    def _cover_plan(image_plan: dict[str, Any], title: str) -> dict[str, Any]:
        cover_plan = image_plan.get("cover") if isinstance(image_plan.get("cover"), dict) else {}
        if GenericStoryWorkflowService._image_plan_summary(cover_plan):
            return cover_plan
        synthesized = dict(cover_plan)
        synthesized["image_prompt"] = (
            f"Create a warm, premium children's storybook cover for '{title}'. Show the main character and "
            "central story setting with a clear emotional promise, child-safe mood, consistent character design, "
            "and clean natural space for the exact title text."
        )
        synthesized.setdefault("composition_type", "cover_composition")
        synthesized.setdefault("lighting_mood", "warm_inviting")
        synthesized.setdefault("dominant_palette", "harmonious story palette")
        return synthesized

    @staticmethod
    def _back_cover_plan(image_plan: dict[str, Any], final_story_page: dict[str, Any]) -> dict[str, Any]:
        explicit = image_plan.get("back_cover")
        if isinstance(explicit, dict) and GenericStoryWorkflowService._image_plan_summary(explicit):
            return explicit

        final_page_plan = GenericStoryMultiImageTestService._final_page_plan(image_plan)
        final_prompt = GenericStoryWorkflowService._image_plan_summary(final_page_plan)
        final_text = str(final_story_page.get("text") or "").strip()
        synthesized_prompt = (
            "Create a peaceful back cover illustration based on the final story moment. "
            "Use the same characters, outfits, setting style, lighting, and palette from the book. "
            "The mood should feel complete, warm, calm, and emotionally satisfying. "
            "Do not include title text, labels, signs, captions, or any readable words."
        )
        if final_prompt:
            synthesized_prompt += f" Final visual moment reference: {final_prompt}"
        elif final_text:
            synthesized_prompt += f" Final story text reference: {final_text}"

        return {
            "image_prompt": synthesized_prompt,
            "composition_type": "peaceful_back_cover",
            "lighting_mood": "warm_calm_closing",
            "dominant_palette": "soft harmonious closing palette",
            "image_negative": "no title text, no labels, no readable words, no watermark",
        }

    @staticmethod
    def _final_page_plan(image_plan: dict[str, Any]) -> dict[str, Any]:
        pages = image_plan.get("pages") if isinstance(image_plan.get("pages"), list) else []
        for page in reversed(pages):
            if isinstance(page, dict):
                return page
        return {}

    @staticmethod
    def _group_items_by_aspect_ratio(
        items: list[MultiImageStoryItem],
    ) -> dict[str, list[MultiImageStoryItem]]:
        cover_items = [item for item in items if item.page_type in {"cover", "back_cover"}]
        page_items = [item for item in items if item.page_type == "story_page"]
        grouped: dict[str, list[MultiImageStoryItem]] = {}
        if page_items:
            grouped["pages"] = page_items
        if cover_items:
            grouped["cover_back_cover"] = cover_items
        return grouped

    def _render_group_prompt(
        self,
        *,
        group_name: str,
        items: list[MultiImageStoryItem],
        story_title: str,
        age_group: str,
        visual_bible: dict[str, Any],
    ) -> str:
        item_payloads = [
            self._prompt_item_payload(item, visual_bible=visual_bible)
            for item in items
        ]
        return load_and_render_prompt(
            self.PROMPT_PATH,
            {
                "group_name": group_name,
                "story_title": story_title,
                "age_group": age_group,
                "target_aspect_ratio": items[0].aspect_ratio,
                "item_count": len(items),
                "item_order": "\n".join(f"- {item.item_id}" for item in items),
                "items_json": _compact_json(item_payloads),
                "global_visual_context": _compact_json(self._global_visual_context(visual_bible)),
            },
        )

    @staticmethod
    def _prompt_item_payload(item: MultiImageStoryItem, *, visual_bible: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "page_type": item.page_type,
            "page_number": item.page_number,
            "filename": item.filename,
            "target_aspect_ratio": item.aspect_ratio,
            "page_image_plan": item.page_image_plan,
            "source_image_prompt": GenericStoryWorkflowService._image_plan_summary(item.page_image_plan),
            "story_page": GenericStoryMultiImageTestService._story_page_context(item.story_page),
            "visual_context": compact_visual_bible_json_for_image_prompt(
                visual_bible,
                page_type=item.page_type,
                image_brief=item.page_image_plan,
                scene_plan_page=item.page_image_plan,
                story_page=item.story_page,
            ),
        }

    @staticmethod
    def _global_visual_context(visual_bible: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "style",
            "illustration_notes",
            "style_consistency_rules",
            "color_palette_global",
            "rendering_style",
            "safety_rules",
            "negative_constraints",
        )
        return {key: visual_bible[key] for key in keys if key in visual_bible}

    @staticmethod
    def _story_page_context(story_page: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(story_page, dict):
            return {}
        allowed_keys = ("page_number", "emotion")
        return {key: story_page[key] for key in allowed_keys if story_page.get(key) is not None}

    async def _apply_saved_urls_to_all_contents(
        self,
        generic_story: GenericStory,
        saved_urls: dict[str, str],
    ) -> None:
        cover_url = saved_urls.get("cover")
        back_cover_url = saved_urls.get("back_cover")
        page_image_urls = {
            int(item_id.removeprefix("page_")): image_url
            for item_id, image_url in saved_urls.items()
            if item_id.startswith("page_") and item_id.removeprefix("page_").isdigit()
        }

        if cover_url:
            generic_story.cover_image = cover_url

        for content in getattr(generic_story, "contents", []) or []:
            story_json = deepcopy(content.story_json)
            self._apply_saved_urls_to_story_json(
                story_json,
                cover_url=cover_url,
                page_image_urls=page_image_urls,
                back_cover_url=back_cover_url,
            )
            content.story_json = story_json
            await self.generic_stories.update_content(content)

        await self.generic_stories.flush()

    @classmethod
    def _apply_saved_urls_to_story_json(
        cls,
        story_json: dict[str, Any],
        *,
        cover_url: str | None,
        page_image_urls: dict[int, str],
        back_cover_url: str | None,
    ) -> None:
        if cover_url:
            story_json["cover_image_url"] = cover_url
        if back_cover_url:
            story_json["back_cover_image_url"] = back_cover_url

        pages = story_json.get("pages")
        if not isinstance(pages, list):
            return
        for fallback, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            page_number = cls._page_number(page, fallback=fallback)
            image_url = page_image_urls.get(page_number)
            if image_url:
                page["image_url"] = image_url

    @staticmethod
    def _story_title(
        generic_story: GenericStory,
        workflow: GenericStoryWorkflow,
        story_json: dict[str, Any],
    ) -> str:
        return (
            str(story_json.get("title") or "").strip()
            or str(getattr(generic_story, "title", "") or "").strip()
            or str(getattr(workflow, "title", "") or "").strip()
            or "Untitled Story"
        )
