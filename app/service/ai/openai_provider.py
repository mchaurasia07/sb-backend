import base64
import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, BadRequestError, OpenAIError, RateLimitError

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

_SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _extract_base64_image_data(image_path: Path | str) -> str:
    """Read image file and encode as base64 data URL for OpenAI."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    if path.suffix.lower() not in _SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image format: {path.suffix}")

    extension = path.suffix.lower()
    media_type_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = media_type_map[extension]

    image_data = path.read_bytes()
    b64_encoded = base64.standard_b64encode(image_data).decode("utf-8")
    return f"data:{media_type};base64,{b64_encoded}"


class OpenAIProvider(AIProvider):
    """OpenAI implementation of the AI provider interface."""

    def __init__(self, api_key: str, image_model: str, text_model: str):
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self.api_key = api_key
        self.image_model = image_model
        self.text_model = text_model
        self._client = AsyncOpenAI(api_key=api_key)

    async def create_character_from_photo(
        self,
        reference_image_path: Path | str,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate character image from reference child photo using two-step approach.

        1. Analyzes the reference image with vision API to extract exact features
        2. Uses analyzed features to enhance the prompt before generating character

        Args:
            reference_image_path: Path to reference child photo
            prompt: Prompt for character generation (will be enhanced with analysis)
            **kwargs: May include 'size' and 'quality'

        Returns:
            ImageGenerationResult with generated image bytes

        Raises:
            AppException: On API errors or invalid inputs
        """
        path = Path(reference_image_path)
        if not path.exists():
            raise AppException(f"Reference image not found: {path}", code="FILE_NOT_FOUND")

        logger.info(f"Generating character from reference: {path.name}, model={self.image_model}")

        try:
            b64_image = _extract_base64_image_data(path)
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
                # Step 1: Analyze reference image with vision API for legacy callers.
                logger.info("Analyzing reference image for exact features...")

                analysis_prompt = f"""Describe this photo to create a premium semi-realistic 3D storybook character model that matches it.

Describe:
1. Hair: color, length, style
2. Face appearance: shape, distinctive features
3. Eyes: color and appearance
4. Skin tone
5. Approximate age. The child profile age is {child_age_label}; describe how to preserve {child_age_visual_guidance}.
6. Any unique stable visual characteristics
7. Overall face and hair model to recreate in storybook form

Do not describe clothing, background, pose, camera crop, or photo quality.
Focus only on stable identity details needed for a reusable storybook character model."""

                logger.info(f"Analysis Prompt being sent to gpt-4o:\n{analysis_prompt}")

                analysis_response = await self._client.chat.completions.create(
                    model="gpt-4o",
                    max_tokens=800,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": analysis_prompt},
                                {"type": "image_url", "image_url": {"url": b64_image}},
                            ],
                        }
                    ],
                )

                analysis_text = analysis_response.choices[0].message.content
                identity_profile_json = None
                logger.info("Reference image analysis complete")

            # Step 2: Enhance prompt with analysis
            enhanced_prompt = (
                "Create a reusable premium semi-realistic 3D children's storybook character model from the "
                "reference photo and permanent identity profile. This is a character-model conversion, not a "
                "photorealistic portrait. Preserve exact identity and transform only the artistic style. "
                "Keep the same face shape, hairstyle, hair direction, hairline, hair volume, eye shape, "
                "natural eye size, eye color, eyebrows, nose, mouth shape, smile type, skin tone, cheeks, "
                "distinctive facial features, and age appearance. "
                "Use soft stylized skin shading, detailed stylized hair, and a warm high-end animated "
                "family-film look. Do not create a raw photo, flat cartoon, generic animated child, or a "
                "different stylized design. Do not copy reference-photo clothing.\n\n"
                f"{prompt}\n\n"
                f"CHILD PROFILE AGE: {child_age_label}\n"
                f"AGE APPEARANCE GUIDANCE: {child_age_visual_guidance}\n\n"
                f"PERMANENT IDENTITY PROFILE JSON:\n{identity_profile_json or '{}'}\n\n"
                f"IDENTITY SUMMARY:\n{analysis_text}\n\n"
                "Generate the master storybook character model matching the analyzed identity details, including the correct profile age."
            )

            # Step 3: Generate image with enhanced prompt
            logger.info("Generating character image with analysis-enhanced prompt...")
            logger.info(f"Enhanced Prompt being sent to {self.image_model}:\n{enhanced_prompt}")

            response = await self._client.images.generate(
                model=self.image_model,
                prompt=enhanced_prompt,
                size=kwargs.get("size", "1024x1536"),
                quality=kwargs.get("quality", "high"),
                n=1,
            )
        except BadRequestError as e:
            error_msg = self._extract_error_message(e)
            logger.error(f"OpenAI rejected character generation request: {error_msg}")
            raise AppException(f"Character generation failed: {error_msg}", code="OPENAI_ERROR")
        except RateLimitError as e:
            logger.error(f"OpenAI rate limit exceeded: {e}")
            raise AppException(
                "OpenAI rate limit exceeded. Please retry after a few moments.",
                code="RATE_LIMIT_ERROR",
            )
        except OpenAIError as e:
            logger.error(f"OpenAI error: {e}")
            raise AppException(
                "OpenAI service error. Please verify your API key and billing.",
                code="OPENAI_ERROR",
            )

        if not response.data or not response.data[0].b64_json:
            raise AppException("OpenAI returned no image data", code="OPENAI_ERROR")

        image_bytes = base64.b64decode(response.data[0].b64_json)
        revised_prompt = getattr(response.data[0], "revised_prompt", None)

        logger.info(f"Successfully generated character using {self.image_model}")
        return ImageGenerationResult(
            image_bytes=image_bytes,
            prompt_used=enhanced_prompt,
            model=self.image_model,
            revised_prompt=revised_prompt,
            metadata={
                "size": kwargs.get("size", "1024x1536"),
                "quality": kwargs.get("quality", "high"),
                "analysis_text": analysis_text,
                "identity_profile_used": bool(identity_profile),
                "identity_profile_json": identity_profile_json,
                "enhanced_prompt": enhanced_prompt,
                "child_age_label": child_age_label,
                "child_age_visual_guidance": child_age_visual_guidance,
            },
        )

    async def generate_text(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text using OpenAI chat completion API.

        Args:
            prompt: Full text prompt for LLM
            **kwargs: May include 'max_tokens', 'temperature'

        Returns:
            TextGenerationResult with generated text

        Raises:
            AppException: On API errors
        """
        # Check if mock mode is enabled
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock LLM response instead of calling OpenAI")

            # Extract age_group from prompt for correct mock response
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
                story_pages_count = 8  # Default
                import re
                page_count_match = re.search(r'"page_number":\s*(\d+)', prompt)
                if page_count_match:
                    # Find the max page_number to determine total pages
                    all_matches = re.findall(r'"page_number":\s*(\d+)', prompt)
                    if all_matches:
                        story_pages_count = max(int(m) for m in all_matches)
                mock_text = get_mock_image_plan_text(story_pages_count=story_pages_count)
            elif "story_plan_json" in prompt.lower():
                # Extract page count from story plan in prompt
                story_pages_count = 8  # Default
                import re
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
                metadata={"mock_mode": True},
            )

        logger.info(f"Generating text with {self.text_model}")

        try:
            response = await self._client.chat.completions.create(
                model=self.text_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=kwargs.get("max_tokens", 36000),
                temperature=kwargs.get("temperature", 0.7),
                response_format=kwargs.get("response_format", None),
            )
        except BadRequestError as e:
            error_msg = self._extract_error_message(e)
            logger.error(f"OpenAI rejected text generation request: {error_msg}")
            raise AppException(f"Text generation failed: {error_msg}", code="OPENAI_ERROR")
        except RateLimitError as e:
            logger.error(f"OpenAI rate limit exceeded: {e}")
            raise AppException(
                "OpenAI rate limit exceeded. Please retry after a few moments.",
                code="RATE_LIMIT_ERROR",
            )
        except OpenAIError as e:
            logger.error(f"OpenAI error: {e}")
            raise AppException(
                "OpenAI service error. Please verify your API key and billing.",
                code="OPENAI_ERROR",
            )

        if not response.choices:
            raise AppException("OpenAI returned no text", code="OPENAI_ERROR")

        content = response.choices[0].message.content
        if not content:
            raise AppException("OpenAI returned empty text", code="OPENAI_ERROR")

        logger.info(f"Successfully generated text using {self.text_model}")
        return TextGenerationResult(
            text=content,
            prompt_used=prompt,
            model=self.text_model,
            metadata={
                "finish_reason": response.choices[0].finish_reason,
                "usage": response.usage.model_dump() if response.usage else None,
            },
        )

    async def describe_character_image(
        self,
        image_bytes: bytes,
        prompt: str,
        mime_type: str = "image/png",
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Analyze the generated master character portrait with a vision prompt."""
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock character description instead of calling OpenAI")
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
                model=kwargs.get("vision_model", "gpt-4o"),
                metadata={"mock_mode": True, "provider": "openai"},
            )

        if not image_bytes:
            raise AppException("Character image is empty", code="EMPTY_IMAGE")

        encoded_image = base64.standard_b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{encoded_image}"
        model = kwargs.get("vision_model", "gpt-4o")

        logger.info(f"Describing generated character image with OpenAI vision model: {model}")

        try:
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=kwargs.get("max_tokens", 1200),
                temperature=kwargs.get("temperature", 0.2),
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
            )
        except BadRequestError as e:
            error_msg = self._extract_error_message(e)
            logger.error(f"OpenAI rejected character description request: {error_msg}")
            raise AppException(f"Character description failed: {error_msg}", code="OPENAI_ERROR")
        except RateLimitError as e:
            logger.error(f"OpenAI rate limit exceeded: {e}")
            raise AppException(
                "OpenAI rate limit exceeded. Please retry after a few moments.",
                code="RATE_LIMIT_ERROR",
            )
        except OpenAIError as e:
            logger.error(f"OpenAI character description error: {e}")
            raise AppException(
                "OpenAI service error while describing character image.",
                code="OPENAI_ERROR",
            )

        if not response.choices or not response.choices[0].message.content:
            raise AppException("OpenAI returned empty character description", code="OPENAI_ERROR")

        return TextGenerationResult(
            text=response.choices[0].message.content,
            prompt_used=prompt,
            model=model,
            metadata={
                "provider": "openai",
                "finish_reason": response.choices[0].finish_reason,
                "usage": response.usage.model_dump() if response.usage else None,
            },
        )

    async def create_story_image(
        self,
        prompt: str,
        reference_image_base64: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate a story image using a prompt and base64 character reference image."""
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock story image instead of calling OpenAI")
            placeholder_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
                b"\x00\x01\x01\x00\x05\x18\r*\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            return ImageGenerationResult(
                image_bytes=placeholder_png,
                prompt_used=prompt,
                model=self.image_model,
                revised_prompt=None,
                metadata={"mock_mode": True, "placeholder": True, "mode": "story_reference_image"},
            )

        try:
            reference_image = parse_base64_image_data(reference_image_base64)

            analysis_prompt = f"""Describe the Master Character Reference Portrait for consistent storybook illustration.

Focus on:
1. Exact illustrated master character face, hairstyle, hairline, facial proportions, and age appearance
2. Hair style, hair direction, and color
3. Eye shape, natural eye size, eye spacing, and eye color
4. Eyebrow shape, nose shape, smile, teeth, cheeks, and skin tone
5. How the master portrait should remain consistent when posed in story scenes

No original child avatar photo is attached for story image generation.
Return a concise visual consistency description of the attached Master Character Reference Portrait."""
            content: list[dict[str, Any]] = [{"type": "text", "text": analysis_prompt}]
            content.extend(
                [
                    {
                        "type": "text",
                        "text": (
                            "This image is the generated Master Character Reference Portrait from "
                            "character_image_url. It is the PRIMARY and ONLY visual identity reference for "
                            "story image generation. Do not treat portrait clothing, crop, or background as "
                            "story requirements."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": reference_image.data_url}},
                ]
            )

            logger.info("Analyzing character reference image for story image consistency")
            analysis_response = await self._client.chat.completions.create(
                model=kwargs.get("vision_model", "gpt-4o"),
                max_tokens=700,
                messages=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
            )

            analysis_text = analysis_response.choices[0].message.content or ""
            enhanced_prompt = (
                f"{prompt}\n\n"
                "MASTER CHARACTER REFERENCE DETAILS TO PRESERVE:\n"
                f"{analysis_text}\n\n"
                "Use the master character image as the primary face/age/body/style reference when provided. "
                "Keep that exact illustrated face, hairstyle, hairline, and age appearance across pages. "
                "Use the visual bible and scene prompt for age-appropriate body proportions, rendering style, and the "
                "one locked story outfit. No original child avatar photo is attached for story image generation. Do not "
                "copy portrait clothing, portrait crop, or portrait background. Do not redesign the child, do not "
                "enlarge the eyes, do not make the child look older or younger, and do not create a generic cartoon face. "
                "Use the Character Identity Lock inside the scene prompt as the written identity and age lock."
            )

            logger.info(f"Generating story image with {self.image_model}")
            response = await self._client.images.generate(
                model=kwargs.get("model", self.image_model),
                prompt=enhanced_prompt,
                size=kwargs.get("size", "1024x1024"),
                quality=kwargs.get("quality", "standard"),
                n=1,
            )
        except ValueError as e:
            raise AppException(str(e), code="INVALID_REFERENCE_IMAGE")
        except BadRequestError as e:
            error_msg = self._extract_error_message(e)
            logger.error(f"OpenAI rejected story image generation request: {error_msg}")
            raise AppException(f"Story image generation failed: {error_msg}", code="OPENAI_ERROR")
        except RateLimitError as e:
            logger.error(f"OpenAI rate limit exceeded: {e}")
            raise AppException(
                "OpenAI rate limit exceeded. Please retry after a few moments.",
                code="RATE_LIMIT_ERROR",
            )
        except OpenAIError as e:
            logger.error(f"OpenAI error: {e}")
            raise AppException(
                "OpenAI service error. Please verify your API key and billing.",
                code="OPENAI_ERROR",
            )

        if not response.data or not response.data[0].b64_json:
            raise AppException("OpenAI returned no image data", code="OPENAI_ERROR")

        image_bytes = base64.b64decode(response.data[0].b64_json)
        revised_prompt = getattr(response.data[0], "revised_prompt", None)

        return ImageGenerationResult(
            image_bytes=image_bytes,
            prompt_used=enhanced_prompt,
            model=kwargs.get("model", self.image_model),
            revised_prompt=revised_prompt,
            metadata={
                "provider": "openai",
                "mode": "story_reference_image",
                "size": kwargs.get("size", "1024x1024"),
                "quality": kwargs.get("quality", "standard"),
                "aspect_ratio": kwargs.get("aspect_ratio"),
                "reference_mime_type": reference_image.mime_type,
                "reference_role": "master_character_reference_portrait",
                "single_reference_used": True,
                "analysis_text": analysis_text,
                "enhanced_prompt": enhanced_prompt,
            },
        )

    async def generate_image(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate image using DALL-E (no reference photo).

        Args:
            prompt: Image generation prompt
            **kwargs: May include 'size', 'quality'

        Returns:
            ImageGenerationResult with image bytes

        Raises:
            AppException: On API errors
        """
        # Check if mock mode is enabled
        if settings.STORY_MOCK_LLM_RESPONSES:
            logger.info("MOCK MODE: Returning mock image (placeholder) instead of calling DALL-E")

            # Generate a simple placeholder PNG (1x1 white pixel)
            # In real testing, you'd replace with a proper placeholder image
            placeholder_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
                b"\x00\x01\x01\x00\x05\x18\r*\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            return ImageGenerationResult(
                image_bytes=placeholder_png,
                prompt_used=prompt,
                model=self.image_model,
                revised_prompt=None,
                metadata={"mock_mode": True, "placeholder": True},
            )

        logger.info(f"Generating image with {self.image_model}")

        try:
            response = await self._client.images.generate(
                model=self.image_model,
                prompt=prompt,
                size=kwargs.get("size", "1024x1024"),
                quality=kwargs.get("quality", "standard"),
                n=1,
            )
        except BadRequestError as e:
            error_msg = self._extract_error_message(e)
            logger.error(f"OpenAI rejected image generation request: {error_msg}")
            raise AppException(f"Image generation failed: {error_msg}", code="OPENAI_ERROR")
        except RateLimitError as e:
            logger.error(f"OpenAI rate limit exceeded: {e}")
            raise AppException(
                "OpenAI rate limit exceeded. Please retry after a few moments.",
                code="RATE_LIMIT_ERROR",
            )
        except OpenAIError as e:
            logger.error(f"OpenAI error: {e}")
            raise AppException(
                "OpenAI service error. Please verify your API key and billing.",
                code="OPENAI_ERROR",
            )

        if not response.data or not response.data[0].b64_json:
            raise AppException("OpenAI returned no image data", code="OPENAI_ERROR")

        image_bytes = base64.b64decode(response.data[0].b64_json)
        revised_prompt = getattr(response.data[0], "revised_prompt", None)

        logger.info(f"Successfully generated image using {self.image_model}")
        return ImageGenerationResult(
            image_bytes=image_bytes,
            prompt_used=prompt,
            model=self.image_model,
            revised_prompt=revised_prompt,
            metadata={
                "size": kwargs.get("size", "1024x1024"),
                "quality": kwargs.get("quality", "standard"),
            },
        )

    @staticmethod
    def _extract_error_message(exc: BadRequestError) -> str:
        """Extract user-friendly error message from OpenAI exception."""
        error_message = str(exc)
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_obj = body.get("error")
            if isinstance(error_obj, dict):
                provider_msg = error_obj.get("message")
                if isinstance(provider_msg, str) and provider_msg.strip():
                    error_message = provider_msg.strip()
        return error_message
