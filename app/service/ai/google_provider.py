import logging
import mimetypes
import json
import asyncio
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from app.core.age_groups import AGE_GROUP_0_3, AGE_GROUP_3_6, AGE_GROUP_6_9
from app.core.config import settings
from app.core.exceptions import AppException
from app.service.ai.base import (
    AIProvider,
    GeneratedImagePart,
    ImageGenerationResult,
    MultiImageGenerationResult,
    TextGenerationResult,
    parse_base64_image_data,
)
from app.service.mock_llm_responses import (
    get_mock_story_plan_text,
    get_mock_story_text,
    get_mock_image_plan_text,
)

logger = logging.getLogger(__name__)

DEFAULT_IMAGEN_MODEL = "imagen-4.0-generate-001"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"

IMAGE_MODEL_ALIASES = {
    "imagen-4": DEFAULT_IMAGEN_MODEL,
    "imagen-4-standard": DEFAULT_IMAGEN_MODEL,
    "imagen-4-fast": "imagen-4.0-fast-generate-001",
    "imagen-4-ultra": "imagen-4.0-ultra-generate-001",
    "imagen-3": "imagen-3.0-generate-002",
    "gemini-image": DEFAULT_GEMINI_IMAGE_MODEL,
    "gemini-flash-image": DEFAULT_GEMINI_IMAGE_MODEL,
}

SUPPORTED_REFERENCE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _normalize_model_name(model: str | None, default: str) -> str:
    normalized = (model or default).strip()
    if normalized.startswith("models/"):
        normalized = normalized.removeprefix("models/")
    return IMAGE_MODEL_ALIASES.get(normalized, normalized)


def _is_imagen_model(model: str) -> bool:
    return model.startswith("imagen-")


def _is_gemini_image_model(model: str) -> bool:
    return model.startswith("gemini-") and "image" in model


def _mime_type_for_path(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in SUPPORTED_REFERENCE_MIME_TYPES:
        raise AppException(
            "Reference image must be a JPEG, PNG, or WEBP file",
            code="UNSUPPORTED_IMAGE_TYPE",
        )
    return mime_type


class GoogleProvider(AIProvider):
    """Google Gemini implementation of the AI provider interface using the unified SDK."""

    def __init__(self, api_key: str, image_model: str, text_model: str, reference_image_model: str | None = None):
        if not api_key:
            raise ValueError("Google Gemini API key is required")

        self.image_model = _normalize_model_name(image_model, DEFAULT_IMAGEN_MODEL)
        self.reference_image_model = _normalize_model_name(reference_image_model, DEFAULT_GEMINI_IMAGE_MODEL)
        self.text_model = _normalize_model_name(text_model, "gemini-2.5-flash")
        self.api_key = api_key

        # Initialize the single unified client
        self.client = genai.Client(api_key=api_key)

    @staticmethod
    def _model_dump_safe(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return str(value)

    @classmethod
    def _text_response_debug(cls, response: types.GenerateContentResponse) -> dict[str, Any]:
        candidate = response.candidates[0] if response.candidates else None
        return {
            "finish_reason": str(getattr(candidate, "finish_reason", None)) if candidate is not None else None,
            "safety_ratings": cls._model_dump_safe(getattr(candidate, "safety_ratings", None))
            if candidate is not None
            else None,
            "prompt_feedback": cls._model_dump_safe(getattr(response, "prompt_feedback", None)),
        }

    @staticmethod
    def _extract_image_from_content_response(response: types.GenerateContentResponse) -> tuple[bytes, str | None]:
        images, response_text = GoogleProvider._extract_images_from_content_response(response)
        if images:
            return images[0].image_bytes, response_text

        candidates = getattr(response, "candidates", None) or []
        finish_reason = candidates[0].finish_reason if candidates else None
        raise AppException(
            f"Google image model returned no image data. Finish reason: {finish_reason}",
            code="EMPTY_RESPONSE",
        )

    @staticmethod
    def _extract_images_from_content_response(
        response: types.GenerateContentResponse,
    ) -> tuple[list[GeneratedImagePart], str | None]:
        text_parts: list[str] = []
        pending_text_parts: list[str] = []
        images: list[GeneratedImagePart] = []

        for part in getattr(response, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)
                pending_text_parts.append(text)

            inline_data = getattr(part, "inline_data", None)
            inline_bytes = getattr(inline_data, "data", None)
            if inline_bytes:
                images.append(
                    GeneratedImagePart(
                        image_bytes=inline_bytes,
                        mime_type=getattr(inline_data, "mime_type", None),
                        preceding_text="\n".join(pending_text_parts) or None,
                    )
                )
                pending_text_parts.clear()
                continue

            as_image = getattr(part, "as_image", None)
            image = as_image() if callable(as_image) else None
            if image and image.image_bytes:
                images.append(
                    GeneratedImagePart(
                        image_bytes=image.image_bytes,
                        mime_type=getattr(image, "mime_type", None),
                        preceding_text="\n".join(pending_text_parts) or None,
                    )
                )
                pending_text_parts.clear()

        return images, "\n".join(text_parts) or None

    @staticmethod
    def _is_transient_google_error(error: Exception) -> bool:
        text = str(error).upper()
        transient_markers = (
            "503",
            "500",
            "502",
            "504",
            "429",
            "UNAVAILABLE",
            "RESOURCE_EXHAUSTED",
            "DEADLINE_EXCEEDED",
            "INTERNAL",
            "SERVICE IS CURRENTLY UNAVAILABLE",
        )
        return any(marker in text for marker in transient_markers)

    async def _generate_text_content_with_transient_retry(
        self,
        prompt: str,
        *,
        response_mime_type: str | None,
        safety_settings: Any,
        max_output_tokens: int,
        temperature: float,
        transient_retries: int,
        retry_base_delay_seconds: float,
    ) -> types.GenerateContentResponse:
        max_attempts = max(1, transient_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self.client.aio.models.generate_content(
                    model=self.text_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                        response_mime_type=response_mime_type,
                        safety_settings=safety_settings,
                    ),
                )
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts or not self._is_transient_google_error(exc):
                    raise
                delay = max(0.0, retry_base_delay_seconds) * (2 ** (attempt - 1))
                logger.warning(
                    "Google text generation transient error; retrying attempt=%s/%s delay=%ss error=%s",
                    attempt + 1,
                    max_attempts,
                    delay,
                    exc,
                )
                if delay:
                    await asyncio.sleep(delay)

        raise last_error or AppException("Google text generation failed", code="GOOGLE_ERROR")

    async def create_character_from_photo(
        self,
        reference_image_path: Path | str,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate a storybook character image from a reference photo.

        Args:
            reference_image_path: Path to reference child photo
            prompt: Prompt for character generation
            **kwargs: Provider-specific options

        Returns:
            ImageGenerationResult with generated image and analysis metadata

        Raises:
            AppException: On API errors or invalid inputs
        """
        path = Path(reference_image_path)
        if not path.exists():
            raise AppException(f"Reference image not found: {path}", code="FILE_NOT_FOUND")

        try:
            image_bytes = path.read_bytes()
            mime_type = _mime_type_for_path(path)

            child_age_label = kwargs.get("child_age_label", "the child's profile age")
            child_age_visual_guidance = kwargs.get(
                "child_age_visual_guidance",
                "age-appropriate child height, body build, hands, feet, limbs, and facial maturity",
            )
            identity_profile = kwargs.get("identity_profile")
            identity_profile_json = kwargs.get("identity_profile_json")
            identity_profile_text = kwargs.get("identity_profile_text")

            if identity_profile:
                analysis_text = str(identity_profile_text or "").strip()
                if not identity_profile_json:
                    identity_profile_json = json.dumps(identity_profile, ensure_ascii=False)
                logger.info("Using caller-provided permanent identity profile for character generation")
            else:
                analysis_prompt = f"""Describe this photo to create a premium semi-realistic 3D storybook character model that remains recognizable as the same child.

Focus on exact visual identity details:
1. Hair color, hairstyle, hair direction, and side part
2. Face shape and facial proportions
3. Eye shape, natural eye size, eye spacing, and eye color
4. Eyebrow shape and thickness
5. Nose shape, smile, visible teeth or tooth gap, cheeks, and skin tone
6. Approximate age appearance. The child profile age is {child_age_label}; describe how the photo should be illustrated with {child_age_visual_guidance}.
7. Any small distinctive facial marks or unique features

Do not describe clothing, background, pose, camera crop, or photo quality.
Format your response as a clear visual identity list."""

                logger.info(f"Analyzing character profile using: {self.text_model}")
                analysis_response = await self.client.aio.models.generate_content(
                    model=self.text_model,
                    contents=[
                        analysis_prompt,
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    ],
                )

                analysis_text = analysis_response.text or ""
                identity_profile_json = None
                logger.info("Reference character features analyzed successfully")

            enhanced_prompt = (
                "Create a single polished premium semi-realistic 3D children's storybook character model from "
                "the reference photo and the permanent identity profile. This is a character-model conversion, "
                "not a photorealistic portrait. Preserve exact identity and transform only the artistic style. "
                "The character must remain recognizably the same child, with the same face shape, hairstyle, "
                "hair direction, hairline, hair volume, eye shape, natural eye size, eye color, eyebrows, nose, "
                "mouth shape, smile type, skin tone, cheeks, distinctive facial features, and age appearance. "
                f"The child profile age is {child_age_label}; preserve {child_age_visual_guidance}. "
                "Use soft stylized skin shading, detailed stylized hair, natural child proportions, and a "
                "warm high-end animated family-film look. The output should look like the reusable master "
                "character model for all future story pages. "
                "Do not create a raw photo, photorealistic passport portrait, flat cartoon, generic animated "
                "child, or a different stylized design. Do not enlarge the eyes, change the hairstyle, change "
                "hair direction, change the hairline, change facial structure, or copy reference-photo clothing. "
                "Generate a clean front-facing head-and-shoulders character model on a pure white studio "
                "background. "
                "Do not include text, logos, watermarks, borders, or extra characters. "
                "PERMANENT IDENTITY PROFILE JSON:\n"
                f"{identity_profile_json or '{}'}\n\n"
                f"Identity summary: {analysis_text}. "
                f"Scene and styling instruction: {prompt}"
            )

            reference_model = _normalize_model_name(
                kwargs.get("reference_image_model"),
                self.reference_image_model,
            )
            logger.info(f"Generating character image from reference using: {reference_model}")
            response = await self.client.aio.models.generate_content(
                model=reference_model,
                contents=[
                    enhanced_prompt,
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=types.ImageConfig(
                        aspect_ratio=kwargs.get("aspect_ratio", "1:1"),
                    ),
                ),
            )

            generated_image_bytes, response_text = self._extract_image_from_content_response(response)

            return ImageGenerationResult(
                image_bytes=generated_image_bytes,
                prompt_used=prompt,
                model=reference_model,
                revised_prompt=enhanced_prompt,
                metadata={
                    "analysis_text": analysis_text,
                    "identity_profile_used": bool(identity_profile),
                    "identity_profile_json": identity_profile_json,
                    "enhanced_prompt": enhanced_prompt,
                    "child_age_label": child_age_label,
                    "child_age_visual_guidance": child_age_visual_guidance,
                    "image_response_text": response_text,
                    "reference_path": str(path),
                    "provider": "google",
                    "mode": "gemini_reference_image",
                },
            )

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Character reference generation pipeline failed: {e}")
            raise AppException(f"Pipeline error: {str(e)}", code="GOOGLE_ERROR")

    async def generate_text(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text utilizing the modern client layout.

        Args:
            prompt: Full text prompt for LLM
            **kwargs: May include 'max_tokens', 'temperature'

        Returns:
            TextGenerationResult with generated text

        Raises:
            AppException: On API errors
        """
        # Keep your existing Mock infrastructure logic here if configured
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock LLM response instead of calling Google Gemini")

            # Extract age_group and page count from prompt for correct mock response
            age_group = AGE_GROUP_3_6  # Default
            if "0-3" in prompt or "0-2" in prompt or "2-4" in prompt:
                age_group = AGE_GROUP_0_3
            elif "6-9" in prompt or "6-8" in prompt:
                age_group = AGE_GROUP_6_9
            elif "3-6" in prompt or "4-6" in prompt:
                age_group = AGE_GROUP_3_6

            # Determine which mock response to return based on prompt content.
            # Check image planning first because that prompt includes "Story Plan JSON".
            if "IMAGE PLANNING" in prompt.upper() or "IMAGE PLAN" in prompt.upper():
                # Extract page count from story_json in prompt
                import re

                story_pages_count = 8  # Default
                page_count_match = re.search(r'"page_number":\s*(\d+)', prompt)
                if page_count_match:
                    # Find the max page_number to determine total pages
                    all_matches = re.findall(r'"page_number":\s*(\d+)', prompt)
                    if all_matches:
                        story_pages_count = max(int(m) for m in all_matches)
                mock_text = get_mock_image_plan_text(story_pages_count=story_pages_count)
            elif "story_plan_json" in prompt.lower():
                # Extract page count from story plan in prompt
                import re

                story_pages_count = 8  # Default
                page_count_match = re.search(r'"final_page_count":\s*(\d+)', prompt)
                if page_count_match:
                    story_pages_count = int(page_count_match.group(1))
                mock_text = get_mock_story_text(child_name="Emma", story_pages_count=story_pages_count)
            elif "STORY PLAN" in prompt.upper():
                mock_text = get_mock_story_plan_text(child_name="Emma", age_group=age_group)
            else:
                # Default mock response
                mock_text = get_mock_story_plan_text(child_name="Emma", age_group=age_group)

            return TextGenerationResult(
                text=mock_text,
                prompt_used=prompt,
                model=self.text_model,
                metadata={"mock_mode": True, "provider": "google"},
            )

        logger.info(f"Generating text with text model: {self.text_model}")

        try:
            # Use unified generation method for standard text requests
            response_format = kwargs.get("response_format")
            response_mime_type = "application/json" if response_format == {"type": "json_object"} else None
            max_attempts = int(kwargs.get("empty_response_retries", 2)) + 1
            transient_retries = int(kwargs.get("transient_error_retries", settings.GOOGLE_TEXT_TRANSIENT_RETRIES))
            retry_base_delay_seconds = float(
                kwargs.get(
                    "transient_error_retry_base_delay_seconds",
                    settings.GOOGLE_TEXT_TRANSIENT_RETRY_BASE_DELAY_SECONDS,
                )
            )
            last_empty_message = "Empty response from Google API"

            for attempt in range(1, max_attempts + 1):
                safety_settings = kwargs.get("safety_settings")
                response = await self._generate_text_content_with_transient_retry(
                    prompt,
                    response_mime_type=response_mime_type,
                    safety_settings=safety_settings,
                    max_output_tokens=kwargs.get("max_tokens", 36000),
                    temperature=kwargs.get("temperature", 0.7),
                    transient_retries=transient_retries,
                    retry_base_delay_seconds=retry_base_delay_seconds,
                )

                debug = self._text_response_debug(response)
                if response.text:
                    usage_metadata = getattr(response, "usage_metadata", None)
                    usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None

                    return TextGenerationResult(
                        text=response.text,
                        prompt_used=prompt,
                        model=self.text_model,
                        metadata={
                            "provider": "google",
                            "finish_reason": debug["finish_reason"],
                            "usage": usage,
                        },
                    )

                last_empty_message = (
                    "Empty response from Google API"
                    f" (attempt={attempt}/{max_attempts}, finish_reason={debug['finish_reason']}, "
                    f"prompt_feedback={debug['prompt_feedback']}, safety_ratings={debug['safety_ratings']})"
                )
                logger.warning(last_empty_message)

            raise AppException(last_empty_message, code="EMPTY_RESPONSE")
        except AppException:
            raise
        except Exception as e:
            logger.error(f"Google text generation failed: {e}")
            raise AppException(f"Text generation failed: {str(e)}", code="GOOGLE_ERROR")

    async def describe_character_image(
        self,
        image_bytes: bytes,
        prompt: str,
        mime_type: str = "image/png",
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Analyze the generated master character portrait with a vision prompt."""
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock character description instead of calling Google Gemini")
            return TextGenerationResult(
                text=(
                    '{"age_appearance":"young child","face_shape":"round","cheek_shape":"soft round",'
                    '"jawline_shape":"soft childlike","chin_shape":"small rounded","skin_tone":"warm medium",'
                    '"hair_color":"dark brown","hair_length":"short","hair_texture":"smooth",'
                    '"hair_style":"neatly combed","hair_direction":"slightly side-swept",'
                    '"eye_color":"brown","eye_shape":"almond","eye_size":"natural child-sized",'
                    '"eyebrow_shape":"soft arched","eyebrow_thickness":"medium",'
                    '"nose_shape":"small rounded","mouth_shape":"small rounded",'
                    '"smile_characteristics":"gentle closed-mouth smile","ear_visibility":"partly visible",'
                    '"distinctive_features":["round cheeks"],'
                    '"identity_summary":"A young child with a round face, soft round cheeks, warm medium skin tone, brown almond eyes, a small rounded nose, and a small rounded mouth with a gentle closed-mouth smile. The child has short dark brown smooth hair, neatly combed with a slight side-swept direction. The overall neck-up identity should stay soft, childlike, and consistent, with natural child-sized eyes and round cheeks as the main distinctive features."}'
                ),
                prompt_used=prompt,
                model=self.text_model,
                metadata={"mock_mode": True, "provider": "google"},
            )

        if not image_bytes:
            raise AppException("Character image is empty", code="EMPTY_IMAGE")

        logger.info(f"Describing generated character image with Google vision model: {self.text_model}")

        try:
            response = await self.client.aio.models.generate_content(
                model=self.text_model,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=kwargs.get("max_tokens", 2000),
                    temperature=kwargs.get("temperature", 0.2),
                    response_mime_type="application/json",
                ),
            )
            if not response.text:
                debug = self._text_response_debug(response)
                raise AppException(
                    "Empty character description response from Google API "
                    f"(finish_reason={debug['finish_reason']})",
                    code="EMPTY_RESPONSE",
                )

            usage_metadata = getattr(response, "usage_metadata", None)
            usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None
            return TextGenerationResult(
                text=response.text,
                prompt_used=prompt,
                model=self.text_model,
                metadata={"provider": "google", "usage": usage},
            )
        except AppException:
            raise
        except Exception as e:
            logger.error(f"Google character description failed: {e}")
            raise AppException(f"Character description failed: {str(e)}", code="GOOGLE_ERROR")

    @staticmethod
    def _story_reference_inputs(
        reference_image_base64: str | None,
        kwargs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Normalize one legacy reference or many named references for Gemini."""
        raw_references = kwargs.get("reference_images_base64")
        references: list[dict[str, Any]] = []
        if isinstance(raw_references, list):
            for index, item in enumerate(raw_references, start=1):
                if isinstance(item, str):
                    references.append(
                        {
                            "image_base64": item,
                            "character_id": f"reference_{index}",
                            "name": f"Reference {index}",
                            "role": "character_reference",
                        }
                    )
                elif isinstance(item, dict):
                    image_base64 = item.get("image_base64") or item.get("base64") or item.get("data")
                    if image_base64:
                        references.append(
                            {
                                "image_base64": image_base64,
                                "character_id": item.get("character_id") or f"reference_{index}",
                                "name": item.get("name") or item.get("character_name") or f"Reference {index}",
                                "role": item.get("role") or "character_reference",
                                "image_url": item.get("image_url") or item.get("reference_image_url"),
                            }
                        )
        if not references and reference_image_base64:
            references.append(
                {
                    "image_base64": reference_image_base64,
                    "character_id": "hero_child",
                    "name": "Hero child",
                    "role": "master_character_reference_portrait",
                }
            )
        return references

    @staticmethod
    def _reference_manifest_instruction(references: list[dict[str, Any]]) -> str:
        if len(references) <= 1:
            return (
                "\nThe only attached image after this prompt is the generated Master Character Reference Portrait "
                "from character_image_url. It is the PRIMARY visual identity reference for the hero child. "
                "No original child avatar photo is attached. Preserve the master character's face, facial "
                "proportions, hairstyle, hairline, skin tone, and age appearance. "
            )

        lines = [
            "\nAttached images after this prompt are named character identity references in this exact order.",
            "Use each attached image only for the matching character's face/head identity and stable visual design.",
            "Do not copy reference-image clothing, crop, pose, white background, or studio framing unless the scene prompt asks for it.",
            "Reference order:",
        ]
        for index, reference in enumerate(references, start=1):
            character_id = str(reference.get("character_id") or f"reference_{index}")
            name = str(reference.get("name") or character_id)
            role = str(reference.get("role") or "character_reference")
            lines.append(f"{index}. character_id={character_id}; name={name}; role={role}")
        lines.append(
            "For the hero child, preserve face, facial proportions, eye shape, natural eye size, hairstyle, "
            "hairline, skin tone, and age appearance. For side characters, preserve the attached reference "
            "character's face/head shape, hair/fur/body pattern, colors, outfit/accessories, scale, and distinctive features."
        )
        return "\n".join(lines) + "\n"

    async def create_story_image(
        self,
        prompt: str,
        reference_image_base64: str | None = None,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate a story image using a prompt and base64 character reference image."""
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock story image instead of calling Google Gemini")
            placeholder_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
                b"\x00\x01\x01\x00\x05\x18\r*\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            return ImageGenerationResult(
                image_bytes=placeholder_png,
                prompt_used=prompt,
                model=self.reference_image_model,
                metadata={"mock_mode": True, "placeholder": True, "provider": "google"},
            )

        reference_inputs = self._story_reference_inputs(reference_image_base64, kwargs)
        if not reference_inputs:
            raise AppException("At least one character reference image is required", code="MISSING_REFERENCE_IMAGE")

        parsed_references = []
        try:
            for reference in reference_inputs:
                parsed = parse_base64_image_data(str(reference.get("image_base64") or ""))
                parsed_references.append((reference, parsed))
        except ValueError as e:
            raise AppException(str(e), code="INVALID_REFERENCE_IMAGE")

        model = _normalize_model_name(
            kwargs.get("model") or kwargs.get("reference_image_model"),
            self.reference_image_model,
        )
        if not _is_gemini_image_model(model):
            raise AppException(
                f"Story image generation with a reference image requires a Gemini image model. Use "
                f"'{DEFAULT_GEMINI_IMAGE_MODEL}'.",
                code="UNSUPPORTED_MODEL",
            )

        consistency_instruction = (
            self._reference_manifest_instruction(reference_inputs)
            + "Do not invent a new face, hairstyle, character variant, or story-theme costume. Use the Character "
            "Identity Lock inside the scene prompt as the written identity and age lock. "
            "Use the Visual Bible and scene prompt for the single locked story outfit, shoes, accessories, "
            "body scale, rendering style, and environment. Do not copy portrait clothing, portrait crop, "
            "white studio background, or head-and-shoulders framing. If the scene prompt conflicts with "
            "an attached character identity reference, keep the attached character identity and only change "
            "the action, expression, clothing allowed by the page prompt, or environment.\n"
        )

        story_prompt = (
            "Generate one polished children's storybook illustration. Character consistency is more important "
            "than scene costume, theme costume, or decorative story details."
            f"{consistency_instruction}"
            "Use a premium semi-realistic 3D storybook style while following this scene prompt. The child must "
            "match the age guidance in the scene prompt and keep the same master-character face and hairstyle in every image. "
            "Use the same single 3D character model across the full book, as if the Master Character Reference "
            "Image has been posed in each scene. Character likeness, age consistency, "
            "modest child-safe clothing coverage, and family-friendly composition are more important than "
            "decorative scene details:\n\n"
            f"{prompt}"
        )

        logger.info(f"Generating story image with Google reference image model: {model}")

        try:
            contents: list[Any] = [story_prompt]
            for _reference, parsed_reference in parsed_references:
                contents.append(
                    types.Part.from_bytes(
                        data=parsed_reference.image_bytes,
                        mime_type=parsed_reference.mime_type,
                    )
                )

            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        image_config=types.ImageConfig(
                            aspect_ratio=kwargs.get("aspect_ratio", "1:1"),
                        ),
                    ),
                )
                image_bytes, response_text = self._extract_image_from_content_response(response)
                usage_metadata = getattr(response, "usage_metadata", None)
                usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None
            except AppException as exc:
                if exc.code != "EMPTY_RESPONSE":
                    raise

                logger.warning(
                    "Google reference story image returned no image; retrying once with reference images: %s",
                    exc.message,
                )
                retry_prompt = (
                    "Generate one polished, child-safe children's storybook illustration. "
                    "Use the only attached image, the Master Character Reference Portrait, as the facial identity "
                    "and hairstyle reference. No original child avatar photo is attached. "
                    "Use the written visual bible for the story outfit, accessories, body scale, "
                    "rendering style, and scene details. Do not copy portrait clothing or portrait crop. "
                    "Keep natural age-appropriate proportions, consistent face, consistent hairstyle, "
                    "modest family-friendly clothing coverage, and a readable medium storybook composition. "
                    "For water-play scenes, use rash guards or t-shirts covering shoulders and the upper body, "
                    "knee-length shorts or leggings, and water shoes. Use covered water-play outfits for every "
                    "visible person and keep background people tiny, simplified, fully clothed, or omitted.\n\n"
                    f"{prompt}"
                )
                retry_contents: list[Any] = [retry_prompt]
                for _reference, parsed_reference in parsed_references:
                    retry_contents.append(
                        types.Part.from_bytes(
                            data=parsed_reference.image_bytes,
                            mime_type=parsed_reference.mime_type,
                        )
                    )

                try:
                    response = await self.client.aio.models.generate_content(
                        model=model,
                        contents=retry_contents,
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE", "TEXT"],
                            image_config=types.ImageConfig(
                                aspect_ratio=kwargs.get("aspect_ratio", "1:1"),
                            ),
                        ),
                    )
                    image_bytes, response_text = self._extract_image_from_content_response(response)
                    usage_metadata = getattr(response, "usage_metadata", None)
                    usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None
                    story_prompt = retry_prompt
                except AppException as retry_exc:
                    if retry_exc.code == "EMPTY_RESPONSE":
                        raise AppException(
                            "Google reference story image returned no image after retry. "
                            "Not using text-only fallback because character reference consistency is required.",
                            code="GOOGLE_REFERENCE_IMAGE_EMPTY",
                        ) from retry_exc
                    raise

            return ImageGenerationResult(
                image_bytes=image_bytes,
                prompt_used=story_prompt,
                model=model,
                metadata={
                    "provider": "google",
                    "mode": "story_reference_image",
                    "aspect_ratio": kwargs.get("aspect_ratio", "1:1"),
                    "reference_mime_type": parsed_references[0][1].mime_type,
                    "reference_role": parsed_references[0][0].get("role"),
                    "reference_count": len(parsed_references),
                    "reference_character_ids": [
                        str(reference.get("character_id") or "")
                        for reference, _parsed_reference in parsed_references
                        if reference.get("character_id")
                    ],
                    "single_reference_used": len(parsed_references) == 1,
                    "image_response_text": response_text,
                    "usage": usage,
                },
            )

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Google story image generation failed: {e}")
            raise AppException(f"Story image generation failed: {str(e)}", code="GOOGLE_ERROR")

    async def generate_image(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate an image using Google's supported image generation SDK methods.

        Args:
            prompt: Image prompt for generation
            **kwargs: May include 'aspect_ratio' (1:1, 3:4, 4:3, 16:9)

        Returns:
            ImageGenerationResult with generated image bytes

        Raises:
            AppException: On API errors
        """
        model = _normalize_model_name(kwargs.get("model"), self.image_model)
        logger.info(f"Generating image with Google model: {model}")

        try:
            if _is_imagen_model(model):
                response = await self.client.aio.models.generate_images(
                    model=model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio=kwargs.get("aspect_ratio", "1:1"),
                        output_mime_type=kwargs.get("output_mime_type", "image/jpeg"),
                        include_safety_attributes=True,
                        include_rai_reason=True,
                    ),
                )

                if not response.generated_images:
                    raise AppException(
                        "No image data returned from Google Imagen. The prompt may have been blocked or the model "
                        "may require a paid Gemini API tier.",
                        code="EMPTY_RESPONSE",
                    )

                generated_image = response.generated_images[0]
                if not generated_image.image or not generated_image.image.image_bytes:
                    raise AppException("Google Imagen returned an empty image payload", code="EMPTY_RESPONSE")

                return ImageGenerationResult(
                    image_bytes=generated_image.image.image_bytes,
                    prompt_used=prompt,
                    model=model,
                    metadata={
                        "provider": "google",
                        "mode": "imagen",
                        "aspect_ratio": kwargs.get("aspect_ratio", "1:1"),
                        "mime_type": generated_image.image.mime_type,
                    },
                )

            if not _is_gemini_image_model(model):
                raise AppException(
                    f"Unsupported Google image model '{model}'. Use '{DEFAULT_IMAGEN_MODEL}' for Imagen or "
                    f"'{DEFAULT_GEMINI_IMAGE_MODEL}' for Gemini image generation.",
                    code="UNSUPPORTED_MODEL",
                )

            response = await self.client.aio.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=types.ImageConfig(
                        aspect_ratio=kwargs.get("aspect_ratio", "1:1"),
                    ),
                ),
            )
            image_bytes, response_text = self._extract_image_from_content_response(response)
            usage_metadata = getattr(response, "usage_metadata", None)
            usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None
            return ImageGenerationResult(
                image_bytes=image_bytes,
                prompt_used=prompt,
                model=model,
                metadata={
                    "provider": "google",
                    "mode": "gemini_image",
                    "aspect_ratio": kwargs.get("aspect_ratio", "1:1"),
                    "image_response_text": response_text,
                    "usage": usage,
                },
            )

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Google image generation failed: {e}")
            raise AppException(f"Image generation failed: {str(e)}", code="IMAGEN_ERROR")

    async def generate_interleaved_images(
        self,
        prompt: str,
        *,
        expected_count: int,
        aspect_ratio: str = "1:1",
        model: str | None = None,
    ) -> MultiImageGenerationResult:
        """Generate multiple Gemini image outputs from one interleaved prompt."""
        if expected_count <= 0:
            raise AppException("expected_count must be greater than zero", code="INVALID_IMAGE_COUNT")

        image_model = _normalize_model_name(model, self.reference_image_model)
        if not _is_gemini_image_model(image_model):
            raise AppException(
                f"Multi-image generation requires a Gemini image model. Use '{DEFAULT_GEMINI_IMAGE_MODEL}'.",
                code="UNSUPPORTED_MODEL",
            )

        logger.info(
            "Generating %s interleaved images with Google model: %s",
            expected_count,
            image_model,
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=image_model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                ),
            )
            images, response_text = self._extract_images_from_content_response(response)
            usage_metadata = getattr(response, "usage_metadata", None)
            usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None

            if len(images) != expected_count:
                raise AppException(
                    f"Google Gemini returned {len(images)} images; expected {expected_count}.",
                    code="GOOGLE_MULTI_IMAGE_COUNT_MISMATCH",
                    details={
                        "expected_count": expected_count,
                        "received_count": len(images),
                        "aspect_ratio": aspect_ratio,
                        "model": image_model,
                        "response_text": response_text,
                    },
                )

            return MultiImageGenerationResult(
                images=images,
                prompt_used=prompt,
                model=image_model,
                metadata={
                    "provider": "google",
                    "mode": "gemini_interleaved_multi_image",
                    "aspect_ratio": aspect_ratio,
                    "image_response_text": response_text,
                    "usage": usage,
                },
            )

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Google interleaved image generation failed: {e}")
            raise AppException(f"Interleaved image generation failed: {str(e)}", code="GOOGLE_ERROR")
