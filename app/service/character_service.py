from datetime import datetime, UTC
import json
from pathlib import Path
import tempfile
from typing import Any
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


CHARACTER_DESCRIPTION_FIELDS = {
    "age_appearance",
    "face_shape",
    "cheek_shape",
    "jawline_shape",
    "chin_shape",
    "skin_tone",
    "hair_color",
    "hair_length",
    "hair_texture",
    "hair_style",
    "hair_direction",
    "eye_color",
    "eye_shape",
    "eye_size",
    "eyebrow_shape",
    "eyebrow_thickness",
    "nose_shape",
    "mouth_shape",
    "smile_characteristics",
    "ear_visibility",
    "distinctive_features",
    "identity_summary",
}


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
        3. Analyze original photo into a permanent identity profile
        4. Generate the master storybook character from the original photo plus identity profile
        5. Save character image to storage
        6. Update child profile with character data
        7. Return response

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

        # Analyze original profile photo first. This permanent profile is the
        # identity lock used for character and story image generation.
        try:
            photo_bytes = photo_path.read_bytes()
            photo_mime_type = self._detect_image_mime_type(photo_bytes)
            logger.info("Generating permanent structured identity profile from original child photo")
            identity_prompt = load_and_render_prompt("prompts/character_description.txt", {})
            logger.info(f"Character Identity Prompt:\n{identity_prompt}")
            identity_result = await ai_service.describe_character_image(
                image_bytes=photo_bytes,
                prompt=identity_prompt,
                mime_type=photo_mime_type,
                response_format={"type": "json_object"},
                max_tokens=2000,
                temperature=0.1,
            )
            character_identity_profile = self._parse_character_description_json(identity_result.text)
            clean_description = self._summarize_character_identity_profile(character_identity_profile)

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
                identity_profile=character_identity_profile,
                identity_profile_json=json.dumps(character_identity_profile, ensure_ascii=False),
                identity_profile_text=clean_description,
            )
        finally:
            try:
                photo_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove temporary reference photo: %s", photo_path)

        logger.info(f"Successfully generated character image using {image_result.model}")
        logger.info("Structured identity profile generated from original photo and stored as source of truth")

        # Save generated character image
        logger.info("Saving character image to storage")
        character_url = await get_image_storage_service().save_character_image(
            parent_id=child.user_id,
            child_id=child.id,
            image_bytes=image_result.image_bytes,
            public_base_url=public_base_url,
        )
        logger.info(f"Character image saved to {character_url}")

        image_metadata = image_result.metadata or {}

        # Build character metadata
        metadata = {
            "description": clean_description,
            "identity_summary": clean_description,
            "identity_profile": character_identity_profile,
            "identity_profile_source": "original_child_photo",
            "style": "premium semi-realistic 3D storybook",
            "generation_model": image_result.model,
            "identity_model": identity_result.model,
            "identity_raw_text": identity_result.text,
            "identity_usage": (identity_result.metadata or {}).get("usage"),
            "child_age_label": self._child_age_label(child),
            "age_visual_guidance": self._age_visual_guidance(child.age),
            "size": image_metadata.get("size"),
            "quality": image_metadata.get("quality"),
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
    def _detect_image_mime_type(image_bytes: bytes) -> str:
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"

    @staticmethod
    def _parse_character_description_json(raw_text: str) -> dict[str, Any]:
        text = (raw_text or "").strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppException(
                "Character description response was not valid JSON",
                code="CHARACTER_DESCRIPTION_INVALID_JSON",
            ) from exc

        if not isinstance(payload, dict):
            raise AppException(
                "Character description response must be a JSON object",
                code="CHARACTER_DESCRIPTION_INVALID_JSON",
            )

        if not payload.get("mouth_shape") and isinstance(payload.get("mouth_description"), str):
            payload["mouth_shape"] = payload["mouth_description"]
        if not payload.get("smile_characteristics") and isinstance(payload.get("smile_type"), str):
            payload["smile_characteristics"] = payload["smile_type"]
        if not payload.get("smile_characteristics") and isinstance(payload.get("mouth_description"), str):
            payload["smile_characteristics"] = payload["mouth_description"]
        if not payload.get("hair_direction"):
            payload["hair_direction"] = "not clearly visible"
        if not payload.get("age_appearance"):
            payload["age_appearance"] = "not clearly visible"
        for optional_text_field in (
            "cheek_shape",
            "jawline_shape",
            "chin_shape",
            "hair_texture",
            "eye_size",
            "eyebrow_thickness",
            "ear_visibility",
            "identity_summary",
        ):
            if not payload.get(optional_text_field):
                payload[optional_text_field] = "not clearly visible"

        normalized: dict[str, Any] = {}
        for field in CHARACTER_DESCRIPTION_FIELDS:
            value = payload.get(field)
            if field == "distinctive_features":
                if value is None:
                    normalized[field] = []
                elif isinstance(value, list):
                    normalized[field] = [item.strip() for item in value if isinstance(item, str) and item.strip()]
                else:
                    raise AppException(
                        "Character description distinctive_features must be an array",
                        code="CHARACTER_DESCRIPTION_SCHEMA_INVALID",
                    )
                continue

            if not isinstance(value, str) or not value.strip():
                raise AppException(
                    f"Character description field '{field}' is required",
                    code="CHARACTER_DESCRIPTION_SCHEMA_INVALID",
                )
            normalized[field] = value.strip()

        if not normalized.get("identity_summary") or normalized["identity_summary"] == "not clearly visible":
            normalized["identity_summary"] = CharacterService._build_identity_summary(normalized)

        # Backward-compatible derived fields for older story prompt helpers.
        normalized["mouth_description"] = (
            f"{normalized['mouth_shape']} mouth with {normalized['smile_characteristics']}"
        )
        normalized["smile_type"] = normalized["smile_characteristics"]

        return normalized

    @staticmethod
    def _summarize_character_identity_profile(profile: dict[str, Any]) -> str:
        identity_summary = profile.get("identity_summary")
        if isinstance(identity_summary, str) and identity_summary.strip():
            return identity_summary.strip()

        return CharacterService._build_identity_summary(profile)

    @staticmethod
    def _build_identity_summary(profile: dict[str, Any]) -> str:
        parts = [
            f"{profile['face_shape']} face",
            f"{profile['cheek_shape']} cheeks",
            f"{profile['jawline_shape']} jawline",
            f"{profile['chin_shape']} chin",
            f"{profile['skin_tone']} skin tone",
            f"{profile['eye_color']} {profile['eye_shape']} {profile['eye_size']} eyes",
            f"{profile['eyebrow_shape']} {profile['eyebrow_thickness']} eyebrows",
            f"{profile['nose_shape']} nose",
            f"{profile['mouth_shape']} mouth with {profile['smile_characteristics']}",
            (
                f"{profile['hair_color']} {profile['hair_length']} {profile['hair_texture']} "
                f"{profile['hair_style']} hair"
                f" with {profile['hair_direction']}"
            ),
            f"{profile['ear_visibility']} ears",
            f"{profile['age_appearance']} age appearance",
        ]
        features = profile.get("distinctive_features") or []
        if features:
            parts.append("distinctive features: " + ", ".join(features[:3]))

        return "A child character with " + "; ".join(parts) + "."

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
