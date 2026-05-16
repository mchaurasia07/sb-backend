from app.core.config import settings
from app.service.ai.base import AIProvider
from app.service.ai.openai_provider import OpenAIProvider

_provider_instance: AIProvider | None = None


def get_ai_provider() -> AIProvider:
    """Get configured AI provider instance (singleton pattern).

    Returns:
        AIProvider: Configured provider instance

    Raises:
        RuntimeError: If no AI provider is configured
    """
    global _provider_instance

    if _provider_instance is not None:
        return _provider_instance

    if settings.OPENAI_API_KEY and settings.CHARACTER_GENERATION_ENABLED:
        _provider_instance = OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            image_model=settings.OPENAI_IMAGE_MODEL,
            text_model=settings.OPENAI_TEXT_MODEL,
        )
        return _provider_instance

    raise RuntimeError(
        "No AI provider configured. "
        "Please set OPENAI_API_KEY and CHARACTER_GENERATION_ENABLED in environment."
    )
