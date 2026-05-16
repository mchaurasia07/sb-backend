import base64
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, BadRequestError, OpenAIError, RateLimitError

from app.core.config import settings
from app.core.exceptions import AppException
from app.service.ai.base import AIProvider, ImageGenerationResult, TextGenerationResult

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

    async def generate_image_from_reference(
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
            # Step 1: Analyze reference image with vision API
            logger.info("Analyzing reference image for exact features...")
            b64_image = _extract_base64_image_data(path)

            analysis_prompt = """Describe this photo to create an illustrated storybook character that matches it.

Describe:
1. Hair: color, length, style
2. Face appearance: shape, distinctive features
3. Eyes: color and appearance
4. Skin tone
5. Approximate age
6. Expression and mood
7. Any unique visual characteristics
8. Overall appearance to recreate in illustration form

Focus on visual details needed for character illustration."""

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
            logger.info("Reference image analysis complete")

            # Step 2: Enhance prompt with analysis
            enhanced_prompt = f"{prompt}\n\nDETAILED REFERENCE ANALYSIS:\n{analysis_text}\n\nGenerate character matching EVERY analyzed detail exactly."

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
                "enhanced_prompt": enhanced_prompt,
            },
        )

    async def generate_text_from_image(
        self,
        image_path: Path | str,
        prompt: str,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text description from image using OpenAI GPT-4V.

        Args:
            image_path: Path to image file to analyze
            prompt: Prompt requesting specific analysis or description
            **kwargs: Additional options (currently unused)

        Returns:
            TextGenerationResult with generated description text

        Raises:
            AppException: On API errors
        """
        path = Path(image_path)
        logger.info(f"Generating text description from image: {path.name}")

        try:
            b64_image = _extract_base64_image_data(path)

            response = await self._client.chat.completions.create(
                model=self.text_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": b64_image}},
                        ],
                    }
                ],
                max_tokens=1024,
            )
        except BadRequestError as e:
            error_msg = self._extract_error_message(e)
            logger.error(f"OpenAI rejected text request: {error_msg}")
            raise AppException(f"OpenAI text generation failed: {error_msg}", code="OPENAI_ERROR")
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

        content = response.choices[0].message.content
        if not content:
            raise AppException("OpenAI returned no text content", code="OPENAI_ERROR")

        logger.info("Successfully generated text description from image")
        return TextGenerationResult(
            content=content,
            model=self.text_model,
            metadata={"max_tokens": 1024},
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
