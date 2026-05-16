from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ImageGenerationResult:
    """Result from image generation operation."""

    image_bytes: bytes
    prompt_used: str
    model: str
    revised_prompt: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TextGenerationResult:
    """Result from text generation operation."""

    content: str
    model: str
    metadata: dict[str, Any] | None = None


class AIProvider(ABC):
    """Abstract base class for AI/LLM providers.

    Defines the interface for AI operations that backends can implement
    for different providers (OpenAI, Replicate, etc.).
    """

    @abstractmethod
    async def generate_image_from_reference(
        self,
        reference_image_path: Path | str,
        prompt: str,
        **kwargs: Any,
    ) -> ImageGenerationResult:
        """Generate image based on a reference photo.

        Args:
            reference_image_path: Path to the reference image file
            prompt: Text prompt describing the desired image
            **kwargs: Provider-specific options (size, quality, etc.)

        Returns:
            ImageGenerationResult with generated image bytes and metadata
        """
        pass

    @abstractmethod
    async def generate_text_from_image(
        self,
        image_path: Path | str,
        prompt: str,
        **kwargs: Any,
    ) -> TextGenerationResult:
        """Generate text description/analysis from an image.

        Args:
            image_path: Path to the image file to analyze
            prompt: Text prompt requesting specific analysis or description
            **kwargs: Provider-specific options

        Returns:
            TextGenerationResult with generated text and metadata
        """
        pass
