from abc import ABC, abstractmethod
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TextGenerationResult:
    """Result from text generation operation."""

    text: str
    prompt_used: str
    model: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ImageGenerationResult:
    """Result from image generation operation."""

    image_bytes: bytes
    prompt_used: str
    model: str
    revised_prompt: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class GeneratedImagePart:
    """One image emitted from an interleaved image generation response."""

    image_bytes: bytes
    mime_type: str | None = None
    preceding_text: str | None = None


@dataclass(frozen=True)
class MultiImageGenerationResult:
    """Result from a prompt that asks a provider to emit multiple images."""

    images: list[GeneratedImagePart]
    prompt_used: str
    model: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Base64ImageData:
    """Decoded base64 image input."""

    image_bytes: bytes
    mime_type: str
    data_url: str


def parse_base64_image_data(
    image_base64: str,
    default_mime_type: str = "image/png",
) -> Base64ImageData:
    """Parse raw base64 image data or a data URL into bytes and MIME type."""
    if not image_base64 or not image_base64.strip():
        raise ValueError("Base64 image data is required")

    value = image_base64.strip()
    mime_type = default_mime_type
    encoded = value

    if value.startswith("data:"):
        header, separator, payload = value.partition(",")
        if not separator:
            raise ValueError("Invalid base64 image data URL")
        mime_type = header.removeprefix("data:").split(";")[0] or default_mime_type
        encoded = payload

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("Invalid base64 image data") from e

    return Base64ImageData(
        image_bytes=image_bytes,
        mime_type=mime_type,
        data_url=f"data:{mime_type};base64,{encoded}",
    )


class AIProvider(ABC):
    """Abstract base class for AI/LLM providers.

    Defines the interface for AI operations that backends can implement
    for different providers (OpenAI, Google, etc.).
    """

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text from a prompt.

        Args:
            prompt: Full text prompt for the LLM
            **kwargs: Provider-specific options such as max_tokens,
                temperature, or response_format

        Returns:
            TextGenerationResult with generated text and metadata
        """
        pass

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate an image from a text prompt.

        Args:
            prompt: Image generation prompt
            **kwargs: Provider-specific options such as size, quality,
                aspect_ratio, or model

        Returns:
            ImageGenerationResult with generated image bytes and metadata
        """
        pass

    @abstractmethod
    async def create_story_image(
        self,
        prompt: str,
        reference_image_base64: str | None = None,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Create a story image using a prompt and base64 master character reference image.

        Args:
            prompt: Story image generation prompt
            reference_image_base64: Base64-encoded generated Master Character Reference Portrait.
                Can be raw base64 or a data URL. Providers may also accept
                `reference_images_base64` in kwargs for multiple named character
                references.
            **kwargs: Provider-specific options such as size, quality,
                aspect_ratio, or model

        Returns:
            ImageGenerationResult with generated image bytes and metadata
        """
        pass

    @abstractmethod
    async def create_character_from_photo(
        self,
        reference_image_path: Path | str,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Create a character image based on a reference photo.

        Analysis and text generation are handled internally and returned
        in the metadata dictionary.

        Args:
            reference_image_path: Path to the reference image file
            prompt: Text prompt describing the desired image
            **kwargs: Provider-specific options (size, quality, etc.)

        Returns:
            ImageGenerationResult with generated image bytes and metadata
        """
        pass

    @abstractmethod
    async def describe_character_image(
        self,
        image_bytes: bytes,
        prompt: str,
        mime_type: str = "image/png",
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Analyze the generated master character image and return a text description.

        Args:
            image_bytes: Generated master character image bytes
            prompt: Vision prompt describing the expected output
            mime_type: MIME type for the image payload
            **kwargs: Provider-specific options such as max_tokens,
                temperature, response_format, or vision_model

        Returns:
            TextGenerationResult with generated text and metadata
        """
        pass
