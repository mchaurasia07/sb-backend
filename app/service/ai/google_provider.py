import logging
import mimetypes
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings
from app.core.exceptions import AppException
from app.service.ai.base import (
    AIProvider,
    ImageGenerationResult,
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
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"

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
    def _extract_image_from_content_response(response: types.GenerateContentResponse) -> tuple[bytes, str | None]:
        text_parts: list[str] = []
        for part in response.parts or []:
            if part.text:
                text_parts.append(part.text)
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data, "\n".join(text_parts) or None

            image = part.as_image()
            if image and image.image_bytes:
                return image.image_bytes, "\n".join(text_parts) or None

        finish_reason = None
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason
        raise AppException(
            f"Google image model returned no image data. Finish reason: {finish_reason}",
            code="EMPTY_RESPONSE",
        )

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

            analysis_prompt = f"""Describe this photo to create an illustrated storybook character that remains recognizable as the same real child.

Focus on exact visual identity details:
1. Hair color, hairstyle, hair direction, and side part
2. Face shape and facial proportions
3. Eye shape, natural eye size, eye spacing, and eye color
4. Eyebrow shape and thickness
5. Nose shape, smile, visible teeth or tooth gap, cheeks, and skin tone
6. Approximate age appearance. The child profile age is {child_age_label}; describe how the photo should be illustrated with {child_age_visual_guidance}.
7. Clothing details and colors
8. Any small distinctive facial marks or unique features

Format your response as a clear description list."""

            logger.info(f"Analyzing character profile using: {self.text_model}")
            analysis_response = await self.client.aio.models.generate_content(
                model=self.text_model,
                contents=[
                    analysis_prompt,
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
            )

            analysis_text = analysis_response.text or ""
            logger.info("Reference character features analyzed successfully")

            enhanced_prompt = (
                "Create a single polished children's storybook character illustration from the reference photo. "
                "The character must remain recognizably the same real child, with the same face shape, hairstyle, "
                "eye shape, natural eye size, eyebrows, nose, smile, skin tone, and approximate age. "
                f"The child profile age is {child_age_label}; preserve {child_age_visual_guidance}. "
                "Use a clean, warm, premium semi-realistic 3D storybook style with natural child proportions. "
                "Do not redesign the child into a generic cartoon character, do not enlarge the eyes, and do not "
                "change the hairstyle or facial structure. "
                "Do not include text, logos, watermarks, borders, or extra characters. "
                f"Reference analysis: {analysis_text}. "
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
            age_group = "5-7"  # Default
            if "2-4" in prompt:
                age_group = "2-4"
            elif "5-7" in prompt:
                age_group = "5-7"
            elif "8-12" in prompt:
                age_group = "8-12"

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

            response = await self.client.aio.models.generate_content(
                model=self.text_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=kwargs.get("max_tokens", 36000),
                    temperature=kwargs.get("temperature", 0.7),
                    response_mime_type=response_mime_type,
                ),
            )

            if not response.text:
                raise AppException("Empty response from Google API", code="EMPTY_RESPONSE")

            finish_reason = None
            if response.candidates:
                finish_reason = getattr(response.candidates[0], "finish_reason", None)

            usage_metadata = getattr(response, "usage_metadata", None)
            usage = usage_metadata.model_dump() if hasattr(usage_metadata, "model_dump") else None

            return TextGenerationResult(
                text=response.text,
                prompt_used=prompt,
                model=self.text_model,
                metadata={
                    "provider": "google",
                    "finish_reason": str(finish_reason) if finish_reason is not None else None,
                    "usage": usage,
                },
            )
        except Exception as e:
            logger.error(f"Google text generation failed: {e}")
            raise AppException(f"Text generation failed: {str(e)}", code="GOOGLE_ERROR")

    async def create_story_image(
        self,
        prompt: str,
        reference_image_base64: str,
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

        try:
            reference_image = parse_base64_image_data(reference_image_base64)
            consistency_reference = None
            if kwargs.get("consistency_reference_image_base64"):
                consistency_reference = parse_base64_image_data(kwargs["consistency_reference_image_base64"])
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

        consistency_instruction = ""
        child_age_label = kwargs.get("child_age_label", "the child's profile age")
        child_age_visual_guidance = kwargs.get(
            "child_age_visual_guidance",
            "age-appropriate child height, body build, hands, feet, limbs, and facial maturity",
        )
        if consistency_reference is not None:
            consistency_instruction = (
                "\nThe first attached image after this prompt is the generated master character image from "
                "character_image_url. It is the PRIMARY reference and must be used as the exact illustrated "
                "character model. Match the master character's face, facial proportions, eye shape, natural eye "
                f"size, hairstyle, {child_age_label} age appearance, age-appropriate body proportions, and "
                "illustration style. The second attached image is the original child profile photo and is only a "
                "supporting real-identity reference. Do not redesign the face or make the child look older or younger. "
                f"Preserve this age/body guidance: {child_age_visual_guidance}. "
                "Clothing may follow the single locked story outfit from the scene prompt, but it must remain "
                "identical across the whole book. If the scene prompt conflicts with the master character face, "
                "age, or body proportions, keep the master character design and only change the action/environment.\n"
            )
        else:
            consistency_instruction = (
                "\nOnly the original child profile photo is attached. Preserve the real child identity and do "
                "not invent a new outfit or story-theme costume.\n"
            )

        story_prompt = (
            "Generate one polished children's storybook illustration. Character consistency is more important "
            "than scene costume, theme costume, or decorative story details."
            f"{consistency_instruction}"
            "Use a premium semi-realistic 3D storybook style while following this scene prompt. The child must "
            f"look like {child_age_label} and keep the same master-character face in every image. Character "
            "likeness and age consistency are more important than costume or scene details:\n\n"
            f"{prompt}"
        )

        logger.info(f"Generating story image with Google reference image model: {model}")

        try:
            contents: list[Any] = [story_prompt]
            if consistency_reference is not None:
                contents.extend(
                    [
                        types.Part.from_bytes(
                            data=consistency_reference.image_bytes,
                            mime_type=consistency_reference.mime_type,
                        ),
                        types.Part.from_bytes(
                            data=reference_image.image_bytes,
                            mime_type=reference_image.mime_type,
                        ),
                    ]
                )
            else:
                contents.append(
                    types.Part.from_bytes(
                        data=reference_image.image_bytes,
                        mime_type=reference_image.mime_type,
                    )
                )

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

            return ImageGenerationResult(
                image_bytes=image_bytes,
                prompt_used=story_prompt,
                model=model,
                metadata={
                    "provider": "google",
                    "mode": "story_reference_image",
                    "aspect_ratio": kwargs.get("aspect_ratio", "1:1"),
                    "reference_mime_type": reference_image.mime_type,
                    "consistency_reference_used": consistency_reference is not None,
                    "consistency_reference_mime_type": (
                        consistency_reference.mime_type if consistency_reference is not None else None
                    ),
                    "image_response_text": response_text,
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
            return ImageGenerationResult(
                image_bytes=image_bytes,
                prompt_used=prompt,
                model=model,
                metadata={
                    "provider": "google",
                    "mode": "gemini_image",
                    "aspect_ratio": kwargs.get("aspect_ratio", "1:1"),
                    "image_response_text": response_text,
                },
            )

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Google image generation failed: {e}")
            raise AppException(f"Image generation failed: {str(e)}", code="IMAGEN_ERROR")
