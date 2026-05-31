from datetime import datetime, UTC
from pathlib import Path
import tempfile
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.core.logger import get_logger
from app.model.request.character import CharacterGenerationRequest
from app.model.response.character import CharacterGenerationResponse
from app.repository.child_repository import ChildRepository
from app.service.ai.factory import get_ai_provider
from app.service.image_storage_provider import get_image_storage_service
from app.utils.prompt_loader import load_and_render_prompt

logger = get_logger(__name__)


class CharacterService:
    """Business logic for character generation from child profiles."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.children = ChildRepository(session)

    async def generate_character(
        self,
        child_id: UUID,
        user_id: UUID,
        public_base_url: str,
        payload: CharacterGenerationRequest,
        ai_provider: str | None = None,
    ) -> CharacterGenerationResponse:
        """Generate AI character from child profile photo.

        Flow:
        1. Validate child profile exists and belongs to user
        2. Verify child has profile photo
        3. Load character generation prompt template
        4. Call AI provider to generate character image
        5. Save character image to storage
        6. Generate character description using vision model
        7. Update child profile with character data
        8. Return response

        Args:
            child_id: ID of child profile
            user_id: ID of parent user (for ownership validation)
            public_base_url: Base URL for constructing public file URLs
            payload: Character generation request with optional context
            ai_provider: AI provider to use ("openai" or "google") - uses default if not specified

        Returns:
            CharacterGenerationResponse with generated character data

        Raises:
            NotFoundException: If child profile not found or doesn't belong to user
            AppException: If child has no profile photo or AI service fails
        """
        # Get AI provider instance (use specified provider or default)
        provider_name = ai_provider or settings.AI_PROVIDER
        ai_service = get_ai_provider(provider_name)
        logger.info(f"Generating character using {provider_name} provider for child_id={child_id}, user_id={user_id}")

        # Validate child exists and belongs to user
        child = await self.children.get_for_user(user_id, child_id)
        if child is None:
            raise NotFoundException(
                "Child profile not found",
                code="CHILD_NOT_FOUND",
            )

        if not child.avatar_image_url:
            raise AppException(
                "Child profile photo is required for character generation",
                code="NO_PHOTO",
            )

        photo_path = await self._materialize_reference_photo(child.avatar_image_url)

        # Generate character image using AI provider
        try:
            logger.info(f"Calling {provider_name} AI provider to generate character image from {photo_path.name}")
            character_prompt = load_and_render_prompt(
                "prompts/character_generation.txt",
                {
                    "additional_context": payload.additional_context or "",
                    "child_age_label": self._child_age_label(child),
                    "child_age_visual_guidance": self._age_visual_guidance(child.age),
                },
            )
            logger.info(f"Character Generation Prompt:\n{character_prompt}")

            image_result = await ai_service.create_character_from_photo(
                reference_image_path=photo_path,
                prompt=character_prompt,
                size=settings.CHARACTER_IMAGE_SIZE,
                quality=settings.CHARACTER_IMAGE_QUALITY,
                child_age_label=self._child_age_label(child),
                child_age_visual_guidance=self._age_visual_guidance(child.age),
            )
        finally:
            try:
                photo_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove temporary reference photo: %s", photo_path)

        logger.info(f"Successfully generated character image using {image_result.model}")

        # Save generated character image
        logger.info("Saving character image to storage")
        character_url = await get_image_storage_service().save_character_image(
            parent_id=child.user_id,
            child_id=child.id,
            image_bytes=image_result.image_bytes,
            public_base_url=public_base_url,
        )
        logger.info(f"Character image saved to {character_url}")

        # Use analysis text from image generation as character description
        logger.info("Using analysis text as character description")
        analysis_text = image_result.metadata.get("analysis_text")

        if not analysis_text:
            # Fallback if analysis was skipped
            analysis_text = (
                f"A beautifully illustrated character representing a {child.age}-year-old child in a premium "
                "semi-realistic 3D storybook aesthetic."
            )
            logger.warning("No analysis text available, using fallback description")

        # Extract clean description from analysis text (remove numbered points if present)
        clean_description = self._extract_clean_description(analysis_text)

        # Build character metadata
        metadata = {
            "description": clean_description,
            "style": "premium semi-realistic 3D storybook",
            "generation_model": image_result.model,
            "prompt_used": image_result.prompt_used,
            "revised_prompt": image_result.revised_prompt,
            "analysis_text": image_result.metadata.get("analysis_text"),
            "enhanced_prompt": image_result.metadata.get("enhanced_prompt"),
            "child_age_label": self._child_age_label(child),
            "age_visual_guidance": self._age_visual_guidance(child.age),
            "size": image_result.metadata.get("size"),
            "quality": image_result.metadata.get("quality"),
            "generated_at": datetime.now(UTC).isoformat(),
            "generation_status": "completed",
        }

        # Update child profile with character data
        logger.info(f"Updating child profile {child_id} with character metadata")
        await self.children.update_character(
            child=child,
            character_image_url=character_url,
            character_metadata=metadata,
        )

        await self.session.commit()
        logger.info(f"Character generation completed for child_id={child_id}")

        return CharacterGenerationResponse(
            character_image_url=character_url,
            character_description=clean_description,
        )

    @staticmethod
    async def _materialize_reference_photo(url_or_path: str) -> Path:
        image_bytes = await get_image_storage_service().get_image_bytes(url_or_path)
        suffix = Path(url_or_path.split("?", 1)[0]).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".png"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            return Path(temp_file.name)

    @staticmethod
    def _extract_clean_description(analysis_text: str) -> str:
        """Extract a clean, concise description from LLM analysis text.

        Removes numbered points and bullet points, keeping only the first sentence or main description.
        """
        if not analysis_text:
            return "A beautifully illustrated character."

        # Remove markdown formatting and numbered lists
        lines = analysis_text.split('\n')
        clean_lines = []

        for line in lines:
            # Skip empty lines and numbered/bulleted points
            line = line.strip()
            if not line or line[0].isdigit() or line.startswith('*') or line.startswith('-'):
                continue
            # Remove markdown bold/italic
            line = line.replace('**', '').replace('__', '').replace('_', '')
            if line:
                clean_lines.append(line)

        # Join lines and take first 1-2 sentences max (up to 200 chars)
        description = ' '.join(clean_lines)
        if len(description) > 200:
            # Truncate at first period if available
            first_period = description.find('.')
            if first_period > 0 and first_period < 200:
                description = description[:first_period + 1]
            else:
                description = description[:200] + "..."

        return description or "A beautifully illustrated character."

    @staticmethod
    def _child_age_label(child) -> str:
        return f"{child.age} years old" if child.age is not None else "the child's profile age"

    @staticmethod
    def _age_visual_guidance(age: int | None) -> str:
        if age is None:
            return "age-appropriate child height, body build, hands, feet, limbs, and facial maturity"
        if age <= 4:
            return (
                "toddler/preschool-age proportions: short child height, soft round cheeks, small hands and feet, "
                "short child limbs, and very young child facial proportions"
            )
        if age <= 7:
            return (
                "early-reader child proportions: childlike height, rounded cheeks, natural child hands and feet, "
                "and young child facial proportions"
            )
        if age <= 12:
            return (
                "older child proportions: age-appropriate height, natural child build, childlike facial maturity, "
                "and no teenage features"
            )
        return "teen-appropriate but still child-safe proportions, matching the profile photo and not adult features"
