import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException
from app.entity.child_profile import ChildProfile
from app.entity.story import Story, StoryGenerationMode, AgeGroup, StoryStatus
from app.entity.story_step import StoryStep, StoryStepName, StepStatus
from app.model.request.story import StoryGenerationRequest
from app.model.response.story import StoryResponse, StoryPageResponse, StoryStepResponse
from app.repository.child_repository import ChildRepository
from app.repository.story_repository import StoryRepository
from app.repository.story_step_repository import StoryStepRepository
from app.repository.story_page_repository import StoryPageRepository
from app.service.ai.openai_provider import OpenAIProvider
from app.service.ai.factory import get_ai_provider
from app.service.image_storage_service import image_storage_service
from app.service.plan_validator import PlanValidator, PlanValidationError
from app.service.image_plan_validator import ImagePlanValidator, ImagePlanValidationError
from app.utils.prompt_loader import load_prompt, render_prompt, load_and_render_prompt

logger = logging.getLogger(__name__)

# Testing flags helper
class StoryGenerationFlags:
    """Helper for managing story generation test/feature flags."""

    def __init__(
        self,
        skip_image_generation: bool = False,
        skip_validation: bool = False,
    ):
        self.skip_image_generation = skip_image_generation
        self.skip_validation = skip_validation

    @classmethod
    def from_request(cls, payload: StoryGenerationRequest) -> "StoryGenerationFlags":
        return cls(
            skip_image_generation=payload.skip_image_generation,
            skip_validation=payload.skip_validation,
        )


class StoryService:
    """Orchestrates the story generation workflow."""

    MAX_RETRIES = 3

    def __init__(self, session: AsyncSession):
        self.session = session
        self.stories = StoryRepository(session)
        self.story_steps = StoryStepRepository(session)
        self.story_pages = StoryPageRepository(session)
        self.children = ChildRepository(session)
        self.ai_provider: OpenAIProvider = get_ai_provider()
        self.plan_validator = PlanValidator()
        self.image_plan_validator = ImagePlanValidator()

    async def generate_story_async(
        self,
        user_id: UUID,
        child_id: UUID,
        payload: StoryGenerationRequest,
        public_base_url: str,
    ) -> StoryResponse:
        """Create story record and return immediately.

        Background task will execute the workflow asynchronously.
        """
        # Validate child exists and belongs to user
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            raise NotFoundException("Child profile not found")

        # Require character image for visual consistency
        if not child.character_image_url:
            raise AppException(
                "Child must have a generated character image before story generation",
                code="NO_CHARACTER_IMAGE",
            )

        # Calculate age_group from child's date of birth
        age_group = self._get_age_group_from_dob(child.dob)
        logger.info(f"Calculated age_group={age_group} for child {child_id} with DOB={child.dob}")

        # Create story record with PENDING status
        story = await self.stories.create(
            user_id=user_id,
            child_id=child_id,
            generation_mode=payload.mode,
            age_group=age_group,
            category=payload.category,
            learning_goal=payload.learning_goal,
            context=payload.context,
            event_description=payload.event_description,
        )
        await self.session.commit()

        logger.info(f"Story {story.id} created with status=PENDING, ready for background execution")

        # Manually construct response to avoid lazy-loading pages relationship
        return StoryResponse(
            id=story.id,
            title=story.title,
            moral=story.moral,
            summary=story.summary,
            status=story.status.value,
            current_step=story.current_step,
            generation_mode=story.generation_mode.value,
            age_group=story.age_group.value,
            pages=[],  # No pages yet, story is PENDING
            created_at=story.created_at,
            updated_at=story.updated_at,
        )

    async def execute_workflow(
        self,
        story_id: UUID,
        flags: StoryGenerationFlags = None,
    ) -> Story:
        """Execute the 6-step story generation workflow.

        This method is called by a background task with a fresh database session.
        """
        logger.info(f"[WORKFLOW] Starting for story {story_id}")
        if flags is None:
            flags = StoryGenerationFlags()

        logger.info(f"[WORKFLOW] Fetching story {story_id} from database")
        story = await self.stories.get_by_id(story_id)
        if story is None:
            logger.error(f"[WORKFLOW] Story {story_id} not found")
            raise NotFoundException(f"Story {story_id} not found")

        logger.info(f"[WORKFLOW] Story found, starting execution")
        try:
            # Step 1: Story Plan Generation
            story.status = StoryStatus.IN_PROGRESS
            story.current_step = StoryStepName.STORY_PLAN_GENERATION.value
            await self.stories.update(story)
            logger.info(f"Story {story_id}: Starting step 1 - Story Plan Generation")

            story_plan = await self._step_generate_plan(story, flags)

            # Step 2: Story Plan Validation (with retries)
            story.current_step = StoryStepName.STORY_PLAN_VALIDATION.value
            await self.stories.update(story)
            logger.info(f"Story {story_id}: Starting step 2 - Story Plan Validation")

            story_plan = await self._step_validate_plan(story, story_plan, flags)

            # Step 3: Story Generation
            story.current_step = StoryStepName.STORY_GENERATION.value
            await self.stories.update(story)
            logger.info(f"Story {story_id}: Starting step 3 - Story Generation")

            story_json = await self._step_generate_story(story, story_plan, flags)

            # Step 4: Image Plan Generation
            story.current_step = StoryStepName.IMAGE_PLAN_GENERATION.value
            await self.stories.update(story)
            logger.info(f"Story {story_id}: Starting step 4 - Image Plan Generation")

            image_plan = await self._step_generate_image_plan(story, story_json, flags)

            # Step 5: Image Plan Validation (optional, can skip)
            if not flags.skip_validation:
                story.current_step = StoryStepName.IMAGE_PLAN_VALIDATION.value
                await self.stories.update(story)
                logger.info(f"Story {story_id}: Starting step 5 - Image Plan Validation")
                image_plan = await self._step_validate_image_plan(story, image_plan, story_json, flags)

            # Step 6: Image Generation
            if not flags.skip_image_generation:
                story.current_step = StoryStepName.IMAGE_GENERATION.value
                await self.stories.update(story)
                logger.info(f"Story {story_id}: Starting step 6 - Image Generation")
                await self._step_generate_images(story, story_json, image_plan, flags)
            else:
                logger.info(f"Story {story_id}: Skipping image generation (test mode)")
                await self._create_pages_without_images(story, story_json)

            # Mark story as completed
            story.status = StoryStatus.COMPLETED
            story.current_step = None
            story.story_plan_json = story_plan
            story.story_json = story_json
            story.image_plan_json = image_plan
            await self.stories.update(story)
            await self.session.commit()

            logger.info(f"Story {story_id}: Workflow completed successfully")
            return story

        except Exception as e:
            logger.exception(f"Story {story_id}: Workflow failed with error: {str(e)}")
            story.status = StoryStatus.FAILED
            story.error_message = str(e)
            story.current_step = None
            await self.stories.update(story)
            await self.session.commit()
            raise

    async def _step_generate_plan(self, story: Story, flags: StoryGenerationFlags) -> dict[str, Any]:
        """Step 1: Generate story plan using LLM."""
        child = await self.children.get_for_user(story.user_id, story.child_id)
        if child is None:
            raise NotFoundException("Child profile not found during plan generation")

        # Load prompt template
        template = load_prompt("prompts/story/story_plan_prompt.txt")

        # Prepare variables
        pages = self._get_page_count_for_age_group(story.age_group)
        theme = story.category or story.event_description or "adventure"

        # Generate better hobby suggestions based on age group
        hobby = self._get_hobby_for_age_group(story.age_group)

        # Extract detailed character info for consistent visual anchor
        character_info = self._extract_character_analysis(child)

        # Render template using safe string replacement to avoid format() issues with JSON
        prompt = template
        prompt = prompt.replace("{age_group}", story.age_group.value)
        prompt = prompt.replace("{first_name}", child.first_name or "Child")
        prompt = prompt.replace("{gender}", child.gender or "neutral")
        prompt = prompt.replace("{theme}", theme)
        prompt = prompt.replace("{hobby}", hobby)
        prompt = prompt.replace("{learning_goal}", story.learning_goal or "personal growth")
        prompt = prompt.replace("{moral}", "kindness and courage")
        prompt = prompt.replace("{pages}", str(pages))
        prompt = prompt.replace("{custom_character}", "false")
        prompt = prompt.replace("{character_description}", character_info)

        # Create step record
        step = await self.story_steps.create(story.id, StoryStepName.STORY_PLAN_GENERATION)
        step.prompt = prompt
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        try:
            # Call LLM
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=4000,
                temperature=0.4,
                response_format={"type": "json_object"},
            )

            # Parse response
            try:
                story_plan = json.loads(result.text)
            except json.JSONDecodeError as e:
                raise AppException(f"Invalid JSON from LLM: {str(e)}", code="INVALID_LLM_JSON")

            step.response = story_plan
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return story_plan

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _step_validate_plan(
        self, story: Story, plan: dict[str, Any], flags: StoryGenerationFlags
    ) -> dict[str, Any]:
        """Step 2: Validate story plan with retry logic."""
        if flags.skip_validation:
            logger.info(f"Story {story.id}: Skipping plan validation (test mode)")
            return plan

        step = await self.story_steps.create(story.id, StoryStepName.STORY_PLAN_VALIDATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        for attempt in range(1, self.MAX_RETRIES + 1):
            step.retry_count = attempt - 1

            # Validate
            result = self.plan_validator.validate(plan, age_group=story.age_group)

            if result.ok:
                step.status = StepStatus.COMPLETED
                step.completed_at = datetime.utcnow()
                step.response = {"valid": True}
                await self.story_steps.update(step)
                await self.session.commit()
                logger.info(f"Story {story.id}: Plan validation passed on attempt {attempt}")
                return plan

            # Validation failed
            error_list = "\n".join([f"  - {err}" for err in result.errors])
            logger.warning(f"Story {story.id}: Plan validation failed on attempt {attempt}:\n{error_list}")

            if attempt < self.MAX_RETRIES:
                # Regenerate with errors as feedback
                await self.session.commit()
                try:
                    plan = await self._retry_plan_generation(story, plan, result.errors, attempt)
                except Exception as e:
                    logger.error(f"Story {story.id}: Failed to regenerate plan on attempt {attempt}: {str(e)}")
                    step.status = StepStatus.FAILED
                    step.error_message = str(e)
                    step.completed_at = datetime.utcnow()
                    await self.story_steps.update(step)
                    await self.session.commit()
                    raise

        # All retries exhausted - perform final validation to get errors for logging
        final_result = self.plan_validator.validate(plan, age_group=story.age_group)
        error_details = "\n".join([f"  - {err}" for err in final_result.errors])
        error_msg = f"Plan validation failed after {self.MAX_RETRIES} attempts:\n{error_details}"

        step.status = StepStatus.FAILED
        step.error_message = error_msg
        step.completed_at = datetime.utcnow()
        await self.story_steps.update(step)
        await self.session.commit()

        logger.error(f"Story {story.id}: {error_msg}")
        raise AppException(
            f"Story plan validation failed after {self.MAX_RETRIES} retries",
            code="PLAN_VALIDATION_FAILED",
        )

    async def _retry_plan_generation(
        self, story: Story, previous_plan: dict[str, Any], errors: list[str], attempt: int
    ) -> dict[str, Any]:
        """Regenerate story plan with validation errors as feedback."""
        logger.info(f"Story {story.id}: Regenerating plan (attempt {attempt + 1}) with error feedback")

        child = await self.children.get_for_user(story.user_id, story.child_id)
        template = load_prompt("prompts/story/story_plan_prompt.txt")

        pages = self._get_page_count_for_age_group(story.age_group)
        theme = story.category or story.event_description or "adventure"
        hobby = self._get_hobby_for_age_group(story.age_group)
        character_info = self._extract_character_analysis(child)

        # Add error feedback to prompt using safe string replacement
        error_feedback = "\n".join([f"- {err}" for err in errors])
        enhanced_prompt = template
        enhanced_prompt = enhanced_prompt.replace("{age_group}", story.age_group.value)
        enhanced_prompt = enhanced_prompt.replace("{first_name}", child.first_name or "Child")
        enhanced_prompt = enhanced_prompt.replace("{gender}", child.gender or "neutral")
        enhanced_prompt = enhanced_prompt.replace("{theme}", theme)
        enhanced_prompt = enhanced_prompt.replace("{hobby}", hobby)
        enhanced_prompt = enhanced_prompt.replace("{learning_goal}", story.learning_goal or "personal growth")
        enhanced_prompt = enhanced_prompt.replace("{moral}", "kindness and courage")
        enhanced_prompt = enhanced_prompt.replace("{pages}", str(pages))
        enhanced_prompt = enhanced_prompt.replace("{custom_character}", "false")
        enhanced_prompt = enhanced_prompt.replace("{character_description}", character_info)
        enhanced_prompt += f"\n\nPREVIOUS VALIDATION ERRORS (fix these):\n{error_feedback}"

        result = await self.ai_provider.generate_text(
            enhanced_prompt,
            max_tokens=4000,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        try:
            new_plan = json.loads(result.text)
        except json.JSONDecodeError as e:
            raise AppException(f"Invalid JSON from regenerated plan: {str(e)}", code="INVALID_LLM_JSON")

        return new_plan

    async def _step_generate_story(
        self, story: Story, plan: dict[str, Any], flags: StoryGenerationFlags
    ) -> dict[str, Any]:
        """Step 3: Generate story text from validated plan."""
        step = await self.story_steps.create(story.id, StoryStepName.STORY_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        template = load_prompt("prompts/story/story_generation_prompt.txt")
        prompt = template.replace("{story_plan_json}", json.dumps(plan, indent=2))
        step.prompt = prompt

        try:
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=4000,
                temperature=0.7,
                response_format={"type": "json_object"},
            )

            try:
                story_json = json.loads(result.text)
            except json.JSONDecodeError as e:
                raise AppException(f"Invalid JSON from story generation: {str(e)}", code="INVALID_LLM_JSON")

            # Extract metadata from plan
            story_json["title"] = plan.get("title", "Untitled")
            story_json["moral"] = plan.get("moral_theme", "")
            story_json["summary"] = plan.get("summary", "")

            step.response = story_json
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return story_json

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _step_generate_image_plan(
        self, story: Story, story_json: dict[str, Any], flags: StoryGenerationFlags
    ) -> dict[str, Any]:
        """Step 4: Generate image plan from story."""
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_PLAN_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        template = load_prompt("prompts/story/image_plan_prompt.txt")

        child = await self.children.get_for_user(story.user_id, story.child_id)
        character_description = child.character_metadata.get("description", "") if child.character_metadata else ""

        prompt = template.replace("{story_json}", json.dumps(story_json, indent=2))
        prompt = prompt.replace("{character_description}", character_description)
        step.prompt = prompt

        try:
            result = await self.ai_provider.generate_text(
                prompt,
                max_tokens=4000,
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            try:
                image_plan = json.loads(result.text)
            except json.JSONDecodeError as e:
                raise AppException(f"Invalid JSON from image plan generation: {str(e)}", code="INVALID_LLM_JSON")

            step.response = image_plan
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            return image_plan

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _step_validate_image_plan(
        self,
        story: Story,
        image_plan: dict[str, Any],
        story_json: dict[str, Any],
        flags: StoryGenerationFlags,
    ) -> dict[str, Any]:
        """Step 5: Validate image plan (optional)."""
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_PLAN_VALIDATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        result = self.image_plan_validator.validate(image_plan, story_json=story_json)

        if result.ok:
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            step.response = {"valid": True}
            await self.story_steps.update(step)
            await self.session.commit()
            logger.info(f"Story {story.id}: Image plan validation passed")
            return image_plan

        # Validation failed
        logger.warning(f"Story {story.id}: Image plan validation failed: {result.errors}")
        step.status = StepStatus.FAILED
        step.error_message = "; ".join(result.errors)
        step.completed_at = datetime.utcnow()
        await self.story_steps.update(step)
        await self.session.commit()
        raise AppException(
            f"Image plan validation failed: {'; '.join(result.errors)}",
            code="IMAGE_PLAN_VALIDATION_FAILED",
        )

    async def _step_generate_images(
        self,
        story: Story,
        story_json: dict[str, Any],
        image_plan: dict[str, Any],
        flags: StoryGenerationFlags,
    ) -> None:
        """Step 6: Generate images using DALL-E."""
        step = await self.story_steps.create(story.id, StoryStepName.IMAGE_GENERATION)
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow()

        # Store image plan as prompt for audit trail
        step.prompt = json.dumps(image_plan, indent=2)

        try:
            pages = story_json.get("pages", [])
            cover = image_plan.get("cover", {})
            back_cover = image_plan.get("back_cover", {})
            image_pages = image_plan.get("pages", [])

            # Generate cover image
            if cover and cover.get("image_prompt"):
                logger.info(f"Story {story.id}: Generating cover image")
                cover_bytes = await self.ai_provider.generate_image(
                    cover.get("image_prompt"),
                    size="1024x1024",
                    quality="standard",
                )
                cover_url = await image_storage_service.save_story_image(
                    story.id,
                    cover_bytes.image_bytes,
                    "cover.png",
                    "",  # base_url will be added by storage service
                )
                await self.story_pages.create_page(
                    story.id,
                    page_number=0,
                    page_type="cover",
                    text="",
                    image_prompt=cover.get("image_prompt"),
                    image_url=cover_url,
                )

            # Generate page images
            for img_page in image_pages:
                page_num = img_page.get("page_number", 0)
                if img_page.get("image_prompt") and page_num > 0:
                    logger.info(f"Story {story.id}: Generating image for page {page_num}")
                    image_bytes = await self.ai_provider.generate_image(
                        img_page.get("image_prompt"),
                        size="1024x1024",
                        quality="standard",
                    )
                    image_url = await image_storage_service.save_story_image(
                        story.id,
                        image_bytes.image_bytes,
                        f"page_{page_num}.png",
                        "",
                    )

                    # Find corresponding story page
                    if page_num <= len(pages):
                        story_page = pages[page_num - 1]
                        await self.story_pages.create_page(
                            story.id,
                            page_number=page_num,
                            page_type="page",
                            text=story_page.get("text", ""),
                            image_prompt=img_page.get("image_prompt"),
                            image_url=image_url,
                        )

            # Generate back cover image
            if back_cover and back_cover.get("image_prompt"):
                logger.info(f"Story {story.id}: Generating back cover image")
                back_cover_bytes = await self.ai_provider.generate_image(
                    back_cover.get("image_prompt"),
                    size="1024x1024",
                    quality="standard",
                )
                back_cover_url = await image_storage_service.save_story_image(
                    story.id,
                    back_cover_bytes.image_bytes,
                    "back_cover.png",
                    "",
                )
                await self.story_pages.create_page(
                    story.id,
                    page_number=len(pages) + 1,
                    page_type="back_cover",
                    text="",
                    image_prompt=back_cover.get("image_prompt"),
                    image_url=back_cover_url,
                )

            # Store image generation results in response for audit trail
            step.response = {
                "images_generated": True,
                "message": "All images generated and saved successfully",
                "image_count": len(image_pages) + (2 if cover and back_cover else (1 if cover or back_cover else 0))
            }
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()

            logger.info(f"Story {story.id}: All images generated successfully")

        except Exception as e:
            step.error_message = str(e)
            step.status = StepStatus.FAILED
            step.completed_at = datetime.utcnow()
            await self.story_steps.update(step)
            await self.session.commit()
            raise

    async def _create_pages_without_images(self, story: Story, story_json: dict[str, Any]) -> None:
        """Create story pages without images (for testing)."""
        pages = story_json.get("pages", [])
        for idx, page in enumerate(pages):
            await self.story_pages.create_page(
                story.id,
                page_number=idx + 1,
                page_type="page",
                text=page.get("text", ""),
                image_prompt=None,
                image_url=None,
            )

    @staticmethod
    @staticmethod
    def _get_age_group_from_dob(dob) -> str:
        """Calculate age group from date of birth."""
        if not dob:
            return "5-7"  # Default to early reader if no DOB

        from datetime import date
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        if age < 5:
            return "2-4"
        elif age < 8:
            return "5-7"
        else:
            return "8-12"

    @staticmethod
    def _get_page_count_for_age_group(age_group: str) -> int:
        """Get recommended page count for age group."""
        age_counts = {
            "2-4": 6,
            "5-7": 10,
            "8-12": 12,
        }
        return age_counts.get(age_group, 10)

    @staticmethod
    def _get_hobby_for_age_group(age_group: str) -> str:
        """Get age-appropriate hobby/interest suggestions."""
        hobbies = {
            "2-4": "playing with toys, exploring, drawing, singing",
            "5-7": "reading, drawing, building with blocks, playing games, riding bikes",
            "8-12": "reading, creating art, sports, music, science experiments, video games",
        }
        return hobbies.get(age_group, "creative hobbies and exploration")

    @staticmethod
    def _extract_character_analysis(child) -> str:
        """Extract detailed character analysis from child profile for visual consistency."""
        age_str = f"{child.age} years old" if child.age else "child"

        if not child.character_metadata:
            return f"A friendly {age_str} child named {child.first_name} ready for adventure."

        metadata = child.character_metadata

        # Priority: Use analysis_text (detailed visual analysis) over simple description
        analysis_text = metadata.get("analysis_text", "")
        description = metadata.get("description", "")

        # Build comprehensive character profile for image anchor consistency
        parts = []

        # Add age and name as header
        parts.append(f"Age: {age_str}")
        parts.append(f"Name: {child.first_name}")

        # Use analysis_text if available (detailed visual analysis from character generation)
        if analysis_text:
            parts.append(f"Visual Analysis:\n{analysis_text}")
        # Fall back to simple description if analysis_text not available
        elif description:
            parts.append(f"Description: {description}")

        # Add visual generation style if available
        if metadata.get("generation_model"):
            parts.append(f"Generated in: {metadata.get('generation_model')} style")

        # Add specific visual anchors for consistency
        parts.append(
            "\nVISUAL ANCHOR (keep consistent in EVERY page illustration):\n"
            "- Same outfit, hair color, and features throughout the story\n"
            "- This character should look identical on every page unless explicitly changing clothes/appearance in the story\n"
            "- Use this as the visual reference for the hero character"
        )

        return "\n".join(parts) if parts else f"A friendly {age_str} child named {child.first_name}."

    async def get_story(self, user_id: UUID, story_id: UUID) -> StoryResponse:
        """Retrieve story with ownership validation."""
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found")

        # Manually construct response to avoid lazy-loading pages relationship
        pages = [
            StoryPageResponse(
                id=page.id,
                page_number=page.page_number,
                page_type=page.page_type,
                text=page.text,
                image_prompt=page.image_prompt,
                image_url=page.image_url,
            )
            for page in story.pages
        ]

        return StoryResponse(
            id=story.id,
            title=story.title,
            moral=story.moral,
            summary=story.summary,
            status=story.status.value,
            current_step=story.current_step,
            generation_mode=story.generation_mode.value,
            age_group=story.age_group.value,
            pages=pages,
            created_at=story.created_at,
            updated_at=story.updated_at,
        )

    async def list_stories(self, user_id: UUID, child_id: UUID | None = None) -> list[StoryResponse]:
        """List user's stories, optionally filtered by child."""
        stories = await self.stories.list_by_user(user_id, child_id)
        results = []
        for story in stories:
            pages = [
                StoryPageResponse(
                    id=page.id,
                    page_number=page.page_number,
                    page_type=page.page_type,
                    text=page.text,
                    image_prompt=page.image_prompt,
                    image_url=page.image_url,
                )
                for page in story.pages
            ]
            results.append(
                StoryResponse(
                    id=story.id,
                    title=story.title,
                    moral=story.moral,
                    summary=story.summary,
                    status=story.status.value,
                    current_step=story.current_step,
                    generation_mode=story.generation_mode.value,
                    age_group=story.age_group.value,
                    pages=pages,
                    created_at=story.created_at,
                    updated_at=story.updated_at,
                )
            )
        return results

    async def get_story_steps(self, user_id: UUID, story_id: UUID) -> list[StoryStepResponse]:
        """Retrieve audit trail for story."""
        # Verify ownership
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found")

        steps = await self.story_steps.list_by_story(story_id)
        return [StoryStepResponse.model_validate(s) for s in steps]
