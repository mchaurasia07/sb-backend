from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
from typing import Any
from uuid import UUID

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.entity.custom_story_workflow import (
    CustomStoryBatchJob,
    CustomStoryWorkflow,
    CustomStoryWorkflowStepRecord,
    CustomStoryWorkflowType,
)
from app.entity.generic_story import GenericStoryContent
from app.entity.story_batch_job import StoryBatchJobType
from app.repository.generic_story_repository import GenericStoryRepository
from app.service.image_storage_provider import get_image_storage_service


class GenericStoryConsistencyDiagnostics:
    """Read-only diagnostics for unified generic story visual consistency."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.generic_stories = GenericStoryRepository(session)

    async def diagnose(
        self,
        generic_story_id: UUID,
        *,
        language: str = "en",
        include_contact_sheet: bool = False,
    ) -> dict[str, Any]:
        normalized_language = language.strip().lower() or "en"
        story = await self.generic_stories.get_by_id(generic_story_id)
        if story is None:
            raise NotFoundException("Generic story not found", "GENERIC_STORY_NOT_FOUND")

        content = await self.generic_stories.get_content_by_story_and_language(
            generic_story_id=generic_story_id,
            language=normalized_language,
        )
        if content is None:
            raise NotFoundException("Generic story content not found", "GENERIC_STORY_CONTENT_NOT_FOUND")

        workflow = await self._latest_generic_workflow(generic_story_id)
        if workflow is None:
            raise NotFoundException("Generic story workflow not found", "GENERIC_STORY_WORKFLOW_NOT_FOUND")

        steps = await self._workflow_steps(workflow.id)
        image_job = await self._latest_image_job(workflow.id)
        story_json = content.story_json if isinstance(content.story_json, dict) else {}
        workflow_story_json = workflow.story_json if isinstance(workflow.story_json, dict) else {}
        image_plan = workflow.image_plan_json if isinstance(workflow.image_plan_json, dict) else {}
        visual_bible = image_plan.get("visual_bible") if isinstance(image_plan.get("visual_bible"), dict) else {}
        image_urls = self._content_image_urls(story_json)

        report = {
            "generic_story": {
                "id": str(story.id),
                "title": story.title,
                "status": story.status,
                "cover_image": story.cover_image,
                "total_pages": story.total_pages,
            },
            "language": normalized_language,
            "content": self._content_summary(story_json),
            "workflow": {
                "id": str(workflow.id),
                "status": self._value(workflow.status),
                "current_step": workflow.current_step,
                "ai_provider": workflow.ai_provider,
                "image_model": workflow.image_model,
                "reference_image_model": workflow.reference_image_model,
                "created_at": str(workflow.created_at),
                "updated_at": str(workflow.updated_at),
            },
            "visual_bible": self._visual_bible_summary(visual_bible),
            "image_plan": self._image_plan_summary(image_plan),
            "workflow_story_images": self._content_summary(workflow_story_json),
            "image_batch": self._image_batch_summary(image_job),
            "steps": [self._step_summary(step) for step in steps],
            "warnings": self._warnings(visual_bible, image_plan, image_job, story_json, workflow_story_json),
        }
        if include_contact_sheet:
            report["image_contact_sheet"] = await self._build_contact_sheet(generic_story_id, image_urls)
        return report

    async def _latest_generic_workflow(self, generic_story_id: UUID) -> CustomStoryWorkflow | None:
        result = await self.session.execute(
            select(CustomStoryWorkflow)
            .where(
                CustomStoryWorkflow.generic_story_id == generic_story_id,
                CustomStoryWorkflow.story_type == CustomStoryWorkflowType.GENERIC,
            )
            .order_by(CustomStoryWorkflow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _workflow_steps(self, workflow_id: UUID) -> list[CustomStoryWorkflowStepRecord]:
        result = await self.session.execute(
            select(CustomStoryWorkflowStepRecord)
            .where(CustomStoryWorkflowStepRecord.workflow_id == workflow_id)
            .order_by(CustomStoryWorkflowStepRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def _latest_image_job(self, workflow_id: UUID) -> CustomStoryBatchJob | None:
        result = await self.session.execute(
            select(CustomStoryBatchJob)
            .where(
                CustomStoryBatchJob.workflow_id == workflow_id,
                CustomStoryBatchJob.job_type == StoryBatchJobType.IMAGE,
            )
            .order_by(CustomStoryBatchJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _content_summary(story_json: dict[str, Any]) -> dict[str, Any]:
        pages = story_json.get("pages") if isinstance(story_json.get("pages"), list) else []
        return {
            "title": story_json.get("title"),
            "cover_image_url": story_json.get("cover_image_url"),
            "back_cover_image_url": story_json.get("back_cover_image_url"),
            "pages": [
                {
                    "page_number": page.get("page_number"),
                    "image_url": page.get("image_url"),
                    "text_head": str(page.get("text") or "")[:240],
                    "image_prompt_head": str(page.get("image_prompt") or "")[:360],
                    "planned_image_prompt_head": str(page.get("planned_image_prompt") or "")[:360],
                }
                for page in pages
                if isinstance(page, dict)
            ],
        }

    @staticmethod
    def _content_image_urls(story_json: dict[str, Any]) -> list[tuple[str, str]]:
        urls: list[tuple[str, str]] = []
        if story_json.get("cover_image_url"):
            urls.append(("cover", str(story_json["cover_image_url"])))
        for page in story_json.get("pages") or []:
            if isinstance(page, dict) and page.get("image_url"):
                urls.append((f"page_{page.get('page_number')}", str(page["image_url"])))
        if story_json.get("back_cover_image_url"):
            urls.append(("back_cover", str(story_json["back_cover_image_url"])))
        return urls

    @staticmethod
    def _visual_bible_summary(visual_bible: dict[str, Any]) -> dict[str, Any]:
        characters = GenericStoryConsistencyDiagnostics._visual_bible_characters(visual_bible)
        return {
            "characters": [
                {
                    "character_id": character.get("character_id"),
                    "name": character.get("name"),
                    "role": character.get("role"),
                    "appearance": character.get("appearance"),
                    "outfit": character.get("outfit"),
                    "hair_lock": character.get("hair_lock"),
                    "body_scale_lock": character.get("body_scale_lock"),
                    "relative_size": character.get("relative_size"),
                    "reference_image_url": character.get("reference_image_url"),
                    "persistent_reference_image_url": character.get("persistent_reference_image_url"),
                }
                for character in characters
            ],
            "character_reference_manifest": visual_bible.get("character_reference_manifest"),
        }

    @staticmethod
    def _image_plan_summary(image_plan: dict[str, Any]) -> dict[str, Any]:
        pages = image_plan.get("pages") if isinstance(image_plan.get("pages"), list) else []
        return {
            "character_reference_manifest": image_plan.get("character_reference_manifest"),
            "cover": GenericStoryConsistencyDiagnostics._image_plan_node_summary(image_plan.get("cover")),
            "pages": [GenericStoryConsistencyDiagnostics._image_plan_node_summary(page) for page in pages],
            "back_cover": GenericStoryConsistencyDiagnostics._image_plan_node_summary(image_plan.get("back_cover")),
        }

    @staticmethod
    def _image_plan_node_summary(node: Any) -> dict[str, Any]:
        node = node if isinstance(node, dict) else {}
        return {
            "page_number": node.get("page_number"),
            "characters_present": node.get("characters_present"),
            "reference_character_ids": node.get("reference_character_ids"),
            "important_objects": node.get("important_objects"),
            "object_states": node.get("object_states"),
            "scene_action": node.get("scene_action"),
            "image_prompt_head": str(node.get("image_prompt") or "")[:500],
        }

    @staticmethod
    def _image_batch_summary(job: CustomStoryBatchJob | None) -> dict[str, Any] | None:
        if job is None:
            return None
        payload = job.request_payload if isinstance(job.request_payload, dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        return {
            "id": str(job.id),
            "status": GenericStoryConsistencyDiagnostics._value(job.status),
            "provider_model": job.provider_model,
            "request_keys": job.request_keys,
            "reference_character_ids_by_item": payload.get("reference_character_ids_by_item"),
            "items": [
                {
                    "key": item.get("key"),
                    "reference_character_ids_used": item.get("reference_character_ids_used"),
                    "reference_image_urls_used": item.get("reference_image_urls_used"),
                    "source_image_prompt_head": str(item.get("source_image_prompt") or "")[:360],
                    "rendered_prompt_head": str(item.get("rendered_prompt") or "")[:500],
                }
                for item in items
                if isinstance(item, dict)
            ],
        }

    @staticmethod
    def _step_summary(step: CustomStoryWorkflowStepRecord) -> dict[str, Any]:
        output = step.output_json if isinstance(step.output_json, dict) else {}
        return {
            "step_name": GenericStoryConsistencyDiagnostics._value(step.step_name),
            "status": GenericStoryConsistencyDiagnostics._value(step.status),
            "has_prompt": bool(step.prompt),
            "prompt_head": str(step.prompt or "")[:500],
            "output_keys": sorted(output.keys()),
            "error_message": step.error_message,
            "created_at": str(step.created_at),
            "completed_at": str(step.completed_at),
        }

    @staticmethod
    def _warnings(
        visual_bible: dict[str, Any],
        image_plan: dict[str, Any],
        image_job: CustomStoryBatchJob | None,
        content_story_json: dict[str, Any],
        workflow_story_json: dict[str, Any],
    ) -> list[str]:
        warnings: list[str] = []
        manifest = image_plan.get("character_reference_manifest")
        manifest_items = [item for item in manifest if isinstance(item, dict)] if isinstance(manifest, list) else []
        manifest_ids = {str(item.get("character_id") or "") for item in manifest_items if item.get("reference_image_url")}
        for character in GenericStoryConsistencyDiagnostics._visual_bible_characters(visual_bible):
            name = str(character.get("name") or character.get("character_id") or "unknown character")
            character_id = str(character.get("character_id") or "")
            if character_id and character_id not in manifest_ids:
                warnings.append(f"Missing reference image for visible character: {name} ({character_id})")
            if not str(character.get("body_scale_lock") or character.get("relative_size") or "").strip():
                warnings.append(f"Missing body-scale lock for character: {name}")

        for label, node in GenericStoryConsistencyDiagnostics._image_plan_nodes(image_plan):
            important_objects = node.get("important_objects") if isinstance(node, dict) else None
            if isinstance(important_objects, list) and important_objects and not isinstance(node.get("object_states"), dict):
                warnings.append(f"{label} has important_objects without object_states")

        batch = GenericStoryConsistencyDiagnostics._image_batch_summary(image_job) if image_job is not None else None
        if batch:
            for item in batch.get("items") or []:
                key = item.get("key")
                used = item.get("reference_character_ids_used") or []
                planned = (batch.get("reference_character_ids_by_item") or {}).get(key)
                if isinstance(planned, list):
                    missing = [value for value in planned if value not in used]
                    if missing:
                        warnings.append(f"{key} planned references not attached: {', '.join(missing)}")

        workflow_urls = {
            page.get("page_number"): page.get("image_url")
            for page in workflow_story_json.get("pages", [])
            if isinstance(page, dict)
        }
        for page in content_story_json.get("pages", []) or []:
            if (
                isinstance(page, dict)
                and page.get("page_number") in workflow_urls
                and workflow_urls.get(page.get("page_number")) != page.get("image_url")
            ):
                warnings.append(f"Content page {page.get('page_number')} image URL differs from workflow story_json")
        return warnings

    @staticmethod
    def _image_plan_nodes(image_plan: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        nodes: list[tuple[str, dict[str, Any]]] = []
        for label in ("cover", "back_cover"):
            node = image_plan.get(label)
            if isinstance(node, dict):
                nodes.append((label, node))
        for page in image_plan.get("pages") or []:
            if isinstance(page, dict):
                nodes.append((f"page {page.get('page_number')}", page))
        return nodes

    @staticmethod
    def _visual_bible_characters(visual_bible: dict[str, Any]) -> list[dict[str, Any]]:
        characters: list[dict[str, Any]] = []
        hero = visual_bible.get("hero") if isinstance(visual_bible.get("hero"), dict) else None
        if hero is not None:
            characters.append(hero)
        recurring = visual_bible.get("recurring_characters")
        if isinstance(recurring, list):
            characters.extend(character for character in recurring if isinstance(character, dict))
        return characters

    async def _build_contact_sheet(self, generic_story_id: UUID, image_urls: list[tuple[str, str]]) -> dict[str, Any]:
        storage = get_image_storage_service()
        output_dir = Path(tempfile.gettempdir()) / f"generic_story_{generic_story_id}_diagnostics"
        output_dir.mkdir(parents=True, exist_ok=True)
        thumbs: list[Image.Image] = []
        images: list[dict[str, Any]] = []
        for label, url in image_urls:
            try:
                image_bytes = await storage.get_image_bytes(url)
                image = Image.open(BytesIO(image_bytes)).convert("RGB")
                path = output_dir / f"{label}.webp"
                path.write_bytes(image_bytes)
                images.append({"label": label, "url": url, "path": str(path), "size": list(image.size)})
                thumb = image.copy()
                thumb.thumbnail((260, 340))
                canvas = Image.new("RGB", (280, 380), "white")
                canvas.paste(thumb, ((280 - thumb.width) // 2, 30))
                draw = ImageDraw.Draw(canvas)
                draw.text((10, 8), label, fill="black")
                draw.text((10, 360), f"{image.size[0]}x{image.size[1]}", fill="black")
                thumbs.append(canvas)
            except Exception as exc:
                images.append({"label": label, "url": url, "error": str(exc)})

        sheet_path = None
        if thumbs:
            columns = 5
            rows = (len(thumbs) + columns - 1) // columns
            sheet = Image.new("RGB", (columns * 280, rows * 380), "white")
            for index, thumb in enumerate(thumbs):
                sheet.paste(thumb, ((index % columns) * 280, (index // columns) * 380))
            sheet_path = output_dir / "contact_sheet.jpg"
            sheet.save(sheet_path, quality=90)
        return {"contact_sheet_path": str(sheet_path) if sheet_path else None, "images": images}

    @staticmethod
    def _value(value: Any) -> str | None:
        if value is None:
            return None
        return value.value if hasattr(value, "value") else str(value)
